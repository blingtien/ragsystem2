# Multimodal Query Processing Module

Production-ready multimodal query processing for RAG-Anything, supporting images, tables, and mathematical equations.

## Features

### 🔒 Security
- **Layered Validation**: Strict type and format checks prevent invalid/malicious entries
- **Content Sanitization**: Automatic removal of dangerous LaTeX commands and SQL injection attempts
- **Size Limits**: Enforced limits on image size (10MB), table size (10K rows), equation length (5000 chars)
- **Format Whitelisting**: Only JPEG, PNG, WebP images allowed

### ⚡ Performance
- **Multi-level Caching**: Memory → Redis → PostgreSQL for optimal speed
- **Parallel Processing**: Concurrent processing of multiple content items
- **Auto-scaling**: Automatic image resizing for oversized content
- **Batch Operations**: Process multiple items in single request

### 🎯 Content Processing
- **Image Processing**:
  - OCR text extraction (pytesseract)
  - Visual description generation (BLIP model)
  - Metadata extraction (dimensions, format)
  - Thumbnail generation

- **Table Processing**:
  - Statistical analysis
  - Pattern detection
  - Column type inference
  - Data summarization

- **Equation Processing**:
  - LaTeX validation
  - Variable extraction
  - Complexity assessment
  - Domain identification

## Installation

### Prerequisites
```bash
# Install system dependencies
sudo apt-get install tesseract-ocr libreoffice

# Install Python dependencies
cd RAG-Anything/api/multimodal
pip install -r requirements.txt
```

### Optional: Vision Models
```bash
# For enhanced visual descriptions (requires GPU)
pip install transformers torch
```

## API Usage

### Multimodal Query Endpoint

**Endpoint**: `POST /api/v1/query/multimodal`

**Request Format**:
```json
{
  "query": "What patterns do you see in this data?",
  "mode": "hybrid",
  "multimodal_content": [
    {
      "type": "image",
      "content": "base64_encoded_image",
      "description": "Sales chart for Q3"
    },
    {
      "type": "table",
      "headers": ["Product", "Q1", "Q2", "Q3"],
      "rows": [["A", 100, 120, 150], ["B", 90, 110, 130]],
      "description": "Quarterly sales data"
    },
    {
      "type": "equation",
      "latex": "y = mx + b",
      "description": "Linear regression model"
    }
  ],
  "vlm_enhanced": true
}
```

**Response Format**:
```json
{
  "success": true,
  "query_id": "uuid",
  "result": "Based on the image and table data, I can see increasing trends...",
  "processing_stats": {
    "processing_time": 2.34,
    "items_processed": 3,
    "cache_hits": 1
  },
  "citations": [
    {
      "type": "image",
      "index": 0,
      "content": "OCR extracted text...",
      "relevance": "high"
    }
  ]
}
```

### Health Check Endpoint

**Endpoint**: `GET /api/v1/multimodal/health`

**Response**:
```json
{
  "status": "healthy",
  "timestamp": "2025-01-20T10:00:00Z",
  "components": {
    "cache": "healthy",
    "rag": "healthy"
  }
}
```

### Metrics Endpoint

**Endpoint**: `GET /api/v1/multimodal/metrics`

**Response**:
```json
{
  "success": true,
  "metrics": {
    "total_queries": 150,
    "successful_queries": 145,
    "failed_queries": 5,
    "cache_hits": 89,
    "avg_processing_time": 1.85,
    "error_categories": {
      "validation_error": 3,
      "processing_error": 2
    }
  }
}
```

## Frontend Integration

The multimodal query interface is available at `/multimodal` in the web UI.

### Features:
- Drag-and-drop image upload
- Table input via CSV or JSON
- LaTeX equation editor with preview
- Real-time processing status
- Result visualization with citations

### Usage:
1. Navigate to "多模态查询" in the sidebar
2. Enter your question
3. Upload images, add tables, or input equations
4. Click "Execute Multimodal Query"
5. View results with citations and processing statistics

## Architecture

### Component Structure
```
multimodal/
├── api_endpoint.py      # Main API handler and orchestration
├── validators.py        # Input validation and sanitization
├── processors.py        # Content-specific processors
├── cache_manager.py     # Multi-level caching system
├── error_handlers.py    # Error categorization and recovery
└── __init__.py         # Package exports
```

### Processing Pipeline
1. **Validation**: Input sanitization and format checking
2. **Cache Check**: Query cache for previously processed content
3. **Processing**: Parallel processing of all content items
4. **Embedding**: Generate embeddings for semantic search
5. **RAG Query**: Execute query with multimodal context
6. **Citation Extraction**: Identify relevant content citations
7. **Cache Update**: Store results for future queries

## Configuration

### Environment Variables
```bash
# Redis Configuration
REDIS_URL=redis://localhost:6379

# PostgreSQL Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=raganything
POSTGRES_USER=raganything_user
POSTGRES_PASSWORD=your_password

# Rate Limiting
MULTIMODAL_RATE_LIMIT=10/minute

# Cache Settings
MULTIMODAL_CACHE_TTL=3600
MULTIMODAL_MEMORY_CACHE_SIZE=100
```

## Error Handling

### Error Categories
- **VALIDATION**: Invalid input format or content
- **PROCESSING**: Content processing failures
- **NETWORK**: Connection or timeout issues
- **RESOURCE**: Memory or disk space issues
- **UNKNOWN**: Unexpected errors

### Recovery Strategies
- Validation errors: Return detailed guidance
- Processing errors: Fallback to basic processing
- Network errors: Retry with exponential backoff
- Resource errors: Reduce batch size or clear cache

## Performance Optimization

### Caching Strategy
- **Memory Cache**: LRU cache for 100 most recent items
- **Redis Cache**: 1-hour TTL for hot data
- **PostgreSQL**: Persistent cache with access tracking

### Best Practices
1. **Batch Requests**: Send multiple items in single request
2. **Image Optimization**: Pre-resize images client-side
3. **Cache Warming**: Pre-process frequently used content
4. **Connection Pooling**: Reuse database connections

## Troubleshooting

### Common Issues

**OCR Not Working**:
```bash
# Install tesseract
sudo apt-get install tesseract-ocr
# Verify installation
tesseract --version
```

**Vision Model Not Loading**:
```bash
# Install transformers with CUDA support
pip install transformers[torch]
# Verify GPU availability
python -c "import torch; print(torch.cuda.is_available())"
```

**Cache Connection Failed**:
```bash
# Check Redis status
redis-cli ping
# Check PostgreSQL status
psql -U raganything_user -d raganything -c "SELECT 1"
```

## Development

### Testing
```bash
# Run unit tests
pytest multimodal/tests/

# Run integration tests
pytest multimodal/tests/integration/

# Test with sample data
python -m multimodal.test_client
```

### Adding New Content Types
1. Create processor class inheriting from `ContentProcessor`
2. Implement `process()` and `generate_embedding()` methods
3. Register in `ProcessorFactory`
4. Add validation in `MultimodalContentValidator`
5. Update frontend component for new type

## License

Part of RAG-Anything project. See main LICENSE file.