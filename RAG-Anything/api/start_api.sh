#!/bin/bash

# Simple API startup script using virtual environment

set -e

# Get the script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "🚀 Starting RAG-Anything API Server..."
echo "📁 Project directory: $PROJECT_ROOT"
echo "="
echo ""

# Check if virtual environment exists
VENV_PATH="$PROJECT_ROOT/venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "❌ Error: Virtual environment not found at $VENV_PATH"
    echo "Please create it first with: python3 -m venv venv"
    exit 1
fi

# Activate virtual environment and start the server
cd "$PROJECT_ROOT"
echo "✅ Activating virtual environment..."
source venv/bin/activate

# Check if lightrag is installed
if python -c "import lightrag" 2>/dev/null; then
    echo "✅ LightRAG is installed"
else
    echo "❌ LightRAG is not installed in the virtual environment"
    echo "Installing required packages..."
    pip install lightrag-hku
fi

# Change to API directory and start server
cd RAG-Anything/api
echo "🔄 Starting API server..."
echo ""
python rag_api_server.py