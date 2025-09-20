#!/bin/bash

# =============================================================================
# RAG-Anything Services Shutdown Script
# =============================================================================
# This script safely shuts down all running RAG-Anything services including:
# - API servers (rag_api_server.py)
# - Web UI development servers (vite/node)
# - Background processing tasks
# =============================================================================

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/shutdown_services.log"

# Function to print colored messages
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to log messages
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Function to find and kill processes
kill_process_by_pattern() {
    local pattern=$1
    local name=$2
    local signal=${3:-TERM}

    local pids=$(pgrep -f "$pattern" 2>/dev/null || true)

    if [ -n "$pids" ]; then
        print_message "$YELLOW" "  ⚠️  Found $name processes: $pids"

        # Try graceful shutdown first
        for pid in $pids; do
            if kill -$signal $pid 2>/dev/null; then
                print_message "$GREEN" "  ✅ Sent $signal signal to PID $pid"
                log_message "Sent $signal signal to $name (PID: $pid)"
            fi
        done

        # Wait for processes to terminate
        sleep 2

        # Force kill if still running
        for pid in $pids; do
            if kill -0 $pid 2>/dev/null; then
                print_message "$YELLOW" "  ⚡ Force killing PID $pid"
                kill -9 $pid 2>/dev/null || true
                log_message "Force killed $name (PID: $pid)"
            fi
        done

        return 0
    else
        print_message "$BLUE" "  ℹ️  No $name processes found"
        return 1
    fi
}

# Function to check if any services are running
check_running_services() {
    local found=1  # Default to not found (inverted logic for shell)

    # Check for API servers
    if pgrep -f "rag_api_server" >/dev/null 2>&1; then
        found=0
    fi

    # Check for MinerU processes
    if pgrep -f "mineru" >/dev/null 2>&1; then
        found=0
    fi

    # Check for Docling processes
    if pgrep -f "docling" >/dev/null 2>&1; then
        found=0
    fi

    # Check for Vite dev servers
    if pgrep -f "vite" >/dev/null 2>&1; then
        found=0
    fi

    # Check for Qwen embedding processes
    if pgrep -f "simple_qwen_embed" >/dev/null 2>&1; then
        found=0
    fi

    return $found
}

# Main shutdown sequence
main() {
    print_message "$BLUE" "
╔══════════════════════════════════════════════════════════════════════╗
║                 RAG-Anything Services Shutdown Script                 ║
╚══════════════════════════════════════════════════════════════════════╝"

    log_message "Starting shutdown sequence"

    # Check if any services are running
    if ! check_running_services; then
        print_message "$GREEN" "\n✅ No RAG-Anything services are currently running"
        exit 0
    fi

    print_message "$YELLOW" "\n🔍 Scanning for running services..."

    # 1. Shutdown API servers
    print_message "$BLUE" "\n📡 Shutting down API servers..."
    kill_process_by_pattern "rag_api_server" "RAG API Server" "TERM" || true

    # 2. Shutdown document parsers
    print_message "$BLUE" "\n📄 Shutting down document parsers..."
    kill_process_by_pattern "mineru" "MinerU Parser" "TERM" || true
    kill_process_by_pattern "docling" "Docling Parser" "TERM" || true

    # 3. Shutdown embedding services
    print_message "$BLUE" "\n🧠 Shutting down embedding services..."
    kill_process_by_pattern "simple_qwen_embed" "Qwen Embedding" "TERM" || true

    # 4. Shutdown web UI servers
    print_message "$BLUE" "\n🌐 Shutting down Web UI servers..."
    kill_process_by_pattern "vite" "Vite Dev Server" "TERM" || true

    # 5. Clean up any orphaned Node processes
    print_message "$BLUE" "\n🧹 Cleaning up orphaned processes..."

    # Kill any node processes running from the webui directory
    local node_pids=$(ps aux | grep -E "node.*webui" | grep -v grep | awk '{print $2}' || true)
    if [ -n "$node_pids" ]; then
        for pid in $node_pids; do
            kill -TERM $pid 2>/dev/null || true
            print_message "$GREEN" "  ✅ Terminated orphaned Node process (PID: $pid)"
        done
    fi

    # 6. Clear any lock files or temporary files
    print_message "$BLUE" "\n🗑️  Cleaning up temporary files..."

    # Remove API server lock files if they exist
    if [ -f "${SCRIPT_DIR}/RAG-Anything/api/.lock" ]; then
        rm -f "${SCRIPT_DIR}/RAG-Anything/api/.lock"
        print_message "$GREEN" "  ✅ Removed API server lock file"
    fi

    # Clear WebSocket connection files if any
    if [ -d "${SCRIPT_DIR}/RAG-Anything/api/ws_connections" ]; then
        rm -rf "${SCRIPT_DIR}/RAG-Anything/api/ws_connections"
        print_message "$GREEN" "  ✅ Cleared WebSocket connection files"
    fi

    # 7. Final verification
    print_message "$BLUE" "\n🔍 Verifying shutdown..."
    sleep 2

    if check_running_services; then
        print_message "$YELLOW" "\n⚠️  Some services may still be running. Checking again..."

        # List remaining processes
        print_message "$YELLOW" "\nRemaining processes:"
        ps aux | grep -E "rag_api_server|mineru|docling|vite|qwen" | grep -v grep || true

        print_message "$RED" "\n❌ Some services could not be shut down properly"
        print_message "$YELLOW" "   You may need to manually kill these processes"
        log_message "Shutdown sequence completed with warnings"
        exit 1
    else
        print_message "$GREEN" "\n✅ All RAG-Anything services have been successfully shut down"
        log_message "Shutdown sequence completed successfully"
    fi

    # 8. Display port status
    print_message "$BLUE" "\n📊 Port Status:"

    # Check common ports
    for port in 8000 8001 3000 3001 5000; do
        if lsof -i:$port >/dev/null 2>&1; then
            print_message "$YELLOW" "  ⚠️  Port $port is still in use"
        else
            print_message "$GREEN" "  ✅ Port $port is free"
        fi
    done

    print_message "$BLUE" "\n
╔══════════════════════════════════════════════════════════════════════╗
║                        Shutdown Complete                              ║
╚══════════════════════════════════════════════════════════════════════╝"
}

# Handle script interruption
trap 'print_message "$RED" "\n❌ Script interrupted"; exit 130' INT TERM

# Run main function
main "$@"