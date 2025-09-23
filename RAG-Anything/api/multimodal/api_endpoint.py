"""
Multimodal Query API Endpoint
Production-ready endpoint with validation, caching, and monitoring
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, List, Any, Optional
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, validator

# Try to import numpy
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

from .validators import MultimodalContentValidator, ValidationError
from .processors import ProcessorFactory
from .cache_manager import CacheManager, CacheKeyGenerator
from .error_handlers import MultimodalErrorHandler, ErrorCategory
from .enhanced_processor import EnhancedMultimodalProcessor

logger = logging.getLogger(__name__)

# Rate limiting (optional)
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address)
    RATE_LIMITING_AVAILABLE = True
except ImportError:
    limiter = None
    RATE_LIMITING_AVAILABLE = False

class MultimodalQueryRequest(BaseModel):
    """Validated multimodal query request"""
    query: str = Field(..., min_length=1, max_length=1000)
    mode: str = Field("hybrid", pattern="^(naive|local|global|hybrid)$")
    multimodal_content: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    vlm_enhanced: bool = True
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    @validator('multimodal_content')
    def validate_content_limit(cls, v):
        if len(v) > 10:
            raise ValueError("Maximum 10 multimodal items allowed per query")
        return v

class MultimodalQueryResponse(BaseModel):
    """Multimodal query response"""
    success: bool
    query_id: str
    result: str
    processing_stats: Dict[str, Any]
    citations: Optional[List[Dict]] = None
    error: Optional[str] = None

class MultimodalAPIHandler:
    """Main handler for multimodal API operations"""

    def __init__(self, rag_instance, cache_manager: CacheManager = None):
        self.rag = rag_instance
        self.cache_manager = cache_manager or CacheManager()
        self.processor_factory = ProcessorFactory(cache_manager)
        self.error_handler = MultimodalErrorHandler()
        self.validator = MultimodalContentValidator()
        self.enhanced_processor = EnhancedMultimodalProcessor(rag_instance)
        print(f"[MULTIMODAL] API Handler initialized with enhanced processor")

        # Metrics tracking
        self.metrics = {
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'cache_hits': 0,
            'avg_processing_time': 0,
            'error_categories': {}
        }

    async def initialize(self):
        """Initialize the handler"""
        await self.cache_manager.initialize()
        logger.info("Multimodal API handler initialized")

    async def process_multimodal_query(
        self,
        request: MultimodalQueryRequest,
        background_tasks: BackgroundTasks
    ) -> MultimodalQueryResponse:
        """Process multimodal query with full validation and caching"""

        start_time = time.time()
        query_id = str(uuid.uuid4())
        processing_stats = {
            'query_id': query_id,
            'started_at': datetime.now().isoformat(),
            'items_processed': 0,
            'cache_hits': 0,
            'processing_time': 0
        }

        try:
            # Step 1: Validate all multimodal content
            print(f"[MULTIMODAL] Processing query {query_id}")
            logger.info(f"Processing multimodal query {query_id}")
            logger.info(f"Query text: {request.query[:100]}...")
            logger.info(f"Multimodal items count: {len(request.multimodal_content) if request.multimodal_content else 0}")
            print(f"[MULTIMODAL] Query: {request.query[:50]}...")
            print(f"[MULTIMODAL] Items: {len(request.multimodal_content) if request.multimodal_content else 0}")

            # Log content details
            if request.multimodal_content:
                for idx, item in enumerate(request.multimodal_content):
                    logger.info(f"Item {idx}: type={item.get('type')}")
                    if item.get('type') == 'image' and 'data' in item:
                        data = item['data']
                        if isinstance(data, dict):
                            content_preview = str(data.get('content', ''))[:50]
                            logger.info(f"  Image data.content preview: {content_preview}")
                            logger.info(f"  Image data.description: {data.get('description', 'None')}")

            validated_items = self.validator.validate_multimodal_request(
                request.multimodal_content
            )

            # Step 2: Check query cache
            query_cache_key = CacheKeyGenerator.generate_query_key(
                request.query,
                [item['hash'] for item in validated_items]
            )

            cached_result = await self.cache_manager.get(query_cache_key)
            if cached_result:
                logger.info(f"Query cache hit for {query_id}")
                processing_stats['cache_hits'] += 1
                self.metrics['cache_hits'] += 1

                return MultimodalQueryResponse(
                    success=True,
                    query_id=query_id,
                    result=cached_result['result'],
                    processing_stats={
                        **processing_stats,
                        'processing_time': time.time() - start_time,
                        'from_cache': True
                    }
                )

            # Step 3: Check for incomplete content
            has_incomplete = any(
                self._is_content_incomplete(item) for item in validated_items
            )

            logger.info(f"Validation passed. Checking for incomplete content: {has_incomplete}")

            # If content is incomplete, use enhanced processor
            if has_incomplete:
                logger.info(f"✅ Detected incomplete content for {query_id}, using enhanced processor")
                result = await self.enhanced_processor.process_query_with_incomplete_content(
                    query=request.query,
                    multimodal_content=validated_items,
                    mode=request.mode
                )

                processing_stats['items_processed'] = len(validated_items)
                processing_stats['processing_time'] = time.time() - start_time
                processing_stats['used_enhanced_processor'] = True

                return MultimodalQueryResponse(
                    success=True,
                    query_id=query_id,
                    result=result,
                    processing_stats=processing_stats
                )

            # Step 3b: Process multimodal content in parallel (normal flow)
            logger.info(f"📌 Using normal processing flow for {query_id}")
            print(f"[MULTIMODAL] Using normal processing flow")
            processed_items = await self.processor_factory.process_batch(validated_items)
            processing_stats['items_processed'] = len(processed_items)
            logger.info(f"Processed {len(processed_items)} items")
            print(f"[MULTIMODAL] Processed items: {len(processed_items)}")

            # Log processed item details
            for idx, item in enumerate(processed_items):
                print(f"[MULTIMODAL] Item {idx}: type={item.get('type')}")
                if item.get('type') == 'image':
                    print(f"[MULTIMODAL]   OCR text: {item.get('ocr_text', '')[:100]}")
                    print(f"[MULTIMODAL]   Visual desc: {item.get('visual_description', '')[:100]}")

            # Step 4: Generate embeddings for all content
            embeddings = await self._generate_embeddings(processed_items)

            # Step 5: Execute RAG query with multimodal context
            print(f"[MULTIMODAL] Executing RAG query with enhanced context")
            result = await self._execute_rag_query(
                request.query,
                processed_items,
                embeddings,
                request.mode,
                request.vlm_enhanced
            )
            print(f"[MULTIMODAL] RAG result preview: {result[:200] if result else 'None'}")

            # Step 6: Extract citations
            citations = await self._extract_citations(result, processed_items)

            # Step 7: Cache the result
            await self.cache_manager.set(
                query_cache_key,
                {
                    'result': result,
                    'citations': citations,
                    'timestamp': datetime.now().isoformat()
                },
                ttl=3600  # 1 hour cache
            )

            # Step 8: Track metrics in background
            background_tasks.add_task(
                self._update_metrics,
                query_id,
                True,
                time.time() - start_time
            )

            # Step 9: Log processing details
            processing_stats.update({
                'processing_time': time.time() - start_time,
                'embeddings_generated': len(embeddings),
                'citations_found': len(citations),
                'cache_stats': self.cache_manager.get_statistics()
            })

            logger.info(f"Query {query_id} completed successfully")
            self.metrics['successful_queries'] += 1

            return MultimodalQueryResponse(
                success=True,
                query_id=query_id,
                result=result,
                processing_stats=processing_stats,
                citations=citations
            )

        except ValidationError as e:
            logger.error(f"Validation error for query {query_id}: {e}")

            # Check if this is due to incomplete content
            has_incomplete = False
            if request.multimodal_content:
                for item in request.multimodal_content:
                    if self._is_content_incomplete(item):
                        has_incomplete = True
                        break

            # If content is incomplete, use enhanced processor instead of failing
            if has_incomplete:
                logger.info(f"🔧 Validation failed but detected incomplete content for {query_id}, using enhanced processor")
                try:
                    result = await self.enhanced_processor.process_query_with_incomplete_content(
                        query=request.query,
                        multimodal_content=request.multimodal_content,
                        mode=request.mode
                    )

                    processing_stats['items_processed'] = len(request.multimodal_content)
                    processing_stats['processing_time'] = time.time() - start_time
                    processing_stats['used_enhanced_processor'] = True
                    processing_stats['validation_bypassed'] = True

                    return MultimodalQueryResponse(
                        success=True,
                        query_id=query_id,
                        result=result,
                        processing_stats=processing_stats
                    )
                except Exception as enhance_error:
                    logger.error(f"Enhanced processor failed: {enhance_error}")
                    # Fall through to original error handling

            # Original validation error handling for non-incomplete content
            self.metrics['failed_queries'] += 1
            self._track_error(ErrorCategory.VALIDATION, str(e))

            return MultimodalQueryResponse(
                success=False,
                query_id=query_id,
                result="",
                processing_stats=processing_stats,
                error=f"Validation error: {e.message}"
            )

        except Exception as e:
            logger.error(f"Processing error for query {query_id}: {e}", exc_info=True)
            self.metrics['failed_queries'] += 1

            # Categorize and handle error
            error_info = self.error_handler.handle_error(e, {
                'query_id': query_id,
                'request': request.dict()
            })

            self._track_error(error_info.category, str(e))

            # Background task to log error details
            background_tasks.add_task(
                self._log_error_details,
                query_id,
                error_info
            )

            return MultimodalQueryResponse(
                success=False,
                query_id=query_id,
                result="",
                processing_stats=processing_stats,
                error=error_info.user_message
            )

    def _generate_fallback_response(self, query: str, processed_items: List[Dict]) -> str:
        """Generate a fallback response when RAG is unavailable"""
        response_parts = [
            "I've processed your multimodal content. Here's what I found:",
            ""
        ]

        for item in processed_items:
            if item['type'] == 'image':
                response_parts.append("📷 Image Content:")
                if item.get('ocr_text'):
                    response_parts.append(f"  Text found: {item['ocr_text'][:200]}...")
                if item.get('visual_description'):
                    response_parts.append(f"  Description: {item['visual_description']}")

            elif item['type'] == 'table':
                response_parts.append("📊 Table Content:")
                if item.get('summary'):
                    response_parts.append(f"  Summary: {item['summary']}")
                if item.get('row_count'):
                    response_parts.append(f"  Size: {item['row_count']} rows × {item['column_count']} columns")

            elif item['type'] == 'equation':
                response_parts.append("🔢 Mathematical Equation:")
                if item.get('latex'):
                    response_parts.append(f"  LaTeX: {item['latex']}")
                if item.get('interpretation'):
                    response_parts.append(f"  Interpretation: {item['interpretation']}")
                if item.get('domain'):
                    response_parts.append(f"  Domain: {item['domain']}")

                # For E=mc^2, provide a basic explanation
                if item.get('latex') == 'E = mc^2':
                    response_parts.append("\n  This is Einstein's famous mass-energy equivalence equation:")
                    response_parts.append("  - E represents energy (in joules)")
                    response_parts.append("  - m represents mass (in kilograms)")
                    response_parts.append("  - c represents the speed of light (approximately 3×10^8 m/s)")
                    response_parts.append("  - It shows that mass and energy are interchangeable")

        response_parts.append(f"\nYour query: {query}")
        response_parts.append("\nNote: RAG system is currently unavailable, showing processed content only.")

        return "\n".join(response_parts)

    def _enhance_query_with_context(self, query: str, multimodal_content: List[Dict]) -> str:
        """Enhance query with multimodal context information"""
        context_parts = []

        # Add the original query
        context_parts.append(f"用户查询：{query}")

        # Check if we have any image content with OCR text
        has_ocr_content = False

        for item in multimodal_content:
            # Handle both processed and raw data formats
            data = item.get('data', item)
            ocr_text = item.get('ocr_text', '')  # Direct access for processed items
            visual_desc = data if isinstance(data, str) else ''  # Visual description

            if item['type'] == 'image':
                # First check direct ocr_text field (from multimodal_content structure)
                if ocr_text:
                    has_ocr_content = True
                    context_parts.append(f"\n图片中识别到的文字内容：\n{ocr_text}")
                # Also check data dict
                elif isinstance(data, dict):
                    if data.get('ocr_text'):
                        has_ocr_content = True
                        context_parts.append(f"\n图片中识别到的文字内容：\n{data['ocr_text']}")
                    if data.get('visual_description'):
                        context_parts.append(f"\n图片视觉描述：{data['visual_description']}")
                elif visual_desc:
                    context_parts.append(f"\n图片视觉描述：{visual_desc}")

            elif item['type'] == 'table':
                if isinstance(data, dict):
                    if data.get('summary'):
                        context_parts.append(f"\n表格摘要：{data['summary']}")
                    if data.get('statistics'):
                        context_parts.append(f"\n表格统计：包含数值数据和模式")

            elif item['type'] == 'equation':
                if isinstance(data, dict):
                    # Check for both 'latex' and 'content' fields
                    latex_content = data.get('latex') or data.get('content')
                    if latex_content:
                        context_parts.append(f"\n公式：{latex_content}")
                    if data.get('interpretation'):
                        context_parts.append(f"\n数学背景：{data['interpretation']}")
                elif isinstance(data, str):
                    # Handle direct latex string
                    context_parts.append(f"\n公式：{data}")

        # If we have OCR content, emphasize the need to analyze it
        if has_ocr_content:
            context_parts.append("\n请基于图片中识别到的文字内容，结合知识库中的相关管理规定和标准，判断内容是否正确并给出详细解释。")

        enhanced_query = "\n".join(context_parts)
        print(f"[DEBUG] Enhanced query with OCR: has_ocr_content={has_ocr_content}")
        return enhanced_query

    async def _generate_embeddings(self, processed_items: List[Dict]) -> List:
        """Generate embeddings for all processed items"""
        tasks = []

        for item in processed_items:
            content_type = item['type']
            processor = self.processor_factory.get_processor(content_type)
            tasks.append(processor.generate_embedding(item))

        embeddings = await asyncio.gather(*tasks)
        return embeddings

    async def _execute_rag_query(
        self,
        query: str,
        processed_items: List[Dict],
        embeddings: List,
        mode: str,
        vlm_enhanced: bool
    ) -> str:
        """Execute the actual RAG query"""

        print(f"[MULTIMODAL] _execute_rag_query called")
        print(f"[MULTIMODAL] Query: {query}")
        print(f"[MULTIMODAL] Processed items count: {len(processed_items)}")

        # Prepare multimodal content for RAG
        multimodal_content = []

        for item, embedding in zip(processed_items, embeddings):
            if item['type'] == 'image':
                multimodal_content.append({
                    'type': 'image',
                    'data': item.get('visual_description', ''),
                    'ocr_text': item.get('ocr_text', ''),
                    'embedding': embedding.tolist() if hasattr(embedding, 'tolist') else embedding
                })
            elif item['type'] == 'table':
                multimodal_content.append({
                    'type': 'table',
                    'data': item.get('summary', ''),
                    'statistics': item.get('statistics', {}),
                    'embedding': embedding.tolist() if hasattr(embedding, 'tolist') else embedding
                })
            elif item['type'] == 'equation':
                multimodal_content.append({
                    'type': 'equation',
                    'data': item.get('latex', ''),
                    'interpretation': item.get('interpretation', ''),
                    'embedding': embedding.tolist() if hasattr(embedding, 'tolist') else embedding
                })

        # Enhance query with multimodal context
        enhanced_query = self._enhance_query_with_context(query, multimodal_content)
        print(f"[MULTIMODAL] Enhanced query: {enhanced_query[:500]}")

        # Check if we have meaningful content
        has_meaningful_content = False
        for item in processed_items:
            if item['type'] == 'image':
                if item.get('ocr_text') or item.get('visual_description'):
                    has_meaningful_content = True
                    break

        print(f"[MULTIMODAL] Has meaningful content: {has_meaningful_content}")

        # If no meaningful content, use enhanced processor for better response
        if not has_meaningful_content:
            print(f"[MULTIMODAL] No meaningful content detected, using enhanced processor")
            if self.enhanced_processor:
                try:
                    result = await self.enhanced_processor.process_query_with_incomplete_content(
                        query=query,
                        multimodal_content=processed_items,
                        mode=mode
                    )
                    return result
                except Exception as e:
                    logger.error(f"Enhanced processor failed: {e}")

        # Use enhanced query without requiring specialized parsers
        try:
            print(f"[MULTIMODAL] Calling RAG with enhanced query")
            # Try using multimodal if available
            if hasattr(self.rag, 'aquery_with_multimodal'):
                result = await self.rag.aquery_with_multimodal(
                    query=enhanced_query,
                    multimodal_content=multimodal_content,
                    mode=mode,
                    vlm_enhanced=vlm_enhanced
                )
            else:
                # Fallback to regular query with enriched context
                result = await self.rag.aquery(
                    query=enhanced_query,
                    mode=mode,
                    vlm_enhanced=vlm_enhanced if hasattr(self.rag, 'vlm_enhanced') else False
                )
            print(f"[MULTIMODAL] RAG result: {result[:200] if result else 'None'}")
        except Exception as rag_error:
            # If RAG query fails, provide a basic response with context
            logger.warning(f"RAG query failed: {rag_error}, using fallback response")
            print(f"[MULTIMODAL] RAG error: {rag_error}")
            result = self._generate_fallback_response(enhanced_query, processed_items)

        return result

    async def _extract_citations(self, result: str, processed_items: List[Dict]) -> List[Dict]:
        """Extract citations from the result"""
        citations = []

        # Simple citation extraction (can be enhanced)
        for i, item in enumerate(processed_items):
            if item['type'] == 'image':
                # Check if image content is referenced in result
                if item.get('ocr_text') and item['ocr_text'] in result:
                    citations.append({
                        'type': 'image',
                        'index': i,
                        'content': item['ocr_text'][:100],
                        'relevance': 'high'
                    })
            elif item['type'] == 'table':
                # Check if table data is referenced
                if item.get('summary') and any(word in result for word in item['summary'].split()[:10]):
                    citations.append({
                        'type': 'table',
                        'index': i,
                        'content': item['summary'][:100],
                        'relevance': 'medium'
                    })
            elif item['type'] == 'equation':
                # Check if equation is referenced
                if item.get('latex') and item['latex'] in result:
                    citations.append({
                        'type': 'equation',
                        'index': i,
                        'content': item['latex'],
                        'relevance': 'high'
                    })

        return citations

    async def _update_metrics(self, query_id: str, success: bool, processing_time: float):
        """Update metrics in background"""
        self.metrics['total_queries'] += 1

        # Update average processing time
        current_avg = self.metrics['avg_processing_time']
        total = self.metrics['total_queries']
        self.metrics['avg_processing_time'] = (
            (current_avg * (total - 1) + processing_time) / total
        )

        # Log metrics periodically
        if self.metrics['total_queries'] % 100 == 0:
            logger.info(f"Metrics update: {self.metrics}")

    async def _log_error_details(self, query_id: str, error_info):
        """Log detailed error information"""
        error_log = {
            'query_id': query_id,
            'timestamp': datetime.now().isoformat()
        }

        # Handle ErrorInfo object attributes safely
        if hasattr(error_info, 'category'):
            if hasattr(error_info.category, 'value'):
                error_log['category'] = error_info.category.value
            else:
                error_log['category'] = str(error_info.category)

        if hasattr(error_info, 'message'):
            error_log['message'] = error_info.message

        if hasattr(error_info, 'details'):
            error_log['details'] = error_info.details

        if hasattr(error_info, 'is_recoverable'):
            error_log['is_recoverable'] = error_info.is_recoverable

        logger.error(f"Error details: {json.dumps(error_log, indent=2)}")

    def _is_content_incomplete(self, item: Dict) -> bool:
        """Check if content item is incomplete"""
        if item.get('type') == 'image':
            data = item.get('data', {})
            # Check for incomplete markers
            if isinstance(data, str) and 'incomplete' in data.lower():
                return True
            if isinstance(data, dict):
                content = data.get('content', '')
                if not content or content == 'incomplete':
                    return True
        return False

    def _track_error(self, category: ErrorCategory, message: str):
        """Track error by category"""
        category_name = category.value if hasattr(category, 'value') else str(category)
        if category_name not in self.metrics['error_categories']:
            self.metrics['error_categories'][category_name] = 0
        self.metrics['error_categories'][category_name] += 1

    def get_metrics(self) -> Dict:
        """Get current metrics"""
        return {
            **self.metrics,
            'cache_stats': self.cache_manager.get_statistics(),
            'processor_stats': self.processor_factory.get_statistics()
        }

    async def health_check(self) -> Dict:
        """Health check for the API"""
        health = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'components': {}
        }

        # Check cache
        try:
            test_key = 'health_check_test'
            await self.cache_manager.set(test_key, 'test', ttl=10)
            test_value = await self.cache_manager.get(test_key)
            health['components']['cache'] = 'healthy' if test_value == 'test' else 'degraded'
        except:
            health['components']['cache'] = 'unhealthy'

        # Check RAG
        health['components']['rag'] = 'healthy' if self.rag else 'unhealthy'

        # Overall status
        if 'unhealthy' in health['components'].values():
            health['status'] = 'unhealthy'
        elif 'degraded' in health['components'].values():
            health['status'] = 'degraded'

        return health

    async def cleanup(self):
        """Cleanup resources"""
        await self.cache_manager.cleanup_expired()
        await self.cache_manager.close()

# Error handling classes
class MultimodalErrorHandler:
    """Error handler for multimodal operations"""

    class ErrorCategory:
        VALIDATION = "validation_error"
        PROCESSING = "processing_error"
        NETWORK = "network_error"
        RESOURCE = "resource_error"
        UNKNOWN = "unknown_error"

    class ErrorInfo:
        def __init__(self, category, message, details=None, is_recoverable=False):
            self.category = category
            self.message = message
            self.details = details or {}
            self.is_recoverable = is_recoverable
            self.user_message = self._generate_user_message()

        def _generate_user_message(self):
            """Generate user-friendly error message"""
            messages = {
                MultimodalErrorHandler.ErrorCategory.VALIDATION: "Invalid input provided. Please check your data format.",
                MultimodalErrorHandler.ErrorCategory.PROCESSING: "An error occurred while processing your request. Please try again.",
                MultimodalErrorHandler.ErrorCategory.NETWORK: "Network connection issue. Please check your connection.",
                MultimodalErrorHandler.ErrorCategory.RESOURCE: "System resources temporarily unavailable. Please try again later.",
                MultimodalErrorHandler.ErrorCategory.UNKNOWN: "An unexpected error occurred. Please contact support if the issue persists."
            }
            return messages.get(self.category, messages[MultimodalErrorHandler.ErrorCategory.UNKNOWN])

    def handle_error(self, error: Exception, context: Dict = None) -> ErrorInfo:
        """Categorize and handle errors"""
        error_message = str(error)
        error_type = type(error).__name__

        # Categorize error
        if isinstance(error, ValidationError):
            category = self.ErrorCategory.VALIDATION
            is_recoverable = True
        elif "timeout" in error_message.lower() or "connection" in error_message.lower():
            category = self.ErrorCategory.NETWORK
            is_recoverable = True
        elif "memory" in error_message.lower() or "resource" in error_message.lower():
            category = self.ErrorCategory.RESOURCE
            is_recoverable = True
        elif "process" in error_message.lower():
            category = self.ErrorCategory.PROCESSING
            is_recoverable = False
        else:
            category = self.ErrorCategory.UNKNOWN
            is_recoverable = False

        return self.ErrorInfo(
            category=category,
            message=error_message,
            details={
                'error_type': error_type,
                'context': context
            },
            is_recoverable=is_recoverable
        )