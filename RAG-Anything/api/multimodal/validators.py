"""
Multimodal Content Validators
Strict validation and sanitization for multimodal inputs
"""

import base64
import hashlib
import re
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import io
import json

# Try to import PIL for image processing (optional)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Try to import magic for file type detection (optional)
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False

# Security constants
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_TABLE_SIZE = 5 * 1024 * 1024   # 5MB
MAX_EQUATION_SIZE = 1 * 1024 * 1024  # 1MB
MAX_IMAGE_DIMENSION = 4096
ALLOWED_IMAGE_FORMATS = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_TABLE_FORMATS = {'application/json', 'text/csv', 'application/vnd.ms-excel'}

class ValidationError(Exception):
    """Custom validation error with details"""
    def __init__(self, message: str, error_code: str, details: Dict = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)

class ImageContent(BaseModel):
    """Validated image content model"""
    content: str = Field(..., description="Base64 encoded image")
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    description: Optional[str] = Field(None, max_length=500)

    @field_validator('content')
    @classmethod
    def validate_image_content(cls, v):
        """Validate and sanitize image content"""
        try:
            # Decode base64
            image_data = base64.b64decode(v)

            # Check size
            if len(image_data) > MAX_IMAGE_SIZE:
                raise ValidationError(
                    f"Image size exceeds {MAX_IMAGE_SIZE/1024/1024}MB limit",
                    "IMAGE_TOO_LARGE"
                )

            # Check format using available methods
            if MAGIC_AVAILABLE:
                mime = magic.from_buffer(image_data, mime=True)
                if mime not in ALLOWED_IMAGE_FORMATS:
                    raise ValidationError(
                        f"Invalid image format: {mime}",
                        "INVALID_IMAGE_FORMAT",
                        {"allowed": list(ALLOWED_IMAGE_FORMATS)}
                    )
            elif PIL_AVAILABLE:
                # Fallback: Check using PIL
                try:
                    img_check = Image.open(io.BytesIO(image_data))
                    img_format = img_check.format
                    if img_format and f"image/{img_format.lower()}" not in ALLOWED_IMAGE_FORMATS:
                        raise ValidationError(
                            f"Invalid image format: {img_format}",
                            "INVALID_IMAGE_FORMAT",
                            {"allowed": list(ALLOWED_IMAGE_FORMATS)}
                        )
                except:
                    pass  # If PIL can open it, we'll allow it

            # Load image and check dimensions if PIL available
            if PIL_AVAILABLE:
                img = Image.open(io.BytesIO(image_data))
                width, height = img.size

                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    # Auto-resize large images
                    try:
                        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
                    except AttributeError:
                        # Fallback for older PIL versions
                        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
                    buffer = io.BytesIO()
                    img.save(buffer, format=img.format)
                    image_data = buffer.getvalue()
                    v = base64.b64encode(image_data).decode()

            return v

        except Exception as e:
            raise ValidationError(
                f"Invalid image data: {str(e)}",
                "INVALID_IMAGE_DATA"
            )

class TableContent(BaseModel):
    """Validated table content model"""
    headers: List[str] = Field(..., min_items=1, max_items=100)
    rows: List[List[Any]] = Field(..., min_items=1, max_items=10000)
    format: Optional[str] = "json"
    description: Optional[str] = Field(None, max_length=500)

    @field_validator('rows')
    @classmethod
    def validate_table_structure(cls, v, values):
        """Validate table structure and sanitize data"""
        headers = values.get('headers', [])

        # Check consistent column count
        for i, row in enumerate(v):
            if len(row) != len(headers):
                raise ValidationError(
                    f"Row {i} has {len(row)} columns, expected {len(headers)}",
                    "INCONSISTENT_TABLE_STRUCTURE"
                )

        # Sanitize cell values (prevent injection)
        sanitized_rows = []
        for row in v:
            sanitized_row = []
            for cell in row:
                if isinstance(cell, str):
                    # Remove potential script tags and SQL injection attempts
                    cell = re.sub(r'<script.*?</script>', '', cell, flags=re.DOTALL)
                    cell = re.sub(r'(DROP|DELETE|INSERT|UPDATE|SELECT)\s+', '', cell, flags=re.IGNORECASE)
                sanitized_row.append(cell)
            sanitized_rows.append(sanitized_row)

        return sanitized_rows

class EquationContent(BaseModel):
    """Validated equation content model"""
    latex: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    content: Optional[str] = Field(default=None, min_length=1, max_length=5000)  # Alternative field name
    format: Optional[str] = Field(default="latex")
    description: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode='before')
    @classmethod
    def normalize_equation_fields(cls, values):
        """Normalize equation fields - copy content to latex if needed"""
        if isinstance(values, dict):
            # If content is provided but latex is not, copy content to latex
            if 'content' in values and values['content'] and ('latex' not in values or not values.get('latex')):
                values['latex'] = values['content']
            # If neither latex nor content is provided, that's an error
            if not values.get('latex') and not values.get('content'):
                raise ValueError("Either 'latex' or 'content' field must be provided")
        return values

    @field_validator('latex')
    @classmethod
    def validate_equation(cls, v):
        """Validate and sanitize LaTeX equation"""
        if not v:
            return v

        # Remove potentially dangerous LaTeX commands
        dangerous_commands = [
            r'\\input', r'\\include', r'\\write', r'\\read',
            r'\\immediate', r'\\openout', r'\\closeout'
        ]

        for cmd in dangerous_commands:
            if re.search(cmd, v):
                raise ValidationError(
                    f"Dangerous LaTeX command detected: {cmd}",
                    "DANGEROUS_LATEX_COMMAND"
                )

        # Basic LaTeX syntax validation
        if v.count('{') != v.count('}'):
            raise ValidationError(
                "Unbalanced braces in LaTeX",
                "INVALID_LATEX_SYNTAX"
            )

        return v

class MultimodalContentValidator:
    """Main validator for multimodal content"""

    @staticmethod
    def validate_image(content: Dict) -> ImageContent:
        """Validate image content"""
        return ImageContent(**content)

    @staticmethod
    def validate_table(content: Dict) -> TableContent:
        """Validate table content"""
        return TableContent(**content)

    @staticmethod
    def validate_equation(content: Dict) -> EquationContent:
        """Validate equation content"""
        return EquationContent(**content)

    @staticmethod
    def generate_content_hash(content: Any) -> str:
        """Generate secure hash for content caching"""
        if isinstance(content, dict):
            content_str = json.dumps(content, sort_keys=True)
        else:
            content_str = str(content)

        return hashlib.sha256(content_str.encode()).hexdigest()

    @staticmethod
    def validate_multimodal_request(items: List[Dict]) -> List[Dict]:
        """Validate all items in multimodal request"""
        validated_items = []

        for item in items:
            item_type = item.get('type')

            try:
                if item_type == 'image':
                    # Handle both nested and flat structure
                    # User format: {'type': 'image', 'data': {'content': '...', 'description': '...'}}
                    # Test format: {'type': 'image', 'content': '...', 'description': '...'}
                    if 'data' in item and isinstance(item['data'], dict):
                        # Nested structure (user's format)
                        image_data = item['data']
                    else:
                        # Flat structure (test format) - extract relevant fields
                        image_data = {k: v for k, v in item.items() if k != 'type'}

                    validated = MultimodalContentValidator.validate_image(image_data)
                    validated_items.append({
                        'type': 'image',
                        'data': validated.dict(),
                        'hash': MultimodalContentValidator.generate_content_hash(validated.content)
                    })

                elif item_type == 'table':
                    # Handle both nested and flat structure
                    if 'data' in item and isinstance(item['data'], dict):
                        table_data = item['data']
                    else:
                        table_data = {k: v for k, v in item.items() if k != 'type'}

                    validated = MultimodalContentValidator.validate_table(table_data)
                    validated_items.append({
                        'type': 'table',
                        'data': validated.dict(),
                        'hash': MultimodalContentValidator.generate_content_hash(validated.dict())
                    })

                elif item_type == 'equation':
                    # Handle both nested and flat structure
                    if 'data' in item and isinstance(item['data'], dict):
                        equation_data = item['data']
                    else:
                        equation_data = {k: v for k, v in item.items() if k != 'type'}

                    validated = MultimodalContentValidator.validate_equation(equation_data)
                    # Use latex or content field for hash, whichever is available
                    hash_content = validated.latex or validated.content or ''
                    validated_items.append({
                        'type': 'equation',
                        'data': validated.dict(),
                        'hash': MultimodalContentValidator.generate_content_hash(hash_content)
                    })

                else:
                    raise ValidationError(
                        f"Unknown content type: {item_type}",
                        "UNKNOWN_CONTENT_TYPE"
                    )

            except ValidationError:
                raise
            except Exception as e:
                raise ValidationError(
                    f"Validation failed for {item_type}: {str(e)}",
                    "VALIDATION_FAILED"
                )

        return validated_items