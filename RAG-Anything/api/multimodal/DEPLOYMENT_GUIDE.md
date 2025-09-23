# Multimodal Query System Deployment Guide

## Quick Start

### 1. Install Dependencies
```bash
cd /home/ragsvr/projects/ragsystem/RAG-Anything/api

# Install multimodal dependencies (optional, for full functionality)
pip install -r multimodal/requirements.txt

# Install system dependencies for OCR (optional)
sudo apt-get install tesseract-ocr
```

### 2. Start API Server
```bash
# The multimodal endpoints are automatically integrated
cd /home/ragsvr/projects/ragsystem/RAG-Anything/api
python rag_api_server.py

# Server will start with multimodal support if dependencies are available
# Check logs for: "✅ 多模态处理器初始化完成"
```

### 3. Access Web Interface
```bash
cd /home/ragsvr/projects/ragsystem/webui
npm install  # If not already installed
npm run dev

# Navigate to http://localhost:3000/multimodal
```

## Deployment Options

### Option 1: Full Installation (Recommended)
- All multimodal features enabled
- Requires: Python packages, Redis, PostgreSQL
- Benefits: OCR, visual descriptions, caching

### Option 2: Minimal Installation
- Basic multimodal processing
- Skips: Vision models, Redis cache
- Still functional with reduced features

### Option 3: API-Only Mode
- No frontend changes needed
- Use existing API clients
- Call `/api/v1/query/multimodal` endpoint

## Verification

### Check Installation Status
```bash
# Test multimodal modules
cd /home/ragsvr/projects/ragsystem/RAG-Anything/api
python test_multimodal.py

# Check API health
curl http://localhost:8000/api/v1/multimodal/health
```

### Expected Response
```json
{
  "status": "healthy",
  "components": {
    "cache": "healthy",
    "rag": "healthy"
  }
}
```

## Configuration

### Optional: Redis Setup
```bash
# Only needed for caching optimization
docker run -d -p 6379:6379 redis:latest

# Or use existing Redis instance
export REDIS_URL=redis://your-redis-host:6379
```

### Optional: Vision Models
```bash
# For enhanced image descriptions (requires GPU)
pip install transformers torch

# Model will auto-download on first use (~500MB)
```

## Testing

### API Test with cURL
```bash
# Simple multimodal query test
curl -X POST http://localhost:8000/api/v1/query/multimodal \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is this?",
    "mode": "hybrid",
    "multimodal_content": [{
      "type": "equation",
      "latex": "E = mc^2",
      "description": "Famous physics equation"
    }]
  }'
```

### Frontend Test
1. Open http://localhost:3000/multimodal
2. Enter a question
3. Add an equation: Click "Add Equation" → Enter `E = mc^2`
4. Click "Execute Multimodal Query"
5. View results with processing statistics

## Troubleshooting

### Issue: "多模态处理器未安装"
**Solution**: Install dependencies
```bash
pip install Pillow pandas numpy pydantic
```

### Issue: OCR not working
**Solution**: Install tesseract
```bash
sudo apt-get install tesseract-ocr
```

### Issue: Redis connection failed
**Solution**: Start Redis or disable cache
```bash
# Start Redis
docker run -d -p 6379:6379 redis:latest

# Or run without Redis (still functional)
export REDIS_URL=""
```

### Issue: Frontend route not found
**Solution**: Rebuild frontend
```bash
cd /home/ragsvr/projects/ragsystem/webui
npm run build
npm run dev
```

## Production Deployment

### Using PM2
```bash
# Install PM2
npm install -g pm2

# Start API server
pm2 start /home/ragsvr/projects/ragsystem/RAG-Anything/api/rag_api_server.py \
  --name rag-api \
  --interpreter python3

# Start frontend
cd /home/ragsvr/projects/ragsystem/webui
pm2 start npm --name rag-webui -- run dev
```

### Using Docker
```bash
# Build API image (create Dockerfile first)
docker build -t rag-multimodal ./RAG-Anything/api

# Run with environment variables
docker run -d \
  -p 8000:8000 \
  -e REDIS_URL=redis://redis:6379 \
  -e POSTGRES_HOST=postgres \
  --name rag-api \
  rag-multimodal
```

### Using systemd
```bash
# Create service file
sudo nano /etc/systemd/system/rag-api.service

# Add configuration
[Unit]
Description=RAG-Anything API with Multimodal Support
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=ragsvr
WorkingDirectory=/home/ragsvr/projects/ragsystem/RAG-Anything/api
ExecStart=/usr/bin/python3 rag_api_server.py
Restart=always

[Install]
WantedBy=multi-user.target

# Enable and start
sudo systemctl enable rag-api
sudo systemctl start rag-api
```

## Monitoring

### Check Logs
```bash
# API logs
journalctl -u rag-api -f

# Or if using PM2
pm2 logs rag-api
```

### Check Metrics
```bash
# Get processing metrics
curl http://localhost:8000/api/v1/multimodal/metrics

# Monitor cache performance
curl http://localhost:8000/api/v1/cache/statistics
```

## Rollback

If issues occur, the system gracefully degrades:
1. Multimodal endpoints return 503 if not available
2. Regular query endpoints continue working
3. Frontend shows warning if multimodal unavailable
4. No impact on existing document processing

To completely disable:
```bash
# Remove multimodal imports from rag_api_server.py
# Or set environment variable
export DISABLE_MULTIMODAL=true
```

## Support

For issues or questions:
1. Check logs for error messages
2. Run `test_multimodal.py` for diagnostics
3. Verify all services are running (PostgreSQL, Redis, API)
4. Ensure Python 3.10 and dependencies installed