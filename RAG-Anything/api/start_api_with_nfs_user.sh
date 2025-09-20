#!/bin/bash

# API启动脚本 - 使用NFS用户权限
# 确保以newragsvr用户身份运行API服务以获得NFS写权限

set -e

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "🚀 启动RAG-Anything API服务..."
echo "📁 项目目录: $PROJECT_ROOT"
echo "👤 切换到NFS用户: newragsvr"
echo "=" * 50

# 检查newragsvr用户是否存在
if ! id newragsvr &>/dev/null; then
    echo "❌ 错误: newragsvr用户不存在"
    echo "请先创建用户: sudo useradd -m newragsvr"
    exit 1
fi

# 检查虚拟环境是否存在
VENV_PATH="$PROJECT_ROOT/venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "❌ 错误: 虚拟环境不存在 ($VENV_PATH)"
    echo "请先创建虚拟环境"
    exit 1
fi

# 检查NFS挂载和权限
echo "🔍 检查NFS存储权限..."
if sudo -u newragsvr test -w /mnt/ragsystem; then
    echo "✅ newragsvr用户对NFS存储有写权限"
else
    echo "⚠️ 警告: newragsvr用户对NFS存储可能没有写权限"
fi

# 切换到项目目录
cd "$PROJECT_ROOT"

# 以newragsvr用户身份启动API服务
echo "🔄 以newragsvr用户身份启动API服务..."
exec sudo -u newragsvr bash -c "
    source venv/bin/activate && \
    cd RAG-Anything/api && \
    python rag_api_server.py
"