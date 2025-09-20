#!/bin/bash

# =============================================================================
# RAG-Anything Services Startup Script
# =============================================================================
# This script starts all RAG-Anything services including:
# - API server
# - Web UI (optional)
# =============================================================================

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="${SCRIPT_DIR}/RAG-Anything/api"
WEBUI_DIR="${SCRIPT_DIR}/webui"
ENV_FILE="${SCRIPT_DIR}/.env"
VENV_DIR="${SCRIPT_DIR}/venv"
LOG_DIR="${SCRIPT_DIR}/logs"

# Default settings
START_API=true
START_WEBUI=false
API_PORT=8001
WEBUI_PORT=3000
USE_NFS=false

# Function to print colored messages
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to check if port is in use
check_port() {
    local port=$1
    if lsof -i:$port >/dev/null 2>&1; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

# Function to wait for service to start
wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=0

    print_message "$YELLOW" "  ⏳ Waiting for $name to start..."

    while [ $attempt -lt $max_attempts ]; do
        if curl -s "$url" >/dev/null 2>&1; then
            print_message "$GREEN" "  ✅ $name is running"
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done

    print_message "$RED" "  ❌ $name failed to start"
    return 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --api-only)
            START_WEBUI=false
            shift
            ;;
        --with-webui)
            START_WEBUI=true
            shift
            ;;
        --api-port)
            API_PORT="$2"
            shift 2
            ;;
        --webui-port)
            WEBUI_PORT="$2"
            shift 2
            ;;
        --use-nfs)
            USE_NFS=true
            shift
            ;;
        --help)
            print_message "$CYAN" "
Usage: $0 [OPTIONS]

Options:
    --api-only      Start only the API server (default)
    --with-webui    Start both API server and Web UI
    --api-port      Set API server port (default: 8001)
    --webui-port    Set Web UI port (default: 3000)
    --use-nfs       Use NFS user permissions for API
    --help          Show this help message

Examples:
    $0                          # Start API server only
    $0 --with-webui            # Start API and Web UI
    $0 --api-port 8000         # Start API on port 8000
    $0 --use-nfs               # Start with NFS permissions
"
            exit 0
            ;;
        *)
            print_message "$RED" "Unknown option: $1"
            print_message "$YELLOW" "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Main startup sequence
main() {
    print_message "$BLUE" "
╔══════════════════════════════════════════════════════════════════════╗
║                 RAG-Anything Services Startup Script                  ║
╚══════════════════════════════════════════════════════════════════════╝"

    # Create log directory if it doesn't exist
    mkdir -p "$LOG_DIR"

    # Check environment file
    if [ ! -f "$ENV_FILE" ]; then
        print_message "$RED" "❌ Environment file not found: $ENV_FILE"
        print_message "$YELLOW" "   Please create .env file with required configuration"
        exit 1
    fi

    # Check virtual environment
    if [ ! -d "$VENV_DIR" ]; then
        print_message "$RED" "❌ Virtual environment not found: $VENV_DIR"
        print_message "$YELLOW" "   Please create virtual environment first"
        exit 1
    fi

    # Activate virtual environment
    print_message "$BLUE" "\n🐍 Activating Python virtual environment..."
    source "${VENV_DIR}/bin/activate"
    print_message "$GREEN" "  ✅ Virtual environment activated"

    # Start API Server
    if [ "$START_API" = true ]; then
        print_message "$BLUE" "\n📡 Starting API Server..."

        # Check if port is already in use
        if check_port $API_PORT; then
            print_message "$YELLOW" "  ⚠️  Port $API_PORT is already in use"
            print_message "$YELLOW" "  Run ./shutdown_services.sh first or use --api-port to specify a different port"
            exit 1
        fi

        # Change to API directory
        cd "$API_DIR"

        # Start API server
        if [ "$USE_NFS" = true ] && [ -f "./start_api_with_nfs_user.sh" ]; then
            print_message "$CYAN" "  🔐 Starting with NFS user permissions..."
            nohup ./start_api_with_nfs_user.sh > "${LOG_DIR}/api_server.log" 2>&1 &
        else
            print_message "$CYAN" "  🚀 Starting API server on port $API_PORT..."
            export API_PORT=$API_PORT
            nohup python3 rag_api_server.py > "${LOG_DIR}/api_server.log" 2>&1 &
        fi

        local api_pid=$!
        print_message "$GREEN" "  ✅ API server started (PID: $api_pid)"

        # Wait for API to be ready
        wait_for_service "http://localhost:$API_PORT/health" "API Server"
    fi

    # Start Web UI
    if [ "$START_WEBUI" = true ]; then
        print_message "$BLUE" "\n🌐 Starting Web UI..."

        # Check if port is already in use
        if check_port $WEBUI_PORT; then
            print_message "$YELLOW" "  ⚠️  Port $WEBUI_PORT is already in use"
            print_message "$YELLOW" "  Run ./shutdown_services.sh first or use --webui-port to specify a different port"
            exit 1
        fi

        # Change to Web UI directory
        cd "$WEBUI_DIR"

        # Check if node_modules exists
        if [ ! -d "node_modules" ]; then
            print_message "$YELLOW" "  📦 Installing Web UI dependencies..."
            npm install
        fi

        # Start Web UI
        print_message "$CYAN" "  🚀 Starting Web UI on port $WEBUI_PORT..."
        export PORT=$WEBUI_PORT
        nohup npm run dev > "${LOG_DIR}/webui.log" 2>&1 &

        local webui_pid=$!
        print_message "$GREEN" "  ✅ Web UI started (PID: $webui_pid)"

        # Wait for Web UI to be ready
        sleep 3
        if check_port $WEBUI_PORT; then
            print_message "$GREEN" "  ✅ Web UI is running"
        else
            print_message "$YELLOW" "  ⚠️  Web UI may take a moment to start"
        fi
    fi

    # Display service status
    print_message "$BLUE" "\n
╔══════════════════════════════════════════════════════════════════════╗
║                        Services Started                               ║
╚══════════════════════════════════════════════════════════════════════╝"

    print_message "$GREEN" "\n📊 Service URLs:"
    print_message "$CYAN" "  API Server:    http://localhost:$API_PORT"
    print_message "$CYAN" "  API Docs:      http://localhost:$API_PORT/docs"
    print_message "$CYAN" "  Health Check:  http://localhost:$API_PORT/health"

    if [ "$START_WEBUI" = true ]; then
        print_message "$CYAN" "  Web UI:        http://localhost:$WEBUI_PORT"
    fi

    print_message "$GREEN" "\n📋 Log Files:"
    print_message "$CYAN" "  API Server:    ${LOG_DIR}/api_server.log"
    if [ "$START_WEBUI" = true ]; then
        print_message "$CYAN" "  Web UI:        ${LOG_DIR}/webui.log"
    fi

    print_message "$YELLOW" "\n💡 Tips:"
    print_message "$CYAN" "  • To stop all services:     ./shutdown_services.sh"
    print_message "$CYAN" "  • To view API logs:         tail -f ${LOG_DIR}/api_server.log"
    if [ "$START_WEBUI" = true ]; then
        print_message "$CYAN" "  • To view Web UI logs:      tail -f ${LOG_DIR}/webui.log"
    fi
    print_message "$CYAN" "  • To check service status:  curl http://localhost:$API_PORT/health"
}

# Handle script interruption
trap 'print_message "$RED" "\n❌ Script interrupted"; exit 130' INT TERM

# Run main function
main