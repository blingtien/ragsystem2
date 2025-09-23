"""
Modular Content Processors
Dedicated processors for each content type with caching and optimization
"""

import asyncio
import base64
import io
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Try to import numpy
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# Try to import optional dependencies
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

# OCR imports (conditionally)
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logging.warning("pytesseract not available, OCR functionality disabled")

# Vision model imports
try:
    from transformers import BlipProcessor, BlipForConditionalGeneration
    VISION_MODEL_AVAILABLE = True
except ImportError:
    VISION_MODEL_AVAILABLE = False
    logging.warning("transformers not available, vision model functionality disabled")

logger = logging.getLogger(__name__)

class ContentProcessor(ABC):
    """Base class for all content processors"""

    def __init__(self, cache_manager=None):
        self.cache_manager = cache_manager
        self.processing_stats = {
            'processed_count': 0,
            'cache_hits': 0,
            'processing_time_total': 0
        }

    @abstractmethod
    async def process(self, content: Dict) -> Dict:
        """Process content and return enriched data"""
        pass

    @abstractmethod
    async def generate_embedding(self, processed_content: Dict):
        """Generate embedding for processed content"""
        pass

    async def get_from_cache(self, content_hash: str, cache_key: str) -> Optional[Dict]:
        """Get processed result from cache"""
        if not self.cache_manager:
            return None

        cached = await self.cache_manager.get(f"{cache_key}:{content_hash}")
        if cached:
            self.processing_stats['cache_hits'] += 1
            logger.debug(f"Cache hit for {cache_key}:{content_hash}")
        return cached

    async def save_to_cache(self, content_hash: str, cache_key: str, data: Dict):
        """Save processed result to cache"""
        if self.cache_manager:
            await self.cache_manager.set(
                f"{cache_key}:{content_hash}",
                data,
                ttl=3600  # 1 hour TTL
            )

class ImageProcessor(ContentProcessor):
    """Process images with OCR and visual description"""

    def __init__(self, cache_manager=None, vision_model=None):
        super().__init__(cache_manager)
        self.vision_model = vision_model
        self.blip_processor = None
        self.blip_model = None

        # Initialize vision model if available
        if VISION_MODEL_AVAILABLE and not vision_model:
            try:
                self.blip_processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
                self.blip_model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
            except Exception as e:
                logger.error(f"Failed to load BLIP model: {e}")

    async def process(self, content: Dict) -> Dict:
        """Process image with OCR and visual description"""
        start_time = datetime.now()
        content_hash = content.get('hash')

        # Check cache
        cached = await self.get_from_cache(content_hash, 'image')
        if cached:
            return cached

        image_data = content['data']['content']
        description = content['data'].get('description', '')

        # Decode image
        image_bytes = base64.b64decode(image_data)

        if not PIL_AVAILABLE:
            # Return basic processing without PIL
            processed = {
                'type': 'image',
                'ocr_text': '',
                'visual_description': 'Image processing unavailable (PIL not installed)',
                'user_description': description,
                'thumbnail': '',
                'metadata': {'note': 'Install Pillow for full image processing'},
                'processed_at': datetime.now().isoformat()
            }
            await self.save_to_cache(content_hash, 'image', processed)
            return processed

        image = Image.open(io.BytesIO(image_bytes))

        # Generate thumbnail for preview
        thumbnail = await self._generate_thumbnail(image)

        # Run processing tasks in parallel
        tasks = []

        # OCR extraction
        if OCR_AVAILABLE:
            tasks.append(self._extract_ocr_text(image))
        else:
            tasks.append(asyncio.create_task(self._dummy_ocr()))

        # Visual description
        if self.blip_model or self.vision_model:
            tasks.append(self._generate_visual_description(image))
        else:
            tasks.append(asyncio.create_task(self._dummy_description()))

        # Image metadata
        tasks.append(self._extract_metadata(image))

        results = await asyncio.gather(*tasks)
        ocr_text, visual_desc, metadata = results

        processed = {
            'type': 'image',
            'ocr_text': ocr_text,
            'visual_description': visual_desc,
            'user_description': description,
            'thumbnail': thumbnail,
            'metadata': metadata,
            'processed_at': datetime.now().isoformat()
        }

        # Save to cache
        await self.save_to_cache(content_hash, 'image', processed)

        # Update stats
        self.processing_stats['processed_count'] += 1
        self.processing_stats['processing_time_total'] += (datetime.now() - start_time).total_seconds()

        return processed

    async def _extract_ocr_text(self, image) -> str:
        """Extract text using OCR"""
        try:
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')

            # Run OCR
            text = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: pytesseract.image_to_string(image, lang='chi_sim+eng')
            )
            return text.strip()
        except Exception as e:
            logger.error(f"OCR extraction failed: {e}")
            return ""

    async def _generate_visual_description(self, image) -> str:
        """Generate visual description using vision model"""
        try:
            if self.vision_model:
                # Use custom vision model
                return await self.vision_model(image)

            elif self.blip_model:
                # Use BLIP model
                inputs = self.blip_processor(image, return_tensors="pt")
                out = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.blip_model.generate(**inputs, max_length=100)
                )
                description = self.blip_processor.decode(out[0], skip_special_tokens=True)
                return description

        except Exception as e:
            logger.error(f"Visual description generation failed: {e}")
            return "Image description not available"

    async def _generate_thumbnail(self, image, size=(256, 256)) -> str:
        """Generate thumbnail for preview"""
        try:
            if not PIL_AVAILABLE:
                return ""
            thumb = image.copy()
            # Use appropriate resampling method based on PIL version
            try:
                thumb.thumbnail(size, Image.Resampling.LANCZOS)
            except AttributeError:
                # Fallback for older PIL versions
                thumb.thumbnail(size, Image.LANCZOS)
            buffer = io.BytesIO()
            thumb.save(buffer, format='JPEG', quality=85)
            return base64.b64encode(buffer.getvalue()).decode()
        except Exception as e:
            logger.error(f"Thumbnail generation failed: {e}")
            return ""

    async def _extract_metadata(self, image) -> Dict:
        """Extract image metadata"""
        return {
            'width': image.width,
            'height': image.height,
            'format': image.format,
            'mode': image.mode,
            'size_bytes': len(image.tobytes()) if hasattr(image, 'tobytes') else 0
        }

    async def _dummy_ocr(self) -> str:
        """Dummy OCR when not available"""
        return ""

    async def _dummy_description(self) -> str:
        """Dummy description when vision model not available"""
        return "Visual description not available (model not loaded)"

    async def generate_embedding(self, processed_content: Dict):
        """Generate embedding for image content"""
        # Combine all text representations
        text = f"{processed_content['ocr_text']} {processed_content['visual_description']} {processed_content['user_description']}"

        # For now, return a dummy embedding
        # In production, use CLIP or similar model
        if NUMPY_AVAILABLE:
            return np.random.randn(512).astype(np.float32)
        else:
            # Return a list of random floats if numpy not available
            import random
            return [random.random() for _ in range(512)]

class TableProcessor(ContentProcessor):
    """Process tables with structure analysis"""

    async def process(self, content: Dict) -> Dict:
        """Process table and extract insights"""
        start_time = datetime.now()
        content_hash = content.get('hash')

        # Check cache
        cached = await self.get_from_cache(content_hash, 'table')
        if cached:
            return cached

        table_data = content['data']
        headers = table_data['headers']
        rows = table_data['rows']
        description = table_data.get('description', '')

        if not PANDAS_AVAILABLE:
            # Return basic processing without pandas
            processed = {
                'type': 'table',
                'summary': f"Table with {len(rows)} rows and {len(headers)} columns",
                'statistics': {},
                'patterns': [],
                'column_types': {col: {'data_type': 'unknown', 'semantic_type': 'unknown'} for col in headers},
                'user_description': description,
                'row_count': len(rows),
                'column_count': len(headers),
                'processed_at': datetime.now().isoformat()
            }
            await self.save_to_cache(content_hash, 'table', processed)
            return processed

        # Create DataFrame for analysis
        df = pd.DataFrame(rows, columns=headers)

        # Run analysis tasks in parallel
        tasks = [
            self._generate_summary(df),
            self._extract_statistics(df),
            self._detect_patterns(df),
            self._infer_column_types(df)
        ]

        results = await asyncio.gather(*tasks)
        summary, statistics, patterns, column_types = results

        processed = {
            'type': 'table',
            'summary': summary,
            'statistics': statistics,
            'patterns': patterns,
            'column_types': column_types,
            'user_description': description,
            'row_count': len(rows),
            'column_count': len(headers),
            'processed_at': datetime.now().isoformat()
        }

        # Save to cache
        await self.save_to_cache(content_hash, 'table', processed)

        # Update stats
        self.processing_stats['processed_count'] += 1
        self.processing_stats['processing_time_total'] += (datetime.now() - start_time).total_seconds()

        return processed

    async def _generate_summary(self, df) -> str:
        """Generate table summary"""
        try:
            summary_parts = []

            # Basic info
            summary_parts.append(f"Table with {len(df)} rows and {len(df.columns)} columns")

            # Column names
            summary_parts.append(f"Columns: {', '.join(df.columns[:5])}" +
                              ("..." if len(df.columns) > 5 else ""))

            # Data types
            dtypes = df.dtypes.value_counts()
            summary_parts.append(f"Data types: {dict(dtypes)}")

            return ". ".join(summary_parts)
        except Exception as e:
            logger.error(f"Table summary generation failed: {e}")
            return "Table summary not available"

    async def _extract_statistics(self, df) -> Dict:
        """Extract statistical information"""
        try:
            stats = {}

            # Numerical columns statistics
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                stats['numeric'] = {
                    col: {
                        'mean': float(df[col].mean()),
                        'std': float(df[col].std()),
                        'min': float(df[col].min()),
                        'max': float(df[col].max())
                    } for col in numeric_cols[:10]  # Limit to first 10 columns
                }

            # Categorical columns
            categorical_cols = df.select_dtypes(include=['object']).columns
            if len(categorical_cols) > 0:
                stats['categorical'] = {
                    col: {
                        'unique_count': int(df[col].nunique()),
                        'top_values': df[col].value_counts().head(5).to_dict()
                    } for col in categorical_cols[:10]
                }

            return stats
        except Exception as e:
            logger.error(f"Statistics extraction failed: {e}")
            return {}

    async def _detect_patterns(self, df) -> List[str]:
        """Detect patterns in table data"""
        patterns = []

        try:
            # Check for missing values
            missing = df.isnull().sum()
            if missing.any():
                patterns.append(f"Missing values in columns: {missing[missing > 0].index.tolist()}")

            # Check for potential ID columns
            for col in df.columns:
                if df[col].dtype in ['int64', 'object']:
                    if df[col].nunique() == len(df):
                        patterns.append(f"'{col}' appears to be an ID column")

            # Check for date columns
            for col in df.columns:
                if df[col].dtype == 'object':
                    try:
                        pd.to_datetime(df[col])
                        patterns.append(f"'{col}' appears to contain dates")
                    except:
                        pass

        except Exception as e:
            logger.error(f"Pattern detection failed: {e}")

        return patterns

    async def _infer_column_types(self, df) -> Dict:
        """Infer semantic column types"""
        column_types = {}

        for col in df.columns:
            dtype = str(df[col].dtype)
            semantic_type = "unknown"

            # Infer semantic type
            col_lower = col.lower()
            if any(term in col_lower for term in ['id', 'key', 'identifier']):
                semantic_type = "identifier"
            elif any(term in col_lower for term in ['date', 'time', 'timestamp']):
                semantic_type = "temporal"
            elif any(term in col_lower for term in ['price', 'cost', 'amount', 'value']):
                semantic_type = "monetary"
            elif any(term in col_lower for term in ['count', 'quantity', 'number']):
                semantic_type = "quantity"
            elif any(term in col_lower for term in ['name', 'title', 'description']):
                semantic_type = "text"
            elif dtype in ['int64', 'float64']:
                semantic_type = "numeric"
            elif dtype == 'object':
                semantic_type = "categorical"

            column_types[col] = {
                'data_type': dtype,
                'semantic_type': semantic_type
            }

        return column_types

    async def generate_embedding(self, processed_content: Dict):
        """Generate embedding for table content"""
        # Create text representation of table
        text_parts = [
            processed_content['summary'],
            json.dumps(processed_content['statistics']),
            ' '.join(processed_content['patterns'])
        ]
        text = ' '.join(text_parts)

        # For now, return a dummy embedding
        # In production, use appropriate embedding model
        if NUMPY_AVAILABLE:
            return np.random.randn(768).astype(np.float32)
        else:
            import random
            return [random.random() for _ in range(768)]

class EquationProcessor(ContentProcessor):
    """Process mathematical equations"""

    async def process(self, content: Dict) -> Dict:
        """Process equation and extract mathematical concepts"""
        start_time = datetime.now()
        content_hash = content.get('hash')

        # Check cache
        cached = await self.get_from_cache(content_hash, 'equation')
        if cached:
            return cached

        equation_data = content['data']
        # Handle both 'latex' and 'content' fields
        latex = equation_data.get('latex') or equation_data.get('content', '')
        description = equation_data.get('description', '')

        # Process equation
        interpretation = await self._interpret_equation(latex)
        variables = await self._extract_variables(latex)
        complexity = await self._assess_complexity(latex)
        domain = await self._identify_domain(latex)

        processed = {
            'type': 'equation',
            'latex': latex,
            'interpretation': interpretation,
            'variables': variables,
            'complexity': complexity,
            'domain': domain,
            'user_description': description,
            'processed_at': datetime.now().isoformat()
        }

        # Save to cache
        await self.save_to_cache(content_hash, 'equation', processed)

        # Update stats
        self.processing_stats['processed_count'] += 1
        self.processing_stats['processing_time_total'] += (datetime.now() - start_time).total_seconds()

        return processed

    async def _interpret_equation(self, latex: str) -> str:
        """Interpret the mathematical equation"""
        # Basic interpretation based on common patterns
        interpretations = []

        if 'frac' in latex:
            interpretations.append("Contains fractions")
        if 'int' in latex:
            interpretations.append("Contains integrals")
        if 'sum' in latex:
            interpretations.append("Contains summation")
        if 'partial' in latex:
            interpretations.append("Contains partial derivatives")
        if 'sqrt' in latex:
            interpretations.append("Contains square roots")
        if 'matrix' in latex or 'bmatrix' in latex:
            interpretations.append("Contains matrices")

        return "; ".join(interpretations) if interpretations else "Mathematical expression"

    async def _extract_variables(self, latex: str) -> List[str]:
        """Extract variables from equation"""
        import re

        # Common variable patterns
        variables = set()

        # Single letters (common variables)
        single_vars = re.findall(r'\b([a-zA-Z])\b', latex)
        variables.update(single_vars)

        # Greek letters
        greek_vars = re.findall(r'\\(alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|phi|psi|omega)', latex)
        variables.update(greek_vars)

        # Subscripted variables
        subscript_vars = re.findall(r'([a-zA-Z])_\{?([a-zA-Z0-9]+)\}?', latex)
        for var, sub in subscript_vars:
            variables.add(f"{var}_{sub}")

        return list(variables)

    async def _assess_complexity(self, latex: str) -> str:
        """Assess equation complexity"""
        complexity_score = 0

        # Count operations
        operations = {
            'basic': ['+', '-', '*', '/'],
            'intermediate': ['frac', 'sqrt', 'power'],
            'advanced': ['int', 'partial', 'sum', 'prod', 'lim']
        }

        for op in operations['basic']:
            complexity_score += latex.count(op) * 1

        for op in operations['intermediate']:
            complexity_score += latex.count(op) * 2

        for op in operations['advanced']:
            complexity_score += latex.count(op) * 3

        if complexity_score < 5:
            return "simple"
        elif complexity_score < 15:
            return "intermediate"
        else:
            return "complex"

    async def _identify_domain(self, latex: str) -> str:
        """Identify mathematical domain"""
        domains = []

        # Check for specific patterns
        if any(term in latex for term in ['int', 'dx', 'dy', 'dz']):
            domains.append("calculus")
        if any(term in latex for term in ['matrix', 'bmatrix', 'det', 'eigenvalue']):
            domains.append("linear algebra")
        if any(term in latex for term in ['P(', 'E[', 'Var', 'sigma', 'mu']):
            domains.append("probability/statistics")
        if any(term in latex for term in ['sin', 'cos', 'tan', 'theta', 'pi']):
            domains.append("trigonometry")
        if any(term in latex for term in ['lim', 'infty', 'epsilon', 'delta']):
            domains.append("analysis")

        return ", ".join(domains) if domains else "general mathematics"

    async def generate_embedding(self, processed_content: Dict):
        """Generate embedding for equation content"""
        # Create text representation
        text = f"{processed_content['latex']} {processed_content['interpretation']} {processed_content['domain']}"

        # For now, return a dummy embedding
        # In production, use mathematical embedding model
        if NUMPY_AVAILABLE:
            return np.random.randn(512).astype(np.float32)
        else:
            import random
            return [random.random() for _ in range(512)]

class ProcessorFactory:
    """Factory for creating content processors"""

    def __init__(self, cache_manager=None, vision_model=None):
        self.cache_manager = cache_manager
        self.vision_model = vision_model
        self._processors = {}

    def get_processor(self, content_type: str) -> ContentProcessor:
        """Get or create processor for content type"""
        if content_type not in self._processors:
            if content_type == 'image':
                self._processors[content_type] = ImageProcessor(
                    self.cache_manager,
                    self.vision_model
                )
            elif content_type == 'table':
                self._processors[content_type] = TableProcessor(
                    self.cache_manager
                )
            elif content_type == 'equation':
                self._processors[content_type] = EquationProcessor(
                    self.cache_manager
                )
            else:
                raise ValueError(f"Unknown content type: {content_type}")

        return self._processors[content_type]

    async def process_batch(self, items: List[Dict]) -> List[Dict]:
        """Process multiple items in parallel"""
        tasks = []

        for item in items:
            processor = self.get_processor(item['type'])
            tasks.append(processor.process(item))

        return await asyncio.gather(*tasks)

    def get_statistics(self) -> Dict:
        """Get processing statistics from all processors"""
        stats = {}
        for content_type, processor in self._processors.items():
            stats[content_type] = processor.processing_stats
        return stats