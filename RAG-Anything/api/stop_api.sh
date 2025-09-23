#!/bin/bash

# ========================================
# Stop RAG-Anything API Services
# ========================================

echo "🛑 Stopping RAG-Anything API Services..."
echo "======================================="

# Kill processes on port 8000
echo "📍 Checking port 8000..."
PIDS_8000=$(lsof -ti:8000)
if [ ! -z "$PIDS_8000" ]; then
    echo "   Found processes on port 8000: $PIDS_8000"
    for PID in $PIDS_8000; do
        echo "   ❌ Killing process $PID..."
        kill -9 $PID 2>/dev/null
    done
    echo "   ✅ Port 8000 cleared"
else
    echo "   ℹ️  No processes found on port 8000"
fi

echo ""

# Kill processes on port 8001
echo "📍 Checking port 8001..."
PIDS_8001=$(lsof -ti:8001)
if [ ! -z "$PIDS_8001" ]; then
    echo "   Found processes on port 8001: $PIDS_8001"
    for PID in $PIDS_8001; do
        echo "   ❌ Killing process $PID..."
        kill -9 $PID 2>/dev/null
    done
    echo "   ✅ Port 8001 cleared"
else
    echo "   ℹ️  No processes found on port 8001"
fi

echo ""

# Also kill any rag_api_server.py processes
echo "📍 Checking for rag_api_server.py processes..."
PIDS_RAG=$(ps aux | grep "rag_api_server.py" | grep -v grep | awk '{print $2}')
if [ ! -z "$PIDS_RAG" ]; then
    echo "   Found rag_api_server.py processes: $PIDS_RAG"
    for PID in $PIDS_RAG; do
        echo "   ❌ Killing process $PID..."
        kill -9 $PID 2>/dev/null
    done
    echo "   ✅ All rag_api_server.py processes stopped"
else
    echo "   ℹ️  No rag_api_server.py processes found"
fi

echo ""

# Verify ports are free
echo "🔍 Verifying ports are free..."
sleep 1

PORT_8000_CHECK=$(lsof -ti:8000)
PORT_8001_CHECK=$(lsof -ti:8001)

if [ -z "$PORT_8000_CHECK" ] && [ -z "$PORT_8001_CHECK" ]; then
    echo "✅ Success! All API services stopped."
    echo "   - Port 8000: FREE"
    echo "   - Port 8001: FREE"
else
    echo "⚠️  Warning: Some processes may still be running"
    [ ! -z "$PORT_8000_CHECK" ] && echo "   - Port 8000 still has processes: $PORT_8000_CHECK"
    [ ! -z "$PORT_8001_CHECK" ] && echo "   - Port 8001 still has processes: $PORT_8001_CHECK"
fi

echo ""
echo "======================================="
echo "💡 To restart the API services, run:"
echo "   ./start_api.sh"
echo "======================================="