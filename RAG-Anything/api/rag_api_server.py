#!/usr/bin/env python3
"""
RAG-Anything API Server
基于RAGAnything的实际API服务器，替换mock版本
支持文档上传、处理、查询等功能
"""

import asyncio
import json
import os
import uuid
import psutil
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import sys
from contextlib import asynccontextmanager

# 检查是否在虚拟环境中运行
if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
    # 不在虚拟环境中，尝试添加虚拟环境路径
    venv_path = Path(__file__).resolve().parent.parent.parent / 'venv'
    if venv_path.exists():
        venv_python = venv_path / 'bin' / 'python'
        if venv_python.exists():
            print("⚠️  警告: 未在虚拟环境中运行")
            print(f"   建议使用: {venv_python} rag_api_server.py")
            print(f"   或运行: ./start_api.sh")
            print("   继续尝试添加虚拟环境路径...")

            site_packages = venv_path / 'lib' / 'python3.10' / 'site-packages'
            if site_packages.exists() and str(site_packages) not in sys.path:
                sys.path.insert(0, str(site_packages))
                print(f"   ✅ 已添加虚拟环境路径: {site_packages}")
    else:
        print("❌ 错误: 未找到虚拟环境且未在虚拟环境中运行")
        print("   请先创建虚拟环境: python3 -m venv venv")
        print("   或激活虚拟环境: source venv/bin/activate")
        sys.exit(1)

# 加载环境变量
from dotenv import load_dotenv
from pathlib import Path

# 使用相对路径加载.env文件 (从API目录向上两级到项目根目录)
current_dir = Path(__file__).resolve().parent  # RAG-Anything/api/
project_root = current_dir.parent.parent  # ragsystem/
env_path = project_root / '.env'

# 如果.env文件不存在，尝试其他位置
if not env_path.exists():
    # 尝试在RAG-Anything目录下查找
    alt_env_path = current_dir.parent / '.env'
    if alt_env_path.exists():
        env_path = alt_env_path
    else:
        print(f"警告: 未找到.env文件，尝试的路径: {env_path} 和 {alt_env_path}")

load_dotenv(env_path, override=True)
print(f"加载环境变量文件: {env_path}")
# 调试：打印Neo4j密码以验证加载正确
import os
print(f"Neo4j密码已加载: {os.getenv('NEO4J_PASSWORD')}")

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc, logger
from raganything import RAGAnything, RAGAnythingConfig
from simple_qwen_embed import qwen_embed
from dotenv import load_dotenv

# 导入数据库配置
sys.path.append(str(Path(__file__).parent.parent))
from database_config import load_database_config, create_lightrag_kwargs

# 导入智能路由和文本处理器
from smart_parser_router import router
from direct_text_processor import text_processor
# 导入详细状态跟踪器
from detailed_status_tracker import detailed_tracker, StatusLogger, ProcessingStage
# 导入WebSocket日志处理器
from websocket_log_handler import websocket_log_handler, setup_websocket_logging, get_log_summary, get_core_progress, clear_logs
# 导入缓存增强处理器和统计跟踪
from cache_enhanced_processor import CacheEnhancedProcessor
from cache_statistics import initialize_cache_tracking, get_cache_stats_tracker
# 导入增强的错误处理和进度跟踪
from enhanced_error_handler import enhanced_error_handler
from advanced_progress_tracker import advanced_progress_tracker
# 导入连接状态检测器
from connection_status_checker import RemoteConnectionChecker
# 导入并行批量处理器
from parallel_batch_processor import ParallelBatchProcessor

# 导入状态管理器
from core.state_manager import StateManager, Document

# 导入多模态处理组件
try:
    from multimodal.api_endpoint import MultimodalAPIHandler, MultimodalQueryRequest, MultimodalQueryResponse
    from multimodal.cache_manager import CacheManager
    from multimodal.validators import ValidationError
    MULTIMODAL_AVAILABLE = True
except ImportError as e:
    logger.warning(f"多模态组件未安装或导入失败: {e}")
    MULTIMODAL_AVAILABLE = False

# 注释掉其他.env加载，统一使用上面的绝对路径
# load_dotenv(dotenv_path="/home/ragsvr/projects/ragsystem/RAG-Anything/.env", override=False)  # 优先加载RAG-Anything的.env
# load_dotenv(dotenv_path="/home/ragsvr/projects/ragsystem/.env", override=False)  # 备用配置
# load_dotenv(dotenv_path="/home/ragsvr/projects/ragsystem/.env.performance", override=True)  # 性能配置覆盖

# 配置日志
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app):
    """应用生命周期管理器"""
    # 启动时执行
    logger.info("🚀 RAG-Anything API服务启动中...")
    logger.info("=" * 80)
    
    # Step 1: 检测远程存储连接状态
    logger.info("📡 检测远程存储连接状态...")
    connection_checker = RemoteConnectionChecker(timeout=5.0)
    connection_results = await connection_checker.check_all_connections()
    
    # 检查关键服务连接状态
    critical_services = ['PostgreSQL', 'Neo4j', 'NFS存储']
    failed_critical = [name for name, result in connection_results.items() 
                      if name in critical_services and 
                      result.status.name in ['FAILED', 'TIMEOUT']]
    
    if failed_critical:
        logger.warning(f"⚠️ 关键服务连接异常: {', '.join(failed_critical)}")
        logger.warning("服务将继续启动，但可能影响功能完整性")
    else:
        logger.info("✅ 所有关键远程服务连接正常")
    
    # Step 2: 设置WebSocket日志处理器
    logger.info("🔧 初始化WebSocket日志处理器...")
    setup_websocket_logging()
    websocket_log_handler.set_event_loop(asyncio.get_event_loop())
    logger.info("✅ WebSocket日志处理器初始化完成")
    
    # Step 3: 初始化状态管理器
    logger.info("🗄️ 初始化状态管理器...")
    await initialize_state_manager()
    logger.info("✅ 状态管理器初始化完成")

    # Step 4: 初始化RAG系统
    logger.info("🧠 初始化RAG系统...")
    await initialize_rag()
    logger.info("✅ RAG系统初始化完成")

    # Step 4.5: 初始化多模态处理器
    if MULTIMODAL_AVAILABLE:
        logger.info("🎨 初始化多模态处理器...")
        await initialize_multimodal_handler()
        logger.info("✅ 多模态处理器初始化完成")
    else:
        logger.info("⚠️ 多模态处理器不可用")

    # Step 5: 加载已存在的文档
    print(f"[STARTUP] Step 5: 开始加载已存在的文档...", flush=True)
    logger.info("📚 加载已存在的文档...")
    await load_existing_documents()
    print(f"[STARTUP] 文档加载完成，documents字典中有 {len(documents)} 个文档", flush=True)
    logger.info(f"✅ 文档加载完成，当前有 {len(documents)} 个文档")
    
    # Step 5: 启动完成汇总
    logger.info("=" * 80)
    logger.info("🎉 RAG-Anything API服务启动完成!")
    logger.info(f"📊 服务状态汇总:")
    logger.info(f"   - 文档数量: {len(documents)}")
    logger.info(f"   - RAG系统: {'✅ 已初始化' if rag_instance else '❌ 初始化失败'}")
    logger.info(f"   - 缓存系统: {'✅ 已启用' if cache_enhanced_processor else '❌ 未启用'}")
    
    # 显示关键配置信息
    working_dir = os.getenv('WORKING_DIR', './rag_storage')
    storage_mode = os.getenv('STORAGE_MODE', 'hybrid')
    logger.info(f"   - 工作目录: {working_dir}")
    logger.info(f"   - 存储模式: {storage_mode}")
    logger.info(f"   - 服务地址: http://localhost:8000")
    logger.info("=" * 80)
    
    yield
    
    # 关闭时执行
    logger.info("🛑 RAG-Anything API服务关闭中...")
    logger.info("👋 服务已关闭")

app = FastAPI(
    title="RAG-Anything API", 
    version="1.0.0",
    lifespan=lifespan
)

# 启用CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
rag_instance: Optional[RAGAnything] = None
cache_enhanced_processor: Optional[CacheEnhancedProcessor] = None
parallel_batch_processor: Optional[ParallelBatchProcessor] = None  # 并行批量处理器
state_manager: Optional[StateManager] = None  # 状态管理器
multimodal_handler: Optional[MultimodalAPIHandler] = None  # 多模态处理器
tasks: Dict[str, dict] = {}
documents: Dict[str, dict] = {}  # 将逐步废弃，替换为state_manager
active_websockets: Dict[str, WebSocket] = {}
processing_log_websockets: List[WebSocket] = []  # 文档解析日志WebSocket连接列表
batch_operations: Dict[str, dict] = {}  # 批量操作状态跟踪

# 日志显示模式
class LogDisplayMode(BaseModel):
    mode: str = "summary"  # core_only, summary, detailed, all
    include_debug: bool = False

# Phase 2: Database-only storage configuration
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/home/ragsvr/projects/ragsystem/uploads")
WORKING_DIR = os.getenv("WORKING_DIR", "/home/ragsvr/projects/ragsystem/rag_storage")  # Still needed for some operations
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/tmp/rag_output_temp")  # Fixed for Phase 2

# Use temporary directories only for transient file operations
TEMP_WORKING_DIR = "/tmp/rag_temp"
TEMP_OUTPUT_DIR = "/tmp/rag_output_temp"

# 确保目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(WORKING_DIR, exist_ok=True)
os.makedirs(TEMP_WORKING_DIR, exist_ok=True)
os.makedirs(TEMP_OUTPUT_DIR, exist_ok=True)

# Request/Response 模型
class QueryRequest(BaseModel):
    query: str
    mode: str = "hybrid"
    vlm_enhanced: bool = False

class DocumentDeleteRequest(BaseModel):
    document_ids: List[str]

# 批量处理相关数据模型
class BatchUploadResponse(BaseModel):
    success: bool
    uploaded_count: int
    failed_count: int
    total_files: int
    results: List[dict]
    message: str

class BatchProcessRequest(BaseModel):
    document_ids: List[str]
    parser: Optional[str] = None
    parse_method: Optional[str] = None

class BatchProcessResponse(BaseModel):
    success: bool
    started_count: int
    failed_count: int
    total_requested: int
    results: List[dict]
    batch_operation_id: str
    message: str
    cache_performance: Optional[dict] = None

class BatchOperationStatus(BaseModel):
    batch_operation_id: str
    operation_type: str  # "upload" | "process"
    status: str  # "running" | "completed" | "failed" | "cancelled"
    total_items: int
    completed_items: int
    failed_items: int
    progress: float
    started_at: str
    completed_at: Optional[str] = None
    results: List[dict]

# 模拟处理阶段
PROCESSING_STAGES = [
    ("parsing", "解析文档", 15),
    ("separation", "分离内容", 5),
    ("text_insert", "插入文本", 25),
    ("image_process", "处理图片", 20),
    ("table_process", "处理表格", 15),
    ("equation_process", "处理公式", 10),
    ("graph_build", "构建知识图谱", 15),
    ("indexing", "创建索引", 10),
]

def save_documents_state():
    """保存文档和任务状态到磁盘"""
    try:
        # Use TEMP_WORKING_DIR for state persistence
        state_file = os.path.join(TEMP_WORKING_DIR, "api_documents_state.json")
        state_data = {
            "documents": documents,
            "tasks": tasks,
            "batch_operations": batch_operations,
            "saved_at": datetime.now().isoformat()
        }
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        logger.info(f"保存了 {len(documents)} 个文档状态到磁盘: {state_file}")
    except Exception as e:
        logger.error(f"保存文档状态失败: {str(e)}")

async def load_existing_documents():
    """从数据库加载已存在的文档状态"""
    global documents, tasks, batch_operations

    print(f"[STARTUP] load_existing_documents() 被调用", flush=True)
    print(f"[STARTUP] state_manager存在: {state_manager is not None}", flush=True)

    try:
        # 使用StateManager从数据库加载所有文档（包括已完成和失败的）
        if state_manager:
            print(f"[STARTUP] 开始从数据库加载文档...", flush=True)
            all_docs = await state_manager.get_all_documents()
            print(f"[STARTUP] 从数据库获取到 {len(all_docs)} 个文档", flush=True)
            logger.info(f"从数据库发现 {len(all_docs)} 个文档记录")

            # 转换为内存字典格式（保持兼容性）
            for i, doc in enumerate(all_docs):
                # 将Document对象转换为字典格式
                document_dict = {
                    "document_id": doc.document_id,
                    "file_name": doc.file_name,
                    "file_path": doc.file_path,
                    "file_size": doc.file_size,
                    "status": doc.status,
                    "created_at": doc.created_at,
                    "updated_at": doc.updated_at,
                    "task_id": doc.task_id,
                    "processing_time": doc.processing_time,
                    "content_length": doc.content_length,
                    "chunks_count": doc.chunks_count,
                    "rag_doc_id": doc.rag_doc_id,
                    "content_summary": doc.content_summary,
                    "error_message": doc.error_message,
                    "batch_operation_id": doc.batch_operation_id,
                    "parser_used": doc.parser_used,
                    "parser_reason": doc.parser_reason,
                    "content_hash": doc.content_hash
                }

                documents[doc.document_id] = document_dict

                # 打印前3个文档的详细信息
                if i < 3:
                    print(f"[STARTUP] 文档 {i+1}: ID={doc.document_id}, 文件名={doc.file_name}, 状态={doc.status}", flush=True)

                # 为已完成的文档创建对应的任务记录（如果需要）
                if doc.task_id and doc.status in ["completed", "failed"]:
                    task = {
                        "task_id": doc.task_id,
                        "document_id": doc.document_id,
                        "type": "process",
                        "status": doc.status,
                        "created_at": doc.created_at,
                        "completed_at": doc.updated_at,
                        "progress": 100 if doc.status == "completed" else 0,
                        "error_message": doc.error_message,
                        "message": f"文档已{doc.status}（从数据库恢复）"
                    }
                    tasks[doc.task_id] = task

            print(f"[STARTUP] ✅ 成功加载 {len(documents)} 个文档到内存字典", flush=True)
            print(f"[STARTUP] 内存documents字典keys: {list(documents.keys())[:5]}...", flush=True)
            logger.info(f"成功从数据库加载 {len(documents)} 个文档状态")
        else:
            print(f"[STARTUP] ⚠️ StateManager未初始化，无法加载文档", flush=True)
            logger.warning("StateManager未初始化，无法加载文档")

    except Exception as e:
        logger.error(f"从数据库加载文档状态失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        logger.info("将使用空的文档列表启动")

    # 备用方案：尝试从状态文件加载（如果数据库加载失败且documents为空）
    if not documents:
        api_state_file = os.path.join(TEMP_WORKING_DIR, "api_documents_state.json")
        if os.path.exists(api_state_file):
            try:
                with open(api_state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)

                documents = state_data.get("documents", {})
                tasks = state_data.get("tasks", {})
                batch_operations = state_data.get("batch_operations", {})

                logger.info(f"从备用状态文件加载了 {len(documents)} 个文档")
            except Exception as e:
                logger.error(f"加载备用状态文件失败: {str(e)}")

async def initialize_state_manager():
    """初始化状态管理器"""
    global state_manager

    logger.info("🔧 初始化状态管理器")

    if state_manager is not None:
        logger.info("✅ 状态管理器已存在，直接返回")
        return state_manager

    try:
        state_manager = StateManager()
        logger.info("✅ 状态管理器初始化成功")
        return state_manager
    except Exception as e:
        logger.error(f"状态管理器初始化失败: {str(e)}")
        raise

async def safe_update_document_status(document_id: str, status: str, **kwargs):
    """安全更新文档状态的辅助函数"""
    try:
        if state_manager:
            await state_manager.update_document_status(document_id, status, **kwargs)
        else:
            logger.warning(f"状态管理器未初始化，无法更新文档状态: {document_id}")
    except Exception as e:
        logger.warning(f"更新文档状态失败 {document_id}: {str(e)}")

async def safe_get_document(document_id: str) -> Optional[Document]:
    """安全获取文档的辅助函数"""
    try:
        if state_manager:
            return await state_manager.get_document(document_id)
        else:
            logger.warning(f"状态管理器未初始化，无法获取文档: {document_id}")
            return None
    except Exception as e:
        logger.warning(f"获取文档失败 {document_id}: {str(e)}")
        return None

async def safe_get_all_documents() -> List[Document]:
    """安全获取所有文档的辅助函数"""
    try:
        if state_manager:
            return await state_manager.get_all_documents()
        else:
            logger.warning("状态管理器未初始化，无法获取所有文档")
            return []
    except Exception as e:
        logger.warning(f"获取所有文档失败: {str(e)}")
        return []

async def safe_find_documents_by_filename(filename: str) -> List[Document]:
    """按文件名查找文档的辅助函数"""
    try:
        all_docs = await safe_get_all_documents()
        return [doc for doc in all_docs if doc.file_name == filename]
    except Exception as e:
        logger.warning(f"按文件名查找文档失败: {str(e)}")
        return []

async def initialize_multimodal_handler():
    """初始化多模态处理器"""
    global multimodal_handler

    logger.info("🔧 initialize_multimodal_handler() 被调用")

    if multimodal_handler is not None:
        logger.info("✅ 多模态处理器已存在，直接返回")
        return multimodal_handler

    logger.info("🚀 开始初始化新的多模态处理器")

    try:
        # 初始化多模态缓存管理器
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        postgres_config = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", 5432)),
            "database": os.getenv("POSTGRES_DB", "raganything"),
            "user": os.getenv("POSTGRES_USER", "raganything_user"),
            "password": os.getenv("POSTGRES_PASSWORD")
        }

        multimodal_cache_manager = CacheManager(
            redis_url=redis_url,
            postgres_config=postgres_config,
            enable_memory_cache=True,
            memory_cache_size=100
        )

        await multimodal_cache_manager.initialize()
        logger.info("✅ 多模态缓存管理器初始化成功")

        # 初始化多模态API处理器
        multimodal_handler = MultimodalAPIHandler(
            rag_instance=rag_instance,
            cache_manager=multimodal_cache_manager
        )

        await multimodal_handler.initialize()
        logger.info("✅ 多模态处理器初始化成功")

        return multimodal_handler

    except Exception as e:
        logger.error(f"多模态处理器初始化失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def initialize_rag():
    """初始化RAG系统和缓存增强处理器"""
    global rag_instance, cache_enhanced_processor, parallel_batch_processor

    logger.info("🔧 initialize_rag() 被调用")

    if rag_instance is not None:
        logger.info("✅ RAG实例已存在，直接返回")
        return rag_instance
    
    logger.info("🚀 开始初始化新的RAG实例")
    
    try:
        # 检查环境变量
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_BINDING_API_KEY")
        if not api_key:
            logger.error("未找到DEEPSEEK_API_KEY，请检查环境变量")
            return None
        
        base_url = os.getenv("LLM_BINDING_HOST", "https://api.deepseek.com/v1")
        
        # 创建配置 - 启用缓存和确保工作目录一致
        config = RAGAnythingConfig(
            working_dir=WORKING_DIR,
            parser_output_dir=OUTPUT_DIR,
            parser=os.getenv("PARSER", "mineru"),
            parse_method=os.getenv("PARSE_METHOD", "auto"),
            enable_image_processing=True,
            enable_table_processing=True,
            enable_equation_processing=True,
        )
        
        # 定义LLM函数
        def llm_model_func(prompt, system_prompt=None, history_messages=[], **kwargs):
            return openai_complete_if_cache(
                "deepseek-chat",
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                api_key=api_key,
                base_url=base_url,
                **kwargs,
            )
        
        # 定义视觉模型函数
        def vision_model_func(
            prompt,
            system_prompt=None,
            history_messages=[],
            image_data=None,
            messages=None,
            **kwargs,
        ):
            if messages:
                return openai_complete_if_cache(
                    "deepseek-vl",
                    "",
                    system_prompt=None,
                    history_messages=[],
                    messages=messages,
                    api_key=api_key,
                    base_url=base_url,
                    **kwargs,
                )
            elif image_data:
                return openai_complete_if_cache(
                    "deepseek-vl",
                    "",
                    system_prompt=None,
                    history_messages=[],
                    messages=[
                        {"role": "system", "content": system_prompt} if system_prompt else None,
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                                },
                            ],
                        } if image_data else {"role": "user", "content": prompt},
                    ],
                    api_key=api_key,
                    base_url=base_url,
                    **kwargs,
                )
            else:
                return llm_model_func(prompt, system_prompt, history_messages, **kwargs)
        
        # 定义嵌入函数
        embedding_func = EmbeddingFunc(
            embedding_dim=1024,
            max_token_size=512,
            func=qwen_embed,
        )
        
        # 配置数据库集成
        db_config = load_database_config()
        lightrag_kwargs = create_lightrag_kwargs(db_config)
        
        # 保持原有缓存设置的兼容性
        if "enable_llm_cache" not in lightrag_kwargs:
            lightrag_kwargs["enable_llm_cache"] = os.getenv("ENABLE_LLM_CACHE", "true").lower() == "true"
        
        logger.info(f"数据库集成配置: 存储模式={db_config.storage_mode}, 缓存={db_config.enable_caching}")
        if db_config.storage_mode in ["hybrid", "postgres_only"]:
            logger.info(f"PostgreSQL: {db_config.postgres_host}:{db_config.postgres_port}/{db_config.postgres_db}")
        if db_config.storage_mode in ["hybrid", "neo4j_only"]:
            logger.info(f"Neo4j: {db_config.neo4j_uri}/{db_config.neo4j_database}")
        
        # 初始化RAGAnything
        rag_instance = RAGAnything(
            config=config,
            llm_model_func=llm_model_func,
            vision_model_func=vision_model_func,
            embedding_func=embedding_func,
            lightrag_kwargs=lightrag_kwargs,
        )
        
        # 确保LightRAG实例已初始化
        await rag_instance._ensure_lightrag_initialized()
        
        # 初始化缓存统计跟踪
        initialize_cache_tracking(WORKING_DIR)
        
        # 创建缓存增强处理器
        cache_enhanced_processor = CacheEnhancedProcessor(
            rag_instance=rag_instance,
            storage_dir=WORKING_DIR
        )

        # 创建并行批量处理器
        max_workers = int(os.getenv("MAX_CONCURRENT_PROCESSING", "3"))
        parallel_batch_processor = ParallelBatchProcessor(
            rag_instance=rag_instance,
            max_workers=max_workers
        )

        logger.info("RAG系统初始化成功")
        logger.info(f"数据目录: {WORKING_DIR}")
        logger.info(f"输出目录: {OUTPUT_DIR}")
        logger.info(f"RAGAnything工作目录: {rag_instance.working_dir}")
        logger.info(f"LLM: DeepSeek API")
        logger.info(f"嵌入: 本地Qwen3-Embedding-0.6B")
        logger.info(f"缓存配置: Parse Cache={os.getenv('ENABLE_PARSE_CACHE', 'true')}, LLM Cache={os.getenv('ENABLE_LLM_CACHE', 'true')}")
        
        # 验证目录一致性
        if rag_instance.working_dir != WORKING_DIR:
            logger.warning(f"工作目录不一致! API服务器: {WORKING_DIR}, RAGAnything: {rag_instance.working_dir}")
        else:
            logger.info("✓ 工作目录配置一致")
        
        return rag_instance
        
    except Exception as e:
        logger.error(f"RAG系统初始化失败: {str(e)}")
        logger.error(f"初始化错误详情: {type(e).__name__}")
        import traceback
        logger.error(f"完整错误堆栈: {traceback.format_exc()}")
        
        # 检查关键环境变量
        env_check = {
            "DEEPSEEK_API_KEY": bool(os.getenv("DEEPSEEK_API_KEY")),
            "LLM_BINDING_API_KEY": bool(os.getenv("LLM_BINDING_API_KEY")),
            "NEO4J_USERNAME": os.getenv("NEO4J_USERNAME"),
            "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
            "POSTGRES_USER": os.getenv("POSTGRES_USER"),
            "POSTGRES_DB": os.getenv("POSTGRES_DB"),
        }
        logger.error(f"环境变量检查: {env_check}")
        
        return None

@app.get("/health")
async def health_check():
    """健康检查端点"""
    global rag_instance
    
    rag_status = "healthy" if rag_instance is not None else "unhealthy"
    
    return {
        "status": "healthy" if rag_status == "healthy" else "degraded",
        "message": "RAG-Anything API is running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "rag_engine": rag_status,
            "tasks": "healthy",
            "documents": "healthy"
        },
        "statistics": {
            "active_tasks": len([t for t in tasks.values() if t["status"] == "running"]),
            "total_tasks": len(tasks),
            "total_documents": len(documents)
        },
        "system_checks": {
            "api": True,
            "websocket": True,
            "storage": True,
            "rag_initialized": rag_instance is not None
        }
    }

def get_rag_statistics():
    """获取RAG系统统计信息"""
    try:
        stats = {
            "documents_processed": len(documents),
            "entities_count": 0,
            "relationships_count": 0,
            "chunks_count": 0
        }
        
        # 尝试从RAG存储文件中读取统计信息
        try:
            # 读取实体数量
            entities_file = os.path.join(WORKING_DIR, "vdb_entities.json")
            if os.path.exists(entities_file):
                with open(entities_file, 'r', encoding='utf-8') as f:
                    entities_data = json.load(f)
                    stats["entities_count"] = len(entities_data.get("data", []))
            
            # 读取关系数量
            relationships_file = os.path.join(WORKING_DIR, "vdb_relationships.json")
            if os.path.exists(relationships_file):
                with open(relationships_file, 'r', encoding='utf-8') as f:
                    relationships_data = json.load(f)
                    stats["relationships_count"] = len(relationships_data.get("data", []))
            
            # 读取chunks数量
            chunks_file = os.path.join(WORKING_DIR, "vdb_chunks.json")
            if os.path.exists(chunks_file):
                with open(chunks_file, 'r', encoding='utf-8') as f:
                    chunks_data = json.load(f)
                    stats["chunks_count"] = len(chunks_data.get("data", []))
                    
        except Exception as e:
            logger.error(f"读取RAG统计信息失败: {e}")
        
        return stats
        
    except Exception as e:
        logger.error(f"获取RAG统计信息失败: {e}")
        return {
            "documents_processed": len(documents),
            "entities_count": 0,
            "relationships_count": 0,
            "chunks_count": 0
        }

def get_content_stats_from_output(file_path: str, output_dir: str) -> Optional[Dict[str, int]]:
    """从MinerU/Docling输出文件中获取内容统计信息"""
    try:
        # 构建输出文件路径
        file_stem = Path(file_path).stem
        
        # 尝试不同的可能路径，包括更多模式
        possible_paths = [
            os.path.join(output_dir, file_stem, "auto", f"{file_stem}_content_list.json"),
            os.path.join(output_dir, file_stem, f"{file_stem}_content_list.json"),
            os.path.join(output_dir, f"{file_stem}_content_list.json"),
            # 尝试在子目录中查找
            os.path.join(output_dir, file_stem, "content_list.json"),
            os.path.join(output_dir, "content_list.json"),
        ]
        
        content_list_file = None
        for path in possible_paths:
            if os.path.exists(path):
                content_list_file = path
                logger.debug(f"找到content_list文件: {path}")
                break
        
        if not content_list_file:
            # 尝试递归搜索content_list.json文件
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    if file.endswith("_content_list.json") or file == "content_list.json":
                        if file_stem in file or file_stem in root:
                            content_list_file = os.path.join(root, file)
                            logger.debug(f"递归找到content_list文件: {content_list_file}")
                            break
                if content_list_file:
                    break
        
        if not content_list_file:
            logger.warning(f"找不到content_list文件: {file_stem}")
            logger.debug(f"搜索路径: {possible_paths}")
            # 列出输出目录内容以便调试
            try:
                if os.path.exists(output_dir):
                    logger.debug(f"输出目录内容: {os.listdir(output_dir)}")
            except Exception as e:
                logger.debug(f"无法列出输出目录: {e}")
            return None
        
        # 读取并统计内容
        with open(content_list_file, 'r', encoding='utf-8') as f:
            content_list = json.load(f)
        
        stats = {
            'total': len(content_list),
            'text': 0,
            'image': 0,
            'table': 0,
            'equation': 0,
            'other': 0
        }
        
        for item in content_list:
            if isinstance(item, dict):
                item_type = item.get('type', 'unknown')
                if item_type == 'text':
                    stats['text'] += 1
                elif item_type == 'image':
                    stats['image'] += 1
                elif item_type == 'table':
                    stats['table'] += 1
                elif item_type in ['equation', 'formula']:
                    stats['equation'] += 1
                else:
                    stats['other'] += 1
        
        logger.info(f"内容统计 ({file_stem}): {stats} (来源: {content_list_file})")
        return stats
        
    except Exception as e:
        logger.error(f"读取内容统计失败: {e}")
        import traceback
        logger.debug(f"详细错误: {traceback.format_exc()}")
        return None

def get_system_metrics():
    """获取系统指标"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # 尝试获取GPU使用率（如果可用）
        gpu_usage = 0
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu_usage = gpus[0].load * 100
        except ImportError:
            gpu_usage = 0
        
        return {
            "cpu_usage": round(cpu_percent, 1),
            "memory_usage": round(memory.percent, 1),
            "disk_usage": round(disk.percent, 1),
            "gpu_usage": round(gpu_usage, 1)
        }
    except Exception as e:
        logger.error(f"获取系统指标失败: {e}")
        return {
            "cpu_usage": 0,
            "memory_usage": 0,
            "disk_usage": 0,
            "gpu_usage": 0
        }

@app.get("/api/system/status")
async def get_system_status():
    """系统状态端点"""
    global rag_instance
    
    metrics = get_system_metrics()
    
    return {
        "success": True,
        "status": "healthy" if rag_instance else "degraded",
        "timestamp": datetime.now().isoformat(),
        "metrics": metrics,
        "processing_stats": get_rag_statistics(),
        "services": {
            "RAG-Anything Core": {
                "status": "running" if rag_instance else "stopped",
                "uptime": "实时运行"
            },
            "Document Parser": {
                "status": "running" if rag_instance else "stopped", 
                "uptime": "实时运行"
            },
            "Query Engine": {
                "status": "running" if rag_instance else "stopped",
                "uptime": "实时运行"
            },
            "Knowledge Graph": {
                "status": "running" if rag_instance else "stopped",
                "uptime": "实时运行"
            }
        }
    }

@app.get("/api/system/parser-stats")
async def get_parser_statistics():
    """获取解析器使用统计"""
    global rag_instance
    
    routing_stats = router.get_routing_stats()
    text_processing_stats = text_processor.get_processing_stats()
    
    # 计算解析器性能指标
    total_routed = routing_stats.get("total_routed", 0)
    parser_usage = routing_stats.get("parser_usage", {})
    category_dist = routing_stats.get("category_distribution", {})
    
    return {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "routing_statistics": {
            "total_files_routed": total_routed,
            "parser_usage": parser_usage,
            "category_distribution": category_dist,
            "efficiency_metrics": {
                "direct_text_processing": parser_usage.get("direct_text", 0),
                "avoided_conversions": parser_usage.get("direct_text", 0),
                "conversion_rate": round(parser_usage.get("direct_text", 0) / max(total_routed, 1) * 100, 1)
            }
        },
        "text_processing_statistics": text_processing_stats,
        "parser_availability": {
            "mineru": router.validate_parser_availability("mineru"),
            "docling": router.validate_parser_availability("docling"), 
            "direct_text": router.validate_parser_availability("direct_text")
        },
        "optimization_summary": {
            "total_optimizations": parser_usage.get("direct_text", 0) + parser_usage.get("docling", 0),
            "pdf_conversions_avoided": parser_usage.get("direct_text", 0),
            "libreoffice_conversions_avoided": sum(1 for doc in documents.values() 
                                                   if doc.get("parser_used", "").startswith("docling") 
                                                   and any(ext in doc.get("file_name", "") 
                                                          for ext in [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"]))
        }
    }

async def process_text_file_direct(task_id: str, file_path: str):
    """直接处理文本文件，避免PDF转换"""
    if task_id not in tasks:
        return
        
    task = tasks[task_id]
    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat()
    
    # 初始化时间变量，确保在所有异常处理中都能访问
    start_time = datetime.now()
    processing_start_time = datetime.now()
    
    # 更新文档状态
    if task["document_id"] in documents:
        documents[task["document_id"]]["status"] = "processing"
    
    try:
        # 获取RAG实例
        rag = await initialize_rag()
        if not rag:
            logger.error("RAG实例获取失败，initialize_rag()返回None")
            logger.error("这通常意味着:")
            logger.error("1. 环境变量配置问题（API密钥、数据库连接）")
            logger.error("2. LightRAG初始化失败（存储组件问题）")
            logger.error("3. 依赖组件不可用（PostgreSQL、Neo4j）")
            raise Exception("RAG系统未初始化")
        
        # 创建详细状态跟踪
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        detailed_status = detailed_tracker.create_status(
            task_id=task_id,
            file_name=os.path.basename(file_path),
            file_size=file_size,
            parser_used="direct_text",
            parser_reason="文本文件直接解析，避免PDF转换"
        )
        
        # 添加状态变更回调
        detailed_tracker.add_status_callback(task_id, lambda status: send_detailed_status_update(task_id, status))
        
        logger.info(f"开始直接处理文本文件: {file_path}")
        await send_processing_log(f"📝 开始直接处理文本文件 (跳过PDF转换)", "info")
        
        # 开始解析阶段
        detailed_status.start_stage(ProcessingStage.PARSING, 1, "直接解析文本文件")
        await send_processing_log(f"⚡ 使用优化路径直接解析文本内容...", "info")
        
        # 更新传统任务状态（保持兼容性）
        task["stage"] = "parsing"
        task["stage_details"]["parsing"]["status"] = "running"
        task["progress"] = 10
        await send_websocket_update(task_id, task)
        
        # 使用直接文本处理器
        content_list = text_processor.process_text_file(file_path, OUTPUT_DIR)
        
        # 更新内容统计
        detailed_status.content_stats.update_from_content_list(content_list)
        detailed_status.complete_stage(ProcessingStage.PARSING)
        detailed_status.add_log("SUCCESS", f"解析完成！提取了 {len(content_list)} 个内容块")
        await send_processing_log(f"✅ 文本解析完成！提取了 {len(content_list)} 个内容块", "success")
        
        # 更新传统任务状态
        task["stage_details"]["parsing"]["status"] = "completed"
        task["progress"] = 30
        await send_websocket_update(task_id, task)
        
        # 开始文本插入阶段
        detailed_status.start_stage(ProcessingStage.TEXT_PROCESSING, len(content_list), "插入文本内容到知识图谱")
        await send_processing_log(f"📝 开始插入 {len(content_list)} 个内容块到知识图谱...", "info")
        
        task["stage"] = "text_insert"
        task["stage_details"]["text_insert"]["status"] = "running"
        task["progress"] = 50
        await send_websocket_update(task_id, task)
        
        # 调用RAG的内容插入方法
        doc_id = await rag.insert_content_list(content_list, file_path)
        if doc_id is None:
            raise Exception("RAG内容插入失败：返回的文档ID为空")
        await send_processing_log(f"✅ 内容插入完成，文档ID: {doc_id[:12]}...", "success")
        
        # 完成文本处理
        detailed_status.complete_stage(ProcessingStage.TEXT_PROCESSING)
        
        # 开始知识图谱构建
        detailed_status.start_stage(ProcessingStage.GRAPH_BUILDING, 1, "构建知识图谱")
        await send_processing_log(f"🕸️  开始构建知识图谱，提取实体和关系...", "info")
        
        # 快速完成其他阶段（文本文件无需图片、表格、公式处理）
        stages_to_complete = [
            ("text_insert", "文本插入", 70),
            ("graph_build", "知识图谱构建", 90),
            ("indexing", "索引创建", 100),
        ]
        
        for stage_name, stage_label, progress in stages_to_complete:
            if task_id not in tasks:
                return
                
            task["stage"] = stage_name
            task["stage_details"][stage_name]["status"] = "completed"
            task["stage_details"][stage_name]["progress"] = 100
            task["progress"] = progress
            task["updated_at"] = datetime.now().isoformat()
            
            await send_websocket_update(task_id, task)
            await asyncio.sleep(0.1)
        
        # 完成知识图谱构建和索引
        detailed_status.complete_stage(ProcessingStage.GRAPH_BUILDING)
        await send_processing_log(f"✅ 知识图谱构建完成", "success")
        
        detailed_status.start_stage(ProcessingStage.INDEXING, 1, "创建搜索索引")
        await send_processing_log(f"🗂️  创建搜索索引...", "info")
        detailed_status.complete_stage(ProcessingStage.INDEXING)
        await send_processing_log(f"✅ 搜索索引创建完成", "success")
        
        # 完成整个处理过程
        detailed_status.complete_processing()
        await send_processing_log(f"🎉 文本文件处理全部完成！", "success")
        
        # 完成处理
        task["status"] = "completed"
        task["progress"] = 100
        task["completed_at"] = datetime.now().isoformat()
        task["multimodal_stats"]["processing_success_rate"] = 100.0
        task["multimodal_stats"]["text_chunks"] = len(content_list)
        
        # 更新文档状态
        if task["document_id"] in documents:
            documents[task["document_id"]]["status"] = "completed"
            documents[task["document_id"]]["updated_at"] = datetime.now().isoformat()
            documents[task["document_id"]]["processing_time"] = (
                datetime.fromisoformat(task["completed_at"]) - 
                datetime.fromisoformat(task["started_at"])
            ).total_seconds()
            documents[task["document_id"]]["chunks_count"] = len(content_list)
            documents[task["document_id"]]["rag_doc_id"] = doc_id
        
        logger.info(f"直接文本处理完成: {file_path}, {len(content_list)}个内容块")
    
    except Exception as e:
        await send_processing_log(f"❌ 直接文本处理失败: {str(e)}", "error")
        logger.error(f"直接文本处理失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 设置详细状态错误
        if detailed_tracker.get_status(task_id):
            detailed_status = detailed_tracker.get_status(task_id)
            detailed_status.set_error(str(e))
        
        task["status"] = "failed"
        task["error_message"] = str(e)
        task["completed_at"] = datetime.now().isoformat()  # 确保设置completed_at
        task["updated_at"] = datetime.now().isoformat()

        if task["document_id"] in documents:
            documents[task["document_id"]]["status"] = "failed"
            documents[task["document_id"]]["error_message"] = str(e)
            documents[task["document_id"]]["updated_at"] = datetime.now().isoformat()
            # 计算处理时间（即使失败）
            processing_time = None
            if "started_at" in task and "completed_at" in task:
                processing_time = (
                    datetime.fromisoformat(task["completed_at"]) -
                    datetime.fromisoformat(task["started_at"])
                ).total_seconds()
                documents[task["document_id"]]["processing_time"] = processing_time

            # Update database status through state manager
            await safe_update_document_status(
                task["document_id"],
                "failed",
                error_message=str(e),
                processing_time=processing_time
            )
    
    finally:
        # 清理状态跟踪
        detailed_tracker.remove_status(task_id)
    
    # 发送最终更新
    await send_websocket_update(task_id, task)

async def process_with_parser(task_id: str, file_path: str, parser_config):
    """使用指定解析器处理文档"""
    if task_id not in tasks:
        return
        
    task = tasks[task_id]
    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat()
    
    # 初始化时间变量，确保在所有异常处理中都能访问
    start_time = datetime.now()
    processing_start_time = datetime.now()
    
    # 更新文档状态
    if task["document_id"] in documents:
        documents[task["document_id"]]["status"] = "processing"
    
    try:
        # 获取RAG实例
        rag = await initialize_rag()
        if not rag:
            logger.error("RAG实例获取失败，initialize_rag()返回None")
            logger.error("这通常意味着:")
            logger.error("1. 环境变量配置问题（API密钥、数据库连接）")
            logger.error("2. LightRAG初始化失败（存储组件问题）")
            logger.error("3. 依赖组件不可用（PostgreSQL、Neo4j）")
            raise Exception("RAG系统未初始化")
        
        # 创建详细状态跟踪
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        detailed_status = detailed_tracker.create_status(
            task_id=task_id,
            file_name=os.path.basename(file_path),
            file_size=file_size,
            parser_used=parser_config.parser,
            parser_reason=parser_config.reason
        )
        
        # 添加状态变更回调
        detailed_tracker.add_status_callback(task_id, lambda status: send_detailed_status_update(task_id, status))
        
        logger.info(f"开始处理文档: {file_path}, 使用解析器: {parser_config.parser}")
        
        # 发送开始处理日志
        await send_processing_log(f"🚀 开始处理文档: {os.path.basename(file_path)}", "info")
        await send_processing_log(f"📄 文件大小: {file_size/1024:.1f} KB", "info")
        await send_processing_log(f"⚙️  解析器: {parser_config.parser} ({parser_config.reason})", "info")
        await send_processing_log(f"🎯 解析方法: {parser_config.method}", "info")
        
        # 开始解析阶段
        detailed_status.start_stage(ProcessingStage.PARSING, 1, f"使用{parser_config.parser}解析器处理文档")
        await send_processing_log(f"🔧 开始文档解析阶段...", "info")
        
        # 更新传统任务状态
        task["stage"] = "parsing"
        task["stage_details"]["parsing"]["status"] = "running"
        task["progress"] = 10
        await send_websocket_update(task_id, task)
        
        # 临时更新RAG配置使用指定解析器
        original_parser = rag.config.parser
        rag.config.parser = parser_config.parser
        
        try:
            # 处理.doc文件的特殊情况：先转换为.docx再用Docling处理
            actual_file_path = file_path
            temp_converted_file = None
            
            if parser_config.parser == "docling" and Path(file_path).suffix.lower() == ".doc":
                await send_processing_log(f"🔄 检测到.doc文件，使用LibreOffice转换为.docx...", "info")
                
                import tempfile
                import subprocess
                import platform
                import shutil
                
                # 创建临时转换文件
                temp_dir = Path(tempfile.mkdtemp())
                file_stem = Path(file_path).stem
                
                try:
                    # 使用LibreOffice转换.doc为.docx
                    convert_cmd = [
                        "libreoffice",
                        "--headless", 
                        "--convert-to",
                        "docx",
                        "--outdir",
                        str(temp_dir),
                        str(file_path)
                    ]
                    
                    convert_subprocess_kwargs = {
                        "capture_output": True,
                        "text": True,
                        "timeout": 60,
                        "encoding": "utf-8",
                        "errors": "ignore",
                    }
                    
                    if platform.system() == "Windows":
                        convert_subprocess_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    
                    result = subprocess.run(convert_cmd, **convert_subprocess_kwargs)
                    
                    if result.returncode != 0:
                        raise RuntimeError(f"LibreOffice转换失败: {result.stderr}")
                    
                    # 查找生成的.docx文件
                    docx_files = list(temp_dir.glob("*.docx"))
                    if not docx_files:
                        raise RuntimeError("LibreOffice转换失败：未生成.docx文件")
                    
                    temp_docx_path = docx_files[0]
                    
                    # 复制转换后的文件到上传目录，保持原始文件名
                    converted_file_path = Path(file_path).parent / f"{file_stem}_converted.docx"
                    shutil.copy2(temp_docx_path, converted_file_path)
                    
                    actual_file_path = str(converted_file_path)
                    temp_converted_file = converted_file_path
                    
                    await send_processing_log(f"✅ LibreOffice转换完成: {temp_docx_path.stat().st_size} bytes", "success")
                    
                except Exception as e:
                    # 清理临时目录
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    raise RuntimeError(f"LibreOffice转换过程出错: {str(e)}")
                finally:
                    # 清理临时目录
                    shutil.rmtree(temp_dir, ignore_errors=True)
            
            # 调用RAGAnything处理文档（使用实际的文件路径）
            await send_processing_log(f"🔄 调用RAG处理引擎开始解析文档...", "info")
            device_type = "cuda" if TORCH_AVAILABLE and torch.cuda.is_available() else "cpu"
            await send_processing_log(f"🖥️  计算设备: {device_type.upper()}", "info")
            
            # Use original processing start time for total processing duration
            # processing_start_time = datetime.now()  # Removed to fix variable scope error
            await rag.process_document_complete(
                file_path=actual_file_path, 
                output_dir=OUTPUT_DIR,
                parse_method=parser_config.method,
                device=device_type,
                lang="en"  # 使用英文语言配置，MinerU不支持"auto"
            )
            
            # 清理转换的临时文件
            if temp_converted_file and temp_converted_file.exists():
                try:
                    temp_converted_file.unlink()
                    await send_processing_log(f"🧹 清理临时转换文件", "info")
                except Exception:
                    pass  # 忽略清理错误
            
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            await send_processing_log(f"✅ 文档解析完成！总耗时: {processing_time:.2f}秒", "success")
            
            # 尝试获取解析结果来更新内容统计
            try:
                await send_processing_log(f"📊 分析解析结果，提取内容统计信息...", "info")
                # 等待一小段时间确保文件写入完成
                await asyncio.sleep(1)
                
                # 尝试从输出文件读取准确的内容统计（使用原始文件名）
                content_stats = get_content_stats_from_output(file_path, OUTPUT_DIR)
                
                if content_stats:
                    await send_processing_log(f"📈 内容统计完成: 总计{content_stats['total']}个内容块", "success")
                    await send_processing_log(f"📝 文本块: {content_stats['text']}个", "info")
                    await send_processing_log(f"🖼️  图片块: {content_stats['image']}个", "info")
                    await send_processing_log(f"📊 表格块: {content_stats['table']}个", "info")
                    await send_processing_log(f"🧮 公式块: {content_stats.get('equation', 0)}个", "info")
                    
                    # 更新详细状态的内容统计
                    detailed_status.content_stats.total_blocks = content_stats['total']
                    detailed_status.content_stats.text_blocks = content_stats['text']
                    detailed_status.content_stats.image_blocks = content_stats['image']
                    detailed_status.content_stats.table_blocks = content_stats['table']
                    detailed_status.content_stats.equation_blocks = content_stats.get('equation', 0)
                    detailed_status.content_stats.other_blocks = content_stats.get('other', 0)
                    
                    # 更新任务的多模态统计
                    if task_id in tasks:
                        tasks[task_id]["multimodal_stats"]["text_chunks"] = content_stats['text']
                        tasks[task_id]["multimodal_stats"]["images_count"] = content_stats['image']
                        tasks[task_id]["multimodal_stats"]["images_processed"] = content_stats['image']
                        tasks[task_id]["multimodal_stats"]["tables_count"] = content_stats['table']
                        tasks[task_id]["multimodal_stats"]["tables_processed"] = content_stats['table']
                        tasks[task_id]["multimodal_stats"]["equations_count"] = content_stats.get('equation', 0)
                        tasks[task_id]["multimodal_stats"]["equations_processed"] = content_stats.get('equation', 0)
                        tasks[task_id]["multimodal_stats"]["processing_success_rate"] = 100.0
                        
                        # 立即发送更新以反映新的统计信息
                        await send_websocket_update(task_id, tasks[task_id])
                    
                    detailed_status.add_log("SUCCESS", f"解析统计: 总计{content_stats['total']}块 (文本:{content_stats['text']}, 图片:{content_stats['image']}, 表格:{content_stats['table']})")
                    
                    # 通知详细状态更新
                    await send_detailed_status_update(task_id, detailed_status.to_dict())
                else:
                    await send_processing_log("⚠️  无法获取详细的内容统计信息", "warning")
                    detailed_status.add_log("WARNING", "无法获取详细的内容统计信息")
                                
            except Exception as e:
                logger.warning(f"获取解析结果统计失败: {e}")
                detailed_status.add_log("WARNING", f"统计信息获取失败: {str(e)}")
            
            # 完成解析阶段
            detailed_status.complete_stage(ProcessingStage.PARSING)
            detailed_status.add_log("SUCCESS", f"使用{parser_config.parser}解析完成，提取了内容块")
            
        finally:
            # 恢复原始解析器配置
            rag.config.parser = original_parser
        
        # 开始后续处理阶段
        await send_processing_log(f"🔍 开始内容分析阶段...", "info")
        detailed_status.start_stage(ProcessingStage.CONTENT_ANALYSIS, 1, "分析文档内容")
        detailed_status.complete_stage(ProcessingStage.CONTENT_ANALYSIS)
        await send_processing_log(f"✅ 内容分析完成", "success")
        
        await send_processing_log(f"📝 开始文本处理阶段...", "info")
        detailed_status.start_stage(ProcessingStage.TEXT_PROCESSING, 1, "处理文本内容")
        detailed_status.complete_stage(ProcessingStage.TEXT_PROCESSING)
        await send_processing_log(f"✅ 文本处理完成", "success")
        
        await send_processing_log(f"🕸️  开始构建知识图谱...", "info")
        detailed_status.start_stage(ProcessingStage.GRAPH_BUILDING, 1, "构建知识图谱")
        await send_processing_log(f"🧠 提取实体和关系中...", "info")
        detailed_status.complete_stage(ProcessingStage.GRAPH_BUILDING)
        await send_processing_log(f"✅ 知识图谱构建完成", "success")
        
        await send_processing_log(f"🗂️  开始创建搜索索引...", "info")
        detailed_status.start_stage(ProcessingStage.INDEXING, 1, "创建搜索索引")
        detailed_status.complete_stage(ProcessingStage.INDEXING)
        await send_processing_log(f"✅ 搜索索引创建完成", "success")
        
        # 完成整个处理过程
        detailed_status.complete_processing()
        await send_processing_log(f"🎉 文档处理全部完成！文档已成功添加到知识库", "success")
        
        # 逐步更新处理进度（保持兼容性）
        stages_progress = [
            ("parsing", "文档解析", 20),
            ("separation", "内容分离", 30), 
            ("text_insert", "文本插入", 50),
            ("image_process", "图片处理", 70),
            ("table_process", "表格处理", 80),
            ("equation_process", "公式处理", 90),
            ("graph_build", "知识图谱构建", 95),
            ("indexing", "索引创建", 100),
        ]
        
        for stage_name, stage_label, progress in stages_progress:
            if task_id not in tasks:  # 任务可能被取消
                return
                
            task["stage"] = stage_name
            task["stage_details"][stage_name]["status"] = "completed"
            task["stage_details"][stage_name]["progress"] = 100
            task["progress"] = progress
            task["updated_at"] = datetime.now().isoformat()
            
            await send_websocket_update(task_id, task)
            await asyncio.sleep(0.2)  # 短暂延迟以显示进度
        
        # 完成处理
        task["status"] = "completed"
        task["progress"] = 100
        task["completed_at"] = datetime.now().isoformat()
        task["multimodal_stats"]["processing_success_rate"] = 100.0
        
        # 更新文档状态
        if task["document_id"] in documents:
            documents[task["document_id"]]["status"] = "completed"
            documents[task["document_id"]]["updated_at"] = datetime.now().isoformat()

            # 获取实际处理结果统计
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            processing_time = (
                datetime.fromisoformat(task["completed_at"]) -
                datetime.fromisoformat(task["started_at"])
            ).total_seconds()
            documents[task["document_id"]]["processing_time"] = processing_time
            documents[task["document_id"]]["content_length"] = file_size
            documents[task["document_id"]]["parser_used"] = f"{parser_config.parser}({parser_config.method})"
            documents[task["document_id"]]["parser_reason"] = parser_config.reason

            # Update database status through state manager
            await safe_update_document_status(
                task["document_id"],
                "completed",
                processing_time=processing_time,
                content_length=file_size,
                parser_used=f"{parser_config.parser}({parser_config.method})",
                parser_reason=parser_config.reason
            )
        
        logger.info(f"文档处理完成: {file_path}, 解析器: {parser_config.parser}")
    
    except Exception as e:
        await send_processing_log(f"❌ 文档处理失败: {str(e)}", "error")
        logger.error(f"文档处理失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 设置详细状态错误
        if detailed_tracker.get_status(task_id):
            detailed_status = detailed_tracker.get_status(task_id)
            detailed_status.set_error(str(e))
        
        task["status"] = "failed"
        task["error_message"] = str(e)
        task["completed_at"] = datetime.now().isoformat()  # 确保设置completed_at
        task["updated_at"] = datetime.now().isoformat()

        if task["document_id"] in documents:
            documents[task["document_id"]]["status"] = "failed"
            documents[task["document_id"]]["error_message"] = str(e)
            documents[task["document_id"]]["updated_at"] = datetime.now().isoformat()
            # 计算处理时间（即使失败）
            processing_time = None
            if "started_at" in task and "completed_at" in task:
                processing_time = (
                    datetime.fromisoformat(task["completed_at"]) -
                    datetime.fromisoformat(task["started_at"])
                ).total_seconds()
                documents[task["document_id"]]["processing_time"] = processing_time

            # Update database status through state manager
            await safe_update_document_status(
                task["document_id"],
                "failed",
                error_message=str(e),
                processing_time=processing_time
            )
    
    finally:
        # 清理状态跟踪
        detailed_tracker.remove_status(task_id)
    
    # 发送最终更新
    await send_websocket_update(task_id, task)

async def process_document_real(task_id: str, file_path: str):
    """智能文档处理过程，使用智能路由选择最优解析策略"""
    if task_id not in tasks:
        return
        
    task = tasks[task_id]
    
    try:
        # 获取文件信息
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_name = Path(file_path).name
        
        logger.info(f"开始智能路由文档: {file_name} ({file_size//1024}KB)")
        
        # 使用智能路由器选择最优解析策略
        parser_config = router.route_parser(file_path, file_size)
        
        # 验证解析器可用性
        if not router.validate_parser_availability(parser_config.parser):
            logger.warning(f"首选解析器 {parser_config.parser} 不可用，使用备用方案")
            parser_config = router.get_fallback_config(parser_config)
            
            # 再次验证备用解析器
            if not router.validate_parser_availability(parser_config.parser):
                raise Exception(f"所有解析器都不可用，请检查安装")
        
        # 记录解析器选择信息
        task["parser_info"] = {
            "selected_parser": parser_config.parser,
            "method": parser_config.method,
            "category": parser_config.category,
            "reason": parser_config.reason,
            "direct_processing": parser_config.direct_processing
        }
        
        # 根据解析策略选择处理方式
        if parser_config.direct_processing:
            logger.info(f"使用直接处理: {parser_config.reason}")
            await process_text_file_direct(task_id, file_path)
        else:
            logger.info(f"使用解析器处理: {parser_config.parser} - {parser_config.reason}")
            await process_with_parser(task_id, file_path, parser_config)
            
    except Exception as e:
        logger.error(f"智能路由处理失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 更新任务状态为失败
        if task_id in tasks:
            task["status"] = "failed"
            task["error_message"] = f"智能路由失败: {str(e)}"
            task["updated_at"] = datetime.now().isoformat()
            
            if task["document_id"] in documents:
                documents[task["document_id"]]["status"] = "failed"
                documents[task["document_id"]]["error_message"] = str(e)
                documents[task["document_id"]]["updated_at"] = datetime.now().isoformat()
            
            # 发送最终更新
            await send_websocket_update(task_id, task)

async def send_detailed_status_update(task_id: str, detailed_status: dict):
    """发送详细状态更新到WebSocket"""
    if task_id in active_websockets:
        try:
            # 检查详细状态是否为空
            if detailed_status is None:
                logger.warning(f"详细状态为空，跳过WebSocket更新: {task_id}")
                return
            
            # 发送详细状态信息
            status_message = {
                "type": "detailed_status",
                "task_id": task_id,
                "detailed_status": detailed_status
            }
            await active_websockets[task_id].send_text(json.dumps(status_message))
        except Exception as e:
            logger.error(f"发送详细状态更新失败: {e}")
            active_websockets.pop(task_id, None)

async def send_websocket_update(task_id: str, task: dict):
    """发送WebSocket更新"""
    if task_id in active_websockets:
        try:
            await active_websockets[task_id].send_text(json.dumps(task))
        except:
            active_websockets.pop(task_id, None)

async def send_processing_log(message: str, level: str = "info"):
    """立即发送处理日志到前端WebSocket客户端"""
    try:
        # 添加调试输出以确认函数被调用
        print(f"[DEBUG] send_processing_log called: {message} (level: {level})")
        print(f"[DEBUG] processing_log_websockets count: {len(processing_log_websockets)}")
        # 创建日志数据
        log_data = {
            "type": "log",
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "source": "api_processing"
        }
        
        # 立即发送到WebSocket客户端
        if processing_log_websockets:
            disconnected = []
            for ws in list(processing_log_websockets):
                try:
                    await ws.send_text(json.dumps(log_data))
                except Exception:
                    disconnected.append(ws)
            
            # 清理断开的连接
            for ws in disconnected:
                if ws in processing_log_websockets:
                    processing_log_websockets.remove(ws)
                
        # 同时发送到logger以确保shell端也能看到
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "success": logging.INFO
        }
        logger.log(level_map.get(level, logging.INFO), message)
        
        # 额外的调试：也尝试通过WebSocket handler发送
        if websocket_log_handler.websocket_clients:
            print(f"[DEBUG] Also sending via websocket_log_handler to {len(websocket_log_handler.websocket_clients)} clients")
            try:
                # 手动触发WebSocket handler
                log_record = logging.LogRecord(
                    name="send_processing_log",
                    level=level_map.get(level, logging.INFO),
                    pathname="",
                    lineno=0,
                    msg=message,
                    args=(),
                    exc_info=None
                )
                websocket_log_handler.emit(log_record)
            except Exception as e:
                print(f"[DEBUG] Failed to emit via websocket_log_handler: {e}")
        
    except Exception as e:
        print(f"Failed to send processing log: {e}")
        # 保底方案，至少确保shell端能看到
        logger.info(message)

@app.post("/api/v1/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """单文档上传端点 - 保持向后兼容"""
    # 检查文件名重复
    existing_docs = await safe_find_documents_by_filename(file.filename)
    if existing_docs:
        raise HTTPException(
            status_code=400,
            detail=f"文件名 '{file.filename}' 已存在，请重命名后再上传"
        )

    task_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())

    # 读取文件内容并计算哈希
    content = await file.read()
    import hashlib
    content_hash = hashlib.sha256(content).hexdigest()

    # 检查重复文件（基于哈希）
    if state_manager:
        existing_doc_id = await state_manager.check_duplicate_by_hash(content_hash)
        if existing_doc_id:
            raise HTTPException(
                status_code=400,
                detail=f"文件内容重复，已存在的文档ID: {existing_doc_id}"
            )

    # 保存上传的文件
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        buffer.write(content)

    # 获取实际文件大小（确保一致性）
    actual_file_size = os.path.getsize(file_path)
    
    # 创建任务记录
    task = {
        "task_id": task_id,
        "status": "pending",
        "stage": "parsing",
        "progress": 0,
        "file_path": file_path,
        "file_name": file.filename,
        "file_size": actual_file_size,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "document_id": document_id,
        "total_stages": len(PROCESSING_STAGES),
        "stage_details": {
            stage[0]: {
                "status": "pending",
                "progress": 0
            } for stage in PROCESSING_STAGES
        },
        "multimodal_stats": {
            "images_count": 0,
            "tables_count": 0,
            "equations_count": 0,
            "images_processed": 0,
            "tables_processed": 0,
            "equations_processed": 0,
            "processing_success_rate": 0.0,
            "text_chunks": 0,
            "knowledge_entities": 0,
            "knowledge_relationships": 0
        }
    }
    
    tasks[task_id] = task
    
    # 创建文档记录
    document = {
        "document_id": document_id,
        "file_name": file.filename,
        "file_path": file_path,
        "file_size": actual_file_size,
        "status": "uploaded",  # 改为uploaded状态，表示已上传但未解析
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "task_id": task_id
    }
    
    # 保存到数据库
    db_document = Document(
        document_id=document_id,
        file_name=file.filename,
        file_path=file_path,
        file_size=actual_file_size,
        status="uploaded",
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        task_id=task_id,
        content_hash=content_hash
    )

    if state_manager:
        try:
            await state_manager.add_document(db_document)
            logger.info(f"✅ 成功保存文档到数据库: {file.filename}")
        except Exception as e:
            logger.error(f"❌ 保存文档到数据库失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("⚠️ StateManager未初始化，无法保存到数据库")

    # 兼容性：同时保存到内存字典
    documents[document_id] = document
    # JSON文件保存已废弃，数据直接保存到数据库
    
    return {
        "success": True,
        "message": "Document uploaded successfully, ready for manual processing", 
        "task_id": task_id,
        "document_id": document_id,
        "file_name": file.filename,
        "file_size": actual_file_size,
        "status": "uploaded"
    }

@app.post("/api/v1/documents/upload/batch", response_model=BatchUploadResponse)
async def upload_documents_batch(files: List[UploadFile] = File(...)):
    """批量文档上传端点"""
    batch_operation_id = str(uuid.uuid4())
    uploaded_count = 0
    failed_count = 0
    results = []
    
    # 创建批量操作状态跟踪
    batch_operation = {
        "batch_operation_id": batch_operation_id,
        "operation_type": "upload",
        "status": "running",
        "total_items": len(files),
        "completed_items": 0,
        "failed_items": 0,
        "progress": 0.0,
        "started_at": datetime.now().isoformat(),
        "results": []
    }
    batch_operations[batch_operation_id] = batch_operation
    
    logger.info(f"开始批量上传 {len(files)} 个文件")
    await send_processing_log(f"📤 开始批量上传 {len(files)} 个文件", "info")
    
    # 支持的文件类型
    supported_extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls', '.txt', '.md', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif', '.webp']
    
    for i, file in enumerate(files):
        file_result = {
            "file_name": file.filename,
            "file_size": 0,
            "status": "failed",
            "message": "",
            "task_id": None,
            "document_id": None
        }
        
        try:
            # 文件类型验证
            file_extension = os.path.splitext(file.filename)[1].lower()
            if file_extension not in supported_extensions:
                file_result["message"] = f"不支持的文件类型: {file_extension}"
                failed_count += 1
                results.append(file_result)
                batch_operation["failed_items"] += 1
                continue
            
            # 检查文件大小（限制100MB）
            content = await file.read()
            file_size = len(content)
            if file_size > 100 * 1024 * 1024:  # 100MB
                file_result["message"] = "文件大小超过100MB限制"
                failed_count += 1
                results.append(file_result)
                batch_operation["failed_items"] += 1
                continue
                
            # 检查文件名重复
            existing_docs = [doc for doc in documents.values() if doc["file_name"] == file.filename]
            if existing_docs:
                file_result["message"] = "文件名重复，已跳过"
                failed_count += 1
                results.append(file_result)
                batch_operation["failed_items"] += 1
                continue
            
            # 保存文件
            task_id = str(uuid.uuid4())
            document_id = str(uuid.uuid4())
            file_path = os.path.join(UPLOAD_DIR, file.filename)
            
            with open(file_path, "wb") as buffer:
                buffer.write(content)
            
            # 获取实际文件大小
            actual_file_size = os.path.getsize(file_path)
            
            # 创建任务记录
            task = {
                "task_id": task_id,
                "status": "pending",
                "stage": "parsing",
                "progress": 0,
                "file_path": file_path,
                "file_name": file.filename,
                "file_size": actual_file_size,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "document_id": document_id,
                "batch_operation_id": batch_operation_id,
                "total_stages": len(PROCESSING_STAGES),
                "stage_details": {
                    stage[0]: {
                        "status": "pending",
                        "progress": 0
                    } for stage in PROCESSING_STAGES
                },
                "multimodal_stats": {
                    "images_count": 0,
                    "tables_count": 0,
                    "equations_count": 0,
                    "images_processed": 0,
                    "tables_processed": 0,
                    "equations_processed": 0,
                    "processing_success_rate": 0.0,
                    "text_chunks": 0,
                    "knowledge_entities": 0,
                    "knowledge_relationships": 0
                }
            }
            
            tasks[task_id] = task
            
            # 创建文档记录
            document = {
                "document_id": document_id,
                "file_name": file.filename,
                "file_path": file_path,
                "file_size": actual_file_size,
                "status": "uploaded",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "task_id": task_id,
                "batch_operation_id": batch_operation_id
            }
            
            documents[document_id] = document
            
            # 成功结果
            file_result.update({
                "file_size": actual_file_size,
                "status": "success",
                "message": "上传成功",
                "task_id": task_id,
                "document_id": document_id
            })
            
            uploaded_count += 1
            batch_operation["completed_items"] += 1
            results.append(file_result)
            
        except Exception as e:
            file_result["message"] = f"上传失败: {str(e)}"
            failed_count += 1
            batch_operation["failed_items"] += 1
            results.append(file_result)
            logger.error(f"批量上传文件 {file.filename} 失败: {str(e)}")
        
        # 更新进度
        batch_operation["progress"] = ((i + 1) / len(files)) * 100
        await send_processing_log(f"📤 批量上传进度: {i + 1}/{len(files)} ({batch_operation['progress']:.1f}%)", "info")
    
    # 完成批量操作
    batch_operation["status"] = "completed"
    batch_operation["completed_at"] = datetime.now().isoformat()
    batch_operation["results"] = results
    
    message = f"批量上传完成: {uploaded_count} 个成功, {failed_count} 个失败"
    logger.info(message)
    await send_processing_log(f"✅ {message}", "info")
    
    # JSON文件保存已废弃，数据直接保存到数据库
    
    return BatchUploadResponse(
        success=failed_count == 0,
        uploaded_count=uploaded_count,
        failed_count=failed_count,
        total_files=len(files),
        results=results,
        message=message
    )

@app.post("/api/v1/documents/{document_id}/process")
async def process_document_manually(document_id: str):
    """手动触发文档处理端点"""
    # 从数据库获取文档
    db_document = await safe_get_document(document_id)
    if not db_document:
        raise HTTPException(status_code=404, detail="Document not found")

    # 检查文档状态 - 允许重新处理失败、完成或卡住的文档
    if db_document.status not in ["uploaded", "failed", "completed", "processing"]:
        raise HTTPException(
            status_code=400,
            detail=f"Document cannot be processed. Current status: {db_document.status}"
        )

    # 检查任务是否已存在，如果不存在则创建
    task_id = db_document.task_id
    if not task_id:
        # 如果没有task_id，创建一个新的
        task_id = str(uuid.uuid4())
        db_document.task_id = task_id
        # 更新数据库中的task_id
        if state_manager:
            await state_manager.update_document(db_document)

    # 如果任务不在内存中，重新创建它
    if task_id not in tasks:
        logger.info(f"Task {task_id} not found in memory, recreating it for document {document_id}")
        # 创建新任务
        task = {
            "task_id": task_id,
            "document_id": document_id,
            "status": "pending",
            "stage": "parsing",
            "progress": 0,
            "file_path": db_document.file_path,
            "file_name": db_document.file_name,
            "file_size": db_document.file_size,
            "created_at": db_document.created_at or datetime.now().isoformat(),
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "multimodal_stats": {
                "text_chunks": 0,
                "images_count": 0,
                "tables_count": 0,
                "equations_count": 0,
                "images_processed": 0,
                "tables_processed": 0,
                "equations_processed": 0,
                "processing_success_rate": 0.0
            },
            "stage_details": {
                "parsing": {"status": "pending", "progress": 0, "message": ""},
                "separation": {"status": "pending", "progress": 0, "message": ""},
                "text_insert": {"status": "pending", "progress": 0, "message": ""},
                "image_process": {"status": "pending", "progress": 0, "message": ""},
                "table_process": {"status": "pending", "progress": 0, "message": ""},
                "equation_process": {"status": "pending", "progress": 0, "message": ""},
                "graph_build": {"status": "pending", "progress": 0, "message": ""},
                "indexing": {"status": "pending", "progress": 0, "message": ""}
            }
        }
        tasks[task_id] = task
    
    try:
        # 检查文件是否存在
        if not os.path.exists(db_document.file_path):
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {db_document.file_path}. Please re-upload the document."
            )

        # 更新文档状态为处理中（数据库和内存）
        await safe_update_document_status(document_id, "processing")

        # 兼容性：同步到内存字典
        document = {
            "document_id": db_document.document_id,
            "file_name": db_document.file_name,
            "file_path": db_document.file_path,
            "file_size": db_document.file_size,
            "status": "processing",
            "created_at": db_document.created_at,
            "updated_at": datetime.now().isoformat(),
            "task_id": task_id,  # 使用新的或已存在的task_id
            "processing_time": db_document.processing_time,
            "content_length": db_document.content_length,
            "chunks_count": db_document.chunks_count,
            "rag_doc_id": db_document.rag_doc_id,
            "content_summary": db_document.content_summary,
            "error_message": db_document.error_message,
            "batch_operation_id": db_document.batch_operation_id,
            "parser_used": db_document.parser_used,
            "parser_reason": db_document.parser_reason,
            "content_hash": db_document.content_hash
        }
        documents[document_id] = document

        # 更新任务状态
        task = tasks[task_id]
        task["status"] = "pending"
        task["updated_at"] = datetime.now().isoformat()

        # 启动处理任务
        file_path = db_document.file_path
        asyncio.create_task(process_document_real(task_id, file_path))
        
        logger.info(f"手动启动文档处理: {document['file_name']}")
        
        return {
            "success": True,
            "message": f"Document processing started for {document['file_name']}",
            "document_id": document_id,
            "task_id": task_id,
            "status": "processing"
        }
        
    except Exception as e:
        logger.error(f"启动文档处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start processing: {str(e)}")

@app.post("/api/v1/documents/process/batch", response_model=BatchProcessResponse)
async def process_documents_batch(request: BatchProcessRequest):
    """优化的批量文档处理端点 - 使用真正的并行处理"""
    # 立即添加print语句确保能看到输出
    print(f"\n{'='*80}", flush=True)
    print(f"[BATCH API] 批量处理端点被调用", flush=True)
    print(f"[BATCH API] 请求数据: {request}", flush=True)
    print(f"[BATCH API] 文档ID列表: {request.document_ids}", flush=True)
    print(f"{'='*80}\n", flush=True)

    batch_operation_id = str(uuid.uuid4())
    started_count = 0
    failed_count = 0
    results = []

    # 创建批量操作状态跟踪
    batch_operation = {
        "batch_operation_id": batch_operation_id,
        "operation_type": "process",
        "status": "running",
        "total_items": len(request.document_ids),
        "completed_items": 0,
        "failed_items": 0,
        "progress": 0.0,
        "started_at": datetime.now().isoformat(),
        "results": []
    }
    batch_operations[batch_operation_id] = batch_operation

    print(f"[BATCH API] 批次ID创建: {batch_operation_id}", flush=True)

    logger.info(f"="*80)
    logger.info(f"📋 批量处理开始 - 批次ID: {batch_operation_id}")
    logger.info(f"   文档数量: {len(request.document_ids)}")
    logger.info(f"   文档ID列表: {request.document_ids}")
    logger.info(f"="*80)
    await send_processing_log(f"🚀 开始并行批量处理 {len(request.document_ids)} 个文档", "info")

    try:
        print(f"[BATCH API] 进入try块", flush=True)
        # 初始化RAG系统
        logger.info("[步骤1] 初始化RAG系统...")
        print(f"[BATCH API] 步骤1: 开始初始化RAG系统...", flush=True)
        rag = await initialize_rag()
        print(f"[BATCH API] RAG初始化结果: {rag is not None}", flush=True)
        if not rag:
            error_msg = "RAG系统初始化失败 - initialize_rag()返回None"
            logger.error(f"[步骤1失败] {error_msg}")
            print(f"[BATCH API] 步骤1失败: {error_msg}", flush=True)
            raise Exception(error_msg)
        logger.info("[步骤1完成] ✅ RAG系统初始化成功")
        print(f"[BATCH API] 步骤1完成: RAG系统初始化成功", flush=True)

        # 检查是否启用并行处理
        use_parallel = os.getenv("ENABLE_PARALLEL_PROCESSING", "true").lower() == "true"
        max_workers = int(os.getenv("MAX_CONCURRENT_PROCESSING", "3"))
        logger.info(f"[步骤2] 配置检查 - 并行处理: {use_parallel}, 最大工作数: {max_workers}")
        print(f"[BATCH API] 步骤2: 配置检查 - 并行处理: {use_parallel}, 最大工作数: {max_workers}", flush=True)

        # 步骤3: 转换文档ID为文件路径，验证文档状态
        logger.info(f"[步骤3] 开始验证文档...")
        print(f"[BATCH API] 步骤3: 开始验证 {len(request.document_ids)} 个文档", flush=True)
        print(f"[BATCH API] 内存中文档数量: {len(documents)}", flush=True)

        # 显示内存中前5个文档ID供参考
        if documents:
            sample_ids = list(documents.keys())[:5]
            print(f"[BATCH API] 内存中文档ID示例: {sample_ids}", flush=True)

        valid_documents = []
        file_paths = []

        for document_id in request.document_ids:
            try:
                logger.debug(f"  检查文档 {document_id}...")
                print(f"[BATCH API] 验证文档: {document_id}", flush=True)
                if document_id not in documents:
                    error_msg = f"文档不存在于内存字典中"
                    print(f"[BATCH API] ❌ 文档不存在: {document_id}", flush=True)
                    logger.warning(f"  ❌ 文档 {document_id}: {error_msg}")
                    results.append({
                        "document_id": document_id,
                        "file_name": "unknown",
                        "status": "failed",
                        "message": error_msg,
                        "task_id": None
                    })
                    failed_count += 1
                    continue
                
                document = documents[document_id]
                logger.debug(f"  找到文档: {document['file_name']}, 状态: {document['status']}")

                # 检查文档状态 - 允许重新处理失败或卡住的文档
                if document["status"] not in ["uploaded", "failed", "processing"]:
                    error_msg = f"文档状态不允许处理: 当前状态={document['status']}, 需要状态=uploaded/failed/processing"
                    logger.warning(f"  ⚠️ 文档 {document_id}: {error_msg}")
                    print(f"[BATCH API] ⚠️ 文档状态不允许处理: {document_id}, status={document['status']}", flush=True)
                    results.append({
                        "document_id": document_id,
                        "file_name": document["file_name"],
                        "status": "failed",
                        "message": error_msg,
                        "task_id": document.get("task_id")
                    })
                    failed_count += 1
                    continue

                # 如果是失败或处理中状态，输出提示信息
                if document["status"] == "failed":
                    print(f"[BATCH API] 📝 文档将被重新处理: {document_id} ({document['file_name']})", flush=True)
                    logger.info(f"  🔄 文档 {document_id} ({document['file_name']}) 将从失败状态重新处理")
                elif document["status"] == "processing":
                    print(f"[BATCH API] ⚠️ 文档卡在处理中，将强制重新处理: {document_id} ({document['file_name']})", flush=True)
                    logger.info(f"  ⚠️ 文档 {document_id} ({document['file_name']}) 卡在处理中状态，将强制重新处理")
                
                # 检查任务是否存在
                task_id = document.get("task_id")
                if not task_id:
                    error_msg = "文档没有关联的task_id"
                    logger.warning(f"  ⚠️ 文档 {document_id}: {error_msg}")
                    results.append({
                        "document_id": document_id,
                        "file_name": document["file_name"],
                        "status": "failed",
                        "message": error_msg,
                        "task_id": None
                    })
                    failed_count += 1
                    continue

                if task_id not in tasks:
                    error_msg = f"任务 {task_id} 不存在于tasks字典中"
                    logger.warning(f"  ⚠️ 文档 {document_id}: {error_msg}")
                    results.append({
                        "document_id": document_id,
                        "file_name": document["file_name"],
                        "status": "failed",
                        "message": error_msg,
                        "task_id": task_id
                    })
                    failed_count += 1
                    continue
                
                # 验证文件路径存在
                file_path = document["file_path"]
                if not os.path.exists(file_path):
                    error_msg = f"文件不存在: {file_path}"
                    logger.error(f"  ❌ 文档 {document_id}: {error_msg}")
                    results.append({
                        "document_id": document_id,
                        "file_name": document["file_name"],
                        "status": "failed",
                        "message": error_msg,
                        "task_id": task_id
                    })
                    failed_count += 1
                    continue
                
                # 文档有效，添加到批处理列表
                logger.info(f"  ✅ 文档 {document_id} ({document['file_name']}) 验证通过")
                valid_documents.append({
                    "document_id": document_id,
                    "document": document,
                    "task_id": task_id
                })
                file_paths.append(file_path)

                # 设置初始状态
                document["status"] = "processing"
                document["updated_at"] = datetime.now().isoformat()
                tasks[task_id]["status"] = "pending"
                tasks[task_id]["batch_operation_id"] = batch_operation_id
                
            except Exception as e:
                error_msg = f"准备处理时出错: {str(e)}"
                logger.error(f"  ❌ 文档 {document_id} 验证异常: {error_msg}")
                import traceback
                logger.error(f"    异常堆栈:\n{traceback.format_exc()}")
                results.append({
                    "document_id": document_id,
                    "file_name": documents.get(document_id, {}).get("file_name", "unknown"),
                    "status": "failed",
                    "message": error_msg,
                    "task_id": None
                })
                failed_count += 1
        
        logger.info(f"[步骤3完成] 验证结果: {len(valid_documents)}个有效, {failed_count}个失败")
        print(f"[BATCH API] 步骤3完成: {len(valid_documents)}个有效文档, {failed_count}个失败", flush=True)
        if valid_documents:
            print(f"[BATCH API] 有效文档列表:", flush=True)
            for doc_info in valid_documents:
                print(f"[BATCH API]   - {doc_info['document_id']}: {doc_info['document']['file_name']}", flush=True)

        # 初始化cache_metrics变量（并行和缓存处理都需要）
        cache_metrics = {}

        # 步骤4: 如果有有效文档，根据配置选择处理模式
        if file_paths:
            logger.info(f"[步骤4] 开始处理 {len(file_paths)} 个有效文档...")
            # 检查是否使用并行处理
            if use_parallel and parallel_batch_processor:
                logger.info(f"[步骤4.1] 使用并行处理器处理文档...")
                await send_processing_log(f"⚡ 使用真正的并行批量处理 {len(file_paths)} 个文档 (最大并发: {max_workers})", "info")

                # 检查parallel_batch_processor是否正确初始化
                if not parallel_batch_processor:
                    error_msg = "parallel_batch_processor未初始化"
                    logger.error(f"[步骤4.1失败] {error_msg}")
                    raise Exception(error_msg)

                logger.info(f"  并行处理器状态: rag_instance={parallel_batch_processor.rag_instance is not None}, max_workers={parallel_batch_processor.max_workers}")

                # 准备文档数据格式
                docs_for_parallel = [
                    {
                        "document_id": doc_info["document_id"],
                        "file_path": doc_info["document"]["file_path"]
                    }
                    for doc_info in valid_documents
                ]

                # 定义进度回调
                async def parallel_progress_callback(progress_data):
                    """并行处理进度回调"""
                    try:
                        # 发送进度到WebSocket
                        progress_msg = {
                            "type": "batch_progress",
                            "batch_id": batch_operation_id,
                            **progress_data
                        }

                        for ws in processing_log_websockets:
                            try:
                                await ws.send_text(json.dumps(progress_msg))
                            except:
                                pass

                        # 记录日志
                        if progress_data.get("status") == "completed":
                            await send_processing_log(f"✅ 完成: {progress_data['file_name']}", "success")
                        elif progress_data.get("status") == "failed":
                            await send_processing_log(f"❌ 失败: {progress_data['file_name']} - {progress_data.get('error', '未知错误')}", "error")
                        elif progress_data.get("status") == "processing":
                            await send_processing_log(f"🔄 处理中: {progress_data['file_name']}", "info")
                    except Exception as e:
                        logger.error(f"进度回调错误: {e}")

                # 使用并行批量处理器
                try:
                    logger.info(f"  调用 process_batch_parallel，参数:")
                    logger.info(f"    - documents数量: {len(docs_for_parallel)}")
                    logger.info(f"    - output_dir: {OUTPUT_DIR}")
                    logger.info(f"    - parse_method: {request.parse_method or 'auto'}")
                    logger.info(f"    - device: {'cuda' if TORCH_AVAILABLE and torch.cuda.is_available() else 'cpu'}")

                    batch_result = await parallel_batch_processor.process_batch_parallel(
                        documents=docs_for_parallel,
                        progress_callback=parallel_progress_callback,
                        output_dir=OUTPUT_DIR,
                        parse_method=request.parse_method or "auto",
                        device="cuda" if TORCH_AVAILABLE and torch.cuda.is_available() else "cpu",
                        lang="en"
                    )

                    logger.info(f"[步骤4.1完成] 并行处理返回结果:")
                    logger.info(f"  - 成功: {batch_result.get('successful', 0)}")
                    logger.info(f"  - 失败: {batch_result.get('failed', 0)}")
                    logger.info(f"  - 总耗时: {batch_result.get('total_time', 0):.1f}秒")

                except Exception as e:
                    error_msg = f"并行处理器执行失败: {str(e)}"
                    logger.error(f"[步骤4.1失败] {error_msg}")
                    import traceback
                    logger.error(f"异常堆栈:\n{traceback.format_exc()}")
                    raise Exception(error_msg)

                # 处理结果
                for doc_info in valid_documents:
                    document_id = doc_info["document_id"]
                    document = doc_info["document"]
                    task_id = doc_info["task_id"]

                    doc_result = batch_result["results"].get(document_id, {})

                    if doc_result.get("success"):
                        # 成功处理
                        document["status"] = "completed"
                        tasks[task_id]["status"] = "completed"
                        tasks[task_id]["completed_at"] = datetime.now().isoformat()

                        await safe_update_document_status(
                            document_id,
                            "completed",
                            processing_time=doc_result.get("processing_time"),
                            parser_used=doc_result.get("parser_used")
                        )

                        results.append({
                            "document_id": document_id,
                            "file_name": document["file_name"],
                            "status": "success",
                            "message": f"并行处理成功 (耗时: {doc_result.get('processing_time', 0):.1f}秒)",
                            "task_id": task_id
                        })
                        started_count += 1
                    else:
                        # 处理失败
                        error_msg = doc_result.get("error", "并行处理失败")
                        document["status"] = "failed"
                        tasks[task_id]["status"] = "failed"
                        tasks[task_id]["error"] = error_msg

                        await safe_update_document_status(
                            document_id,
                            "failed",
                            error_message=error_msg
                        )

                        results.append({
                            "document_id": document_id,
                            "file_name": document["file_name"],
                            "status": "failed",
                            "message": f"处理失败: {error_msg}",
                            "task_id": task_id
                        })
                        failed_count += 1

                # 记录性能统计
                await send_processing_log(
                    f"📊 并行批量处理完成: {batch_result['successful']}/{batch_result['total_documents']} 成功, "
                    f"总耗时 {batch_result['total_time']:.1f}秒, "
                    f"并行加速比 {batch_result['parallel_speedup']:.2f}x",
                    "info"
                )
            else:
                # 使用原有的缓存增强处理器（保持向后兼容）
                logger.info(f"[步骤4.2] 使用缓存增强处理器处理文档...")
                await send_processing_log(f"📊 使用缓存增强的高级批量处理 {len(file_paths)} 个文档", "info")

                if not cache_enhanced_processor:
                    error_msg = "cache_enhanced_processor未初始化"
                    logger.error(f"[步骤4.2失败] {error_msg}")
                    raise Exception(error_msg)

                # 获取配置参数
                parse_method = request.parse_method or "auto"
                device_type = "cuda" if TORCH_AVAILABLE and torch.cuda.is_available() else "cpu"

                # 创建WebSocket进度回调
                async def websocket_progress_callback(progress_data):
                    """WebSocket progress callback for real-time updates"""
                    try:
                        # Send progress to all connected WebSocket clients
                        for ws in processing_log_websockets:
                            try:
                                await ws.send_text(json.dumps(progress_data))
                            except Exception:
                                pass  # Remove disconnected clients silently
                    except Exception as e:
                        logger.debug(f"WebSocket progress callback error: {e}")

                # Register progress callback with advanced progress tracker
                advanced_progress_tracker.register_websocket_callback(websocket_progress_callback)

                try:
                    logger.info(f"  调用 batch_process_with_cache_tracking，参数:")
                    logger.info(f"    - file_paths数量: {len(file_paths)}")
                    logger.info(f"    - output_dir: {OUTPUT_DIR}")
                    logger.info(f"    - parse_method: {parse_method}")
                    logger.info(f"    - max_workers: {max_workers}")

                    # 使用缓存增强处理器进行批量处理，带有增强的错误处理和进度跟踪
                    batch_result = await cache_enhanced_processor.batch_process_with_cache_tracking(
                        file_paths=file_paths,
                        progress_callback=websocket_progress_callback,
                        output_dir=OUTPUT_DIR,
                        parse_method=parse_method,
                        max_workers=max_workers,
                        recursive=False,  # 不扫描目录，处理明确的文件列表
                        show_progress=True,
                        lang="en",  # 可以从配置中获取
                        device=device_type if TORCH_AVAILABLE else "cpu"
                    )

                    logger.info(f"[步骤4.2完成] 缓存处理返回结果:")
                    logger.info(f"  - successful_files: {len(batch_result.get('successful_files', []))}")
                    logger.info(f"  - failed_files: {len(batch_result.get('failed_files', []))}")
                    logger.info(f"  - total_processing_time: {batch_result.get('total_processing_time', 0):.1f}秒")

                except Exception as e:
                    error_msg = f"缓存处理器执行失败: {str(e)}"
                    logger.error(f"[步骤4.2失败] {error_msg}")
                    import traceback
                    logger.error(f"异常堆栈:\n{traceback.format_exc()}")
                    raise Exception(error_msg)

                finally:
                    # Clean up progress callback registration
                    try:
                        advanced_progress_tracker.unregister_websocket_callback(websocket_progress_callback)
                    except Exception:
                        pass

                await send_processing_log(f"✅ RAGAnything批量处理完成", "info")

                # 步骤3: 处理批量结果并更新文档状态
                parse_results = batch_result.get("parse_result", {})
                # 获取成功和失败的文件列表
                successful_files = batch_result.get("successful_files", [])
                failed_files = batch_result.get("failed_files", [])
                errors = batch_result.get("errors", {})
                successful_rag_files = batch_result.get("successful_rag_files", 0)
                processing_time = batch_result.get("total_processing_time", 0)
                cache_metrics = batch_result.get("cache_metrics", {})

                # 映射文件路径到文档ID
                path_to_doc = {doc_info["document"]["file_path"]: doc_info for doc_info in valid_documents}

                # 处理每个文件的结果
                for file_path in file_paths:
                    doc_info = path_to_doc[file_path]
                    document_id = doc_info["document_id"]
                    document = doc_info["document"]
                    task_id = doc_info["task_id"]

                    # 检查文件是否在成功列表中
                    if file_path in successful_files:
                        # 成功处理
                        document["status"] = "completed"
                        tasks[task_id]["status"] = "completed"
                        tasks[task_id]["completed_at"] = datetime.now().isoformat()

                        # Update database status through state manager
                        await safe_update_document_status(
                            document_id,
                            "completed"
                        )

                        results.append({
                            "document_id": document_id,
                            "file_name": document["file_name"],
                            "status": "success",
                            "message": "文档批量处理成功",
                            "task_id": task_id
                        })
                        started_count += 1
                    else:
                        # 处理失败 - 从errors字典获取错误信息
                        error_msg = errors.get(file_path, "批量处理过程中出现未知错误")
                        document["status"] = "failed"
                        tasks[task_id]["status"] = "failed"
                        tasks[task_id]["error"] = error_msg
                        tasks[task_id]["updated_at"] = datetime.now().isoformat()

                        # Update database status through state manager
                        await safe_update_document_status(
                            document_id,
                            "failed",
                            error_message=error_msg
                        )

                        results.append({
                            "document_id": document_id,
                            "file_name": document["file_name"],
                            "status": "failed",
                            "message": f"RAG处理失败: {error_msg}",
                            "task_id": task_id
                        })
                        failed_count += 1

                # 记录详细的缓存性能统计
                cache_metrics = batch_result.get("cache_metrics", {})
                cache_hits = cache_metrics.get("cache_hits", 0)
                cache_misses = cache_metrics.get("cache_misses", 0)
                time_saved = cache_metrics.get("total_time_saved", 0.0)
                hit_ratio = cache_metrics.get("cache_hit_ratio", 0.0)
                efficiency = cache_metrics.get("efficiency_improvement", 0.0)

                await send_processing_log(f"📈 批量处理性能统计: {successful_rag_files} 成功, 耗时 {processing_time:.2f}s", "info")
                await send_processing_log(f"🚀 缓存性能: {cache_hits} 命中, {cache_misses} 未命中, 命中率 {hit_ratio:.1f}%", "info")
                if time_saved > 0:
                    await send_processing_log(f"⚡ 时间节省: {time_saved:.1f}s, 效率提升 {efficiency:.1f}%", "info")
        
        # 更新批量操作状态
        batch_operation["completed_items"] = started_count
        batch_operation["failed_items"] = failed_count
        batch_operation["progress"] = 100.0
        batch_operation["status"] = "completed"
        batch_operation["completed_at"] = datetime.now().isoformat()
        batch_operation["results"] = results
        
        message = f"高级批量处理完成: {started_count} 个成功, {failed_count} 个失败"
        logger.info(message)
        await send_processing_log(f"🎉 {message}", "info")
        
        # 创建包含缓存性能的响应
        response_data = {
            "success": failed_count == 0,
            "started_count": started_count,
            "failed_count": failed_count,
            "total_requested": len(request.document_ids),
            "results": results,
            "batch_operation_id": batch_operation_id,
            "message": message,
            "cache_performance": cache_metrics if cache_metrics else {
                "cache_hits": 0,
                "cache_misses": 0,
                "cache_hit_ratio": 0.0,
                "total_time_saved": 0.0,
                "efficiency_improvement": 0.0
            }
        }

        return BatchProcessResponse(**response_data)

    except Exception as e:
        # 立即打印异常信息
        print(f"\n[BATCH API ERROR] ❌ 批量处理失败!!!", flush=True)
        print(f"[BATCH API ERROR] 批次ID: {batch_operation_id}", flush=True)
        print(f"[BATCH API ERROR] 异常类型: {type(e).__name__}", flush=True)
        print(f"[BATCH API ERROR] 异常消息: {str(e)}", flush=True)
        import traceback
        error_traceback = traceback.format_exc()
        print(f"[BATCH API ERROR] 完整堆栈:\n{error_traceback}", flush=True)

        # 记录主异常
        logger.error(f"="*80)
        logger.error(f"[批量处理失败] 批次ID: {batch_operation_id}")
        logger.error(f"异常类型: {type(e).__name__}")
        logger.error(f"异常消息: {str(e)}")
        logger.error(f"完整堆栈跟踪:\n{error_traceback}")
        logger.error(f"="*80)

        # 使用增强的错误处理器
        error_info = enhanced_error_handler.categorize_error(e, {
            "operation": "batch_processing",
            "batch_id": batch_operation_id,
            "document_count": len(request.document_ids),
            "context": "api_endpoint"
        })
        
        # 获取用户友好的错误信息
        user_error = enhanced_error_handler.get_user_friendly_error_message(error_info)
        
        error_msg = f"批量处理失败: {user_error['message']}"
        logger.error(f"Batch processing error: {error_info.message}")
        await send_processing_log(f"❌ {error_msg}", "error")
        
        # 如果是可恢复的错误，提供建议
        if error_info.is_recoverable:
            await send_processing_log(f"💡 建议: {user_error['suggested_solution']}", "warning")
        
        # 获取系统健康警告
        health_warnings = enhanced_error_handler.get_system_health_warnings()
        for warning in health_warnings:
            await send_processing_log(f"⚠️ 系统警告: {warning}", "warning")
        
        # 更新所有待处理文档的状态为失败
        for document_id in request.document_ids:
            if document_id in documents:
                document = documents[document_id]
                document["status"] = "failed"
                document["error_category"] = error_info.category.value
                document["error_severity"] = user_error['severity']
                document["suggested_solution"] = user_error['suggested_solution']
                
                task_id = document.get("task_id")
                if task_id and task_id in tasks:
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = error_msg
                    tasks[task_id]["error_category"] = error_info.category.value
                    tasks[task_id]["error_details"] = user_error
                    tasks[task_id]["updated_at"] = datetime.now().isoformat()
        
        # 更新批量操作状态
        batch_operation["status"] = "failed"
        batch_operation["failed_items"] = len(request.document_ids)
        batch_operation["completed_at"] = datetime.now().isoformat()
        batch_operation["error"] = error_msg
        batch_operation["error_details"] = user_error
        batch_operation["system_warnings"] = health_warnings
        
        # 返回详细的错误信息
        error_response = {
            "error": error_msg,
            "error_category": error_info.category.value,
            "error_severity": user_error['severity'],
            "is_recoverable": error_info.is_recoverable,
            "suggested_solution": user_error['suggested_solution'],
            "system_warnings": health_warnings,
            "timestamp": datetime.now().isoformat()
        }
        
        raise HTTPException(status_code=500, detail=error_response)

@app.post("/api/v1/query")
async def query_documents(request: QueryRequest):
    """查询文档端点"""
    rag = await initialize_rag()
    if not rag:
        raise HTTPException(status_code=503, detail="RAG系统未初始化")
    
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="查询内容不能为空")
    
    try:
        # 检查RAG系统状态
        logger.info(f"开始执行查询: query='{request.query}', mode={request.mode}, vlm_enhanced={request.vlm_enhanced}")

        # 确保LightRAG已初始化
        if not hasattr(rag, 'lightrag') or rag.lightrag is None:
            logger.error("RAG系统的LightRAG组件未初始化")
            await rag._ensure_lightrag_initialized()

        # 执行查询
        result = await rag.aquery(
            request.query,
            mode=request.mode,
            vlm_enhanced=request.vlm_enhanced if request.vlm_enhanced is not None else False
        )

        # 记录查询任务
        query_task_id = str(uuid.uuid4())
        query_task = {
            "task_id": query_task_id,
            "type": "query",
            "query": request.query,
            "mode": request.mode,
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "processing_time": 0.234,  # 模拟处理时间
            "status": "completed"
        }
        tasks[query_task_id] = query_task

        logger.info(f"查询成功完成: task_id={query_task_id}")

        return {
            "success": True,
            "query": request.query,
            "mode": request.mode,
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "processing_time": 0.234,
            "sources": [],  # RAG可能返回的源文档信息
            "metadata": {
                "total_documents": len(documents),
                "tokens_used": 156,
                "confidence_score": 0.89
            }
        }

    except AttributeError as e:
        logger.error(f"属性错误 - RAG系统可能未完全初始化: {str(e)}")
        import traceback
        logger.error(f"完整堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=503, detail="RAG系统未完全初始化，请稍后重试")

    except Exception as e:
        logger.error(f"查询失败 - 错误类型: {type(e).__name__}")
        logger.error(f"错误消息: {str(e)}")
        import traceback
        logger.error(f"完整堆栈跟踪:\n{traceback.format_exc()}")

        # 检查是否是常见错误
        error_msg = str(e)
        if "No documents" in error_msg or "empty" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail="知识库中暂无文档，请先上传并处理文档后再进行查询"
            )
        elif "API" in error_msg or "api_key" in error_msg.lower():
            raise HTTPException(
                status_code=503,
                detail="LLM API连接失败，请检查API密钥配置"
            )
        elif "connection" in error_msg.lower() or "network" in error_msg.lower():
            raise HTTPException(
                status_code=503,
                detail="网络连接错误，请检查网络和数据库连接"
            )
        else:
            raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

@app.post("/api/v1/query/multimodal")
async def query_multimodal(request: MultimodalQueryRequest, background_tasks: BackgroundTasks):
    """多模态查询端点 - 支持图像、表格、公式等多种内容类型"""

    # 添加详细日志
    print(f"[RAG_API_SERVER] Received multimodal query")
    print(f"[RAG_API_SERVER] Query: {request.query}")
    print(f"[RAG_API_SERVER] Mode: {request.mode}")
    print(f"[RAG_API_SERVER] Multimodal content count: {len(request.multimodal_content) if request.multimodal_content else 0}")
    logger.info(f"[RAG_API_SERVER] Multimodal query received: {request.query[:100]}")

    # 检查多模态处理器是否可用
    if not MULTIMODAL_AVAILABLE or not multimodal_handler:
        print(f"[RAG_API_SERVER] Multimodal handler not available")
        raise HTTPException(
            status_code=503,
            detail="多模态处理器未安装或未初始化。请检查依赖项和配置。"
        )

    # 确保RAG系统已初始化
    if not rag_instance:
        print(f"[RAG_API_SERVER] RAG instance not initialized")
        raise HTTPException(
            status_code=503,
            detail="RAG系统未初始化"
        )

    try:
        print(f"[RAG_API_SERVER] Calling multimodal_handler.process_multimodal_query")
        # 处理多模态查询
        result = await multimodal_handler.process_multimodal_query(
            request,
            background_tasks
        )

        # 记录查询成功
        print(f"[RAG_API_SERVER] Query processed successfully: {result.query_id}")
        logger.info(f"多模态查询成功: query_id={result.query_id}")

        return result

    except ValidationError as e:
        print(f"[RAG_API_SERVER] ValidationError caught: {e}")
        logger.error(f"[RAG_API_SERVER] ValidationError: {e}")
        # 验证错误 - 输入格式不正确
        logger.warning(f"多模态查询验证失败: {e.message}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": e.message,
                "details": e.details
            }
        )

    except Exception as e:
        # 其他错误
        logger.error(f"多模态查询失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_error",
                "message": "多模态查询处理失败",
                "details": str(e)
            }
        )

@app.get("/api/v1/multimodal/health")
async def multimodal_health():
    """检查多模态处理器健康状态"""
    if not MULTIMODAL_AVAILABLE:
        return {
            "status": "unavailable",
            "message": "多模态组件未安装"
        }

    if not multimodal_handler:
        return {
            "status": "uninitialized",
            "message": "多模态处理器未初始化"
        }

    try:
        health = await multimodal_handler.health_check()
        return health
    except Exception as e:
        return {
            "status": "unhealthy",
            "message": str(e)
        }

@app.get("/api/v1/multimodal/metrics")
async def multimodal_metrics():
    """获取多模态处理器指标"""
    if not multimodal_handler:
        raise HTTPException(
            status_code=503,
            detail="多模态处理器未初始化"
        )

    try:
        metrics = multimodal_handler.get_metrics()
        return {
            "success": True,
            "metrics": metrics
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"获取指标失败: {str(e)}"
        )

@app.get("/api/v1/tasks")
async def list_tasks():
    """获取任务列表"""
    return {
        "success": True,
        "tasks": list(tasks.values()),
        "total_count": len(tasks),
        "active_tasks": len([t for t in tasks.values() if t["status"] == "running"])
    }

@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    """获取特定任务"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {
        "success": True,
        "task": tasks[task_id]
    }

@app.get("/api/v1/tasks/{task_id}/detailed-status")
async def get_detailed_task_status(task_id: str):
    """获取任务的详细状态信息"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 获取详细状态
    detailed_status = detailed_tracker.get_status(task_id)
    if not detailed_status:
        return {
            "success": True,
            "task_id": task_id,
            "has_detailed_status": False,
            "message": "详细状态跟踪不可用"
        }
    
    return {
        "success": True,
        "task_id": task_id,
        "has_detailed_status": True,
        "detailed_status": detailed_status.to_dict()
    }

@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task["status"] == "running":
        task["status"] = "cancelled"
        task["updated_at"] = datetime.now().isoformat()
        
        # 更新文档状态
        if task["document_id"] in documents:
            documents[task["document_id"]]["status"] = "failed"
            documents[task["document_id"]]["error_message"] = "Task cancelled by user"
        
        # 关闭WebSocket连接
        if task_id in active_websockets:
            try:
                await active_websockets[task_id].close()
            except:
                pass
            active_websockets.pop(task_id, None)
    
    return {
        "success": True,
        "message": "Task cancelled successfully"
    }

def get_document_display_info(doc):
    """获取文档的显示信息，适用于统一文档区域"""
    doc_id = doc["document_id"]
    task_id = doc.get("task_id")
    
    # 基础信息
    display_info = {
        "document_id": doc_id,
        "file_name": doc["file_name"],
        "file_size": doc["file_size"],
        "uploaded_at": doc["created_at"],
        "status_code": doc["status"]
    }
    
    # 根据状态生成显示信息
    if doc["status"] == "uploaded":
        display_info.update({
            "status_display": "等待解析",
            "action_type": "start_processing",
            "action_icon": "play",
            "action_text": "开始解析",
            "can_process": True
        })
    
    elif doc["status"] == "processing":
        # 获取实时处理状态
        current_progress = ""
        progress_percent = 0
        
        if task_id and task_id in tasks:
            task = tasks[task_id]
            stage = task.get("stage", "processing")
            progress = task.get("progress", 0)
            progress_percent = progress
            
            # 获取详细状态信息
            detailed_status = detailed_tracker.get_status(task_id)
            if detailed_status:
                current_stage = detailed_status.current_stage
                if current_stage:
                    stage_names = {
                        "parsing": "解析文档",
                        "content_analysis": "分析内容", 
                        "text_processing": "处理文本",
                        "image_processing": "处理图片",
                        "table_processing": "处理表格",
                        "equation_processing": "处理公式",
                        "graph_building": "构建知识图谱",
                        "indexing": "创建索引"
                    }
                    stage_display = stage_names.get(current_stage.value, current_stage.value)
                    
                    # 显示具体进度信息
                    if hasattr(detailed_status, 'content_stats'):
                        stats = detailed_status.content_stats
                        if current_stage.value == "image_processing" and stats.image_blocks > 0:
                            current_progress = f"{stage_display} ({stats.image_blocks}张图片)"
                        elif current_stage.value == "table_processing" and stats.table_blocks > 0:
                            current_progress = f"{stage_display} ({stats.table_blocks}个表格)"
                        else:
                            current_progress = stage_display
                    else:
                        current_progress = stage_display
                else:
                    current_progress = "解析中..."
            else:
                stage_names = {
                    "parsing": "解析文档",
                    "separation": "分离内容",
                    "text_insert": "插入文本", 
                    "image_process": "处理图片",
                    "table_process": "处理表格",
                    "equation_process": "处理公式",
                    "graph_build": "构建知识图谱",
                    "indexing": "创建索引"
                }
                current_progress = stage_names.get(stage, "解析中...")
        
        display_info.update({
            "status_display": f"解析中 - {current_progress}",
            "action_type": "processing",
            "action_icon": "loading",
            "action_text": f"{progress_percent}%",
            "can_process": False,
            "progress_percent": progress_percent
        })
    
    elif doc["status"] == "completed":
        # 计算完成时间
        time_info = "刚刚完成"
        if "updated_at" in doc:
            try:
                from datetime import datetime
                updated_time = datetime.fromisoformat(doc["updated_at"])
                now = datetime.now()
                time_diff = now - updated_time
                
                if time_diff.days > 0:
                    time_info = f"{time_diff.days}天前完成"
                elif time_diff.seconds > 3600:
                    hours = time_diff.seconds // 3600
                    time_info = f"{hours}小时前完成"
                elif time_diff.seconds > 60:
                    minutes = time_diff.seconds // 60
                    time_info = f"{minutes}分钟前完成"
                else:
                    time_info = "刚刚完成"
            except:
                time_info = "已完成"
        
        # 添加文档统计信息
        chunks_info = ""
        if "chunks_count" in doc and doc["chunks_count"]:
            chunks_info = f" ({doc['chunks_count']}个文本块)"
        
        display_info.update({
            "status_display": f"已完成 - {time_info}{chunks_info}",
            "action_type": "completed",
            "action_icon": "check",
            "action_text": "已完成",
            "can_process": False
        })
    
    elif doc["status"] == "failed":
        error_msg = doc.get("error_message", "未知错误")
        # 简化错误信息显示
        if error_msg and len(error_msg) > 30:
            error_msg = error_msg[:30] + "..."
        elif not error_msg:
            error_msg = "未知错误"
        
        display_info.update({
            "status_display": f"解析失败 - {error_msg}",
            "action_type": "retry",
            "action_icon": "refresh",
            "action_text": "重试",
            "can_process": True
        })
    
    else:
        display_info.update({
            "status_display": doc["status"],
            "action_type": "unknown",
            "action_icon": "question",
            "action_text": "未知",
            "can_process": False
        })
    
    return display_info

@app.get("/api/v1/documents")
async def list_documents():
    """获取文档列表 - 优化为统一文档区域显示"""
    # 从数据库获取所有文档
    all_docs = await safe_get_all_documents()

    # 获取增强的文档显示信息
    enhanced_documents = []
    for doc in all_docs:
        # 将Document对象转换为字典格式以兼容现有的get_document_display_info函数
        doc_dict = {
            "document_id": doc.document_id,
            "file_name": doc.file_name,
            "file_path": doc.file_path,
            "file_size": doc.file_size,
            "status": doc.status,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "task_id": doc.task_id,
            "processing_time": doc.processing_time,
            "content_length": doc.content_length,
            "chunks_count": doc.chunks_count,
            "rag_doc_id": doc.rag_doc_id,
            "content_summary": doc.content_summary,
            "error_message": doc.error_message,
            "batch_operation_id": doc.batch_operation_id,
            "parser_used": doc.parser_used,
            "parser_reason": doc.parser_reason,
            "content_hash": doc.content_hash
        }
        enhanced_documents.append(get_document_display_info(doc_dict))

    # 按上传时间倒序排列，最新上传的在前
    enhanced_documents.sort(key=lambda x: x["uploaded_at"], reverse=True)

    return {
        "success": True,
        "documents": enhanced_documents,
        "total_count": len(enhanced_documents),
        "status_counts": {
            "uploaded": len([d for d in all_docs if d.status == "uploaded"]),
            "processing": len([d for d in all_docs if d.status == "processing"]),
            "completed": len([d for d in all_docs if d.status == "completed"]),
            "failed": len([d for d in all_docs if d.status == "failed"])
        }
    }

@app.get("/api/v1/logs/summary")
async def get_log_summary_api(mode: str = "summary", include_debug: bool = False):
    """获取日志摘要"""
    try:
        summary = get_log_summary(include_debug=include_debug)
        return {
            "success": True,
            "data": summary
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"获取日志摘要失败: {str(e)}"}
        )

@app.get("/api/v1/logs/core")
async def get_core_logs_api():
    """获取核心进度日志"""
    try:
        core_logs = get_core_progress()
        return {
            "success": True,
            "logs": core_logs
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"获取核心日志失败: {str(e)}"}
        )

@app.post("/api/v1/logs/clear")
async def clear_processing_logs_api():
    """清空处理日志"""
    try:
        clear_logs()
        return {"success": True, "message": "日志已清空"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"清空日志失败: {str(e)}"}
        )

@app.delete("/api/v1/documents")
async def delete_documents(request: DocumentDeleteRequest):
    """删除文档 - 完整删除包括向量库和知识图谱中的相关内容"""
    deleted_count = 0
    deletion_results = []
    rag = await initialize_rag()
    
    for doc_id in request.document_ids:
        # 从数据库获取文档信息
        doc = await state_manager.get_document(doc_id) if state_manager else None

        # 如果数据库中没有，尝试从内存中获取（兼容旧代码）
        if not doc and doc_id in documents:
            doc = documents[doc_id]

        if doc:
            # 处理Document对象和dict兼容性
            if hasattr(doc, 'file_name'):  # Document对象
                file_name = doc.file_name
                file_path = doc.file_path
                rag_doc_id = doc.rag_doc_id
            else:  # dict
                file_name = doc.get("file_name", "unknown")
                file_path = doc.get("file_path", "")
                rag_doc_id = doc.get("rag_doc_id")

            result = {
                "document_id": doc_id,
                "file_name": file_name,
                "status": "success",
                "message": "",
                "details": {}
            }

            try:
                # 1. 从RAG系统中删除文档数据（如果有rag_doc_id）
                if rag_doc_id and rag:
                    logger.info(f"从RAG系统删除文档: {rag_doc_id}")
                    deletion_result = await rag.lightrag.adelete_by_doc_id(rag_doc_id)
                    result["details"]["rag_deletion"] = {
                        "status": deletion_result.status,
                        "message": deletion_result.message,
                        "status_code": deletion_result.status_code
                    }
                    logger.info(f"RAG删除结果: {deletion_result.status} - {deletion_result.message}")
                else:
                    result["details"]["rag_deletion"] = {
                        "status": "skipped",
                        "message": "文档未在RAG系统中找到或RAG未初始化",
                        "status_code": 404
                    }
                
                # 2. 删除上传的文件
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    result["details"]["file_deletion"] = "文件已删除"
                    logger.info(f"删除文件: {file_path}")
                else:
                    result["details"]["file_deletion"] = "文件不存在或已删除"
                
                # 3. 从数据库中删除文档记录
                if state_manager:
                    await state_manager.remove_document(doc_id)

                # 4. 从内存中删除文档记录（如果存在）
                if doc_id in documents:
                    del documents[doc_id]
                deleted_count += 1
                
                result["message"] = f"文档 {file_name} 已完全删除"
                logger.info(f"成功删除文档: {file_name}")
                
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"删除文档时发生错误: {str(e)}"
                result["details"]["error"] = str(e)
                logger.error(f"删除文档失败 {file_name}: {str(e)}")
            
            deletion_results.append(result)
        else:
            deletion_results.append({
                "document_id": doc_id,
                "file_name": "unknown",
                "status": "not_found",
                "message": "文档不存在",
                "details": {}
            })
    
    success_count = len([r for r in deletion_results if r["status"] == "success"])
    
    return {
        "success": success_count > 0,
        "message": f"成功删除 {success_count}/{len(request.document_ids)} 个文档",
        "deleted_count": success_count,
        "deletion_results": deletion_results
    }

@app.delete("/api/v1/documents/clear")
async def clear_documents():
    """清空所有文档 - 完整清空包括向量库和知识图谱中的所有内容"""
    count = len(documents)
    rag = await initialize_rag()
    
    # 记录清空结果
    clear_results = {
        "total_documents": count,
        "files_deleted": 0,
        "rag_deletions": {"success": 0, "failed": 0, "skipped": 0},
        "orphan_deletions": {"success": 0, "failed": 0},
        "errors": []
    }
    
    # 1. 删除文档管理界面中的文档
    for doc_id, doc in list(documents.items()):
        try:
            # 从RAG系统中删除文档数据
            rag_doc_id = doc.get("rag_doc_id")
            if rag_doc_id and rag:
                try:
                    deletion_result = await rag.lightrag.adelete_by_doc_id(rag_doc_id)
                    if deletion_result.status == "success":
                        clear_results["rag_deletions"]["success"] += 1
                        logger.info(f"从RAG系统删除文档: {rag_doc_id} - {deletion_result.message}")
                    else:
                        clear_results["rag_deletions"]["failed"] += 1
                        logger.warning(f"RAG删除失败: {rag_doc_id} - {deletion_result.message}")
                except Exception as e:
                    clear_results["rag_deletions"]["failed"] += 1
                    clear_results["errors"].append(f"RAG删除失败 {rag_doc_id}: {str(e)}")
                    logger.error(f"RAG删除异常 {rag_doc_id}: {str(e)}")
            else:
                clear_results["rag_deletions"]["skipped"] += 1
            
            # 删除上传的文件
            if os.path.exists(doc["file_path"]):
                os.remove(doc["file_path"])
                clear_results["files_deleted"] += 1
                
        except Exception as e:
            clear_results["errors"].append(f"删除文档失败 {doc.get('file_name', doc_id)}: {str(e)}")
            logger.error(f"删除文档异常: {str(e)}")
    
    # 2. 清理RAG系统中的孤儿文档
    if rag:
        try:
            # 读取RAG系统中的所有文档
            doc_status_file = os.path.join(TEMP_WORKING_DIR, "kv_store_doc_status.json")
            if os.path.exists(doc_status_file):
                logger.info("清理RAG系统中的孤儿文档...")
                with open(doc_status_file, 'r', encoding='utf-8') as f:
                    rag_docs = json.load(f)
                
                # 获取已处理的RAG文档ID
                processed_rag_ids = {doc.get("rag_doc_id") for doc in documents.values() if doc.get("rag_doc_id")}
                
                # 找出孤儿文档
                orphan_rag_ids = set(rag_docs.keys()) - processed_rag_ids
                logger.info(f"发现 {len(orphan_rag_ids)} 个孤儿文档: {list(orphan_rag_ids)}")
                
                # 删除孤儿文档
                for orphan_id in orphan_rag_ids:
                    try:
                        deletion_result = await rag.lightrag.adelete_by_doc_id(orphan_id)
                        if deletion_result.status == "success":
                            clear_results["orphan_deletions"]["success"] += 1
                            logger.info(f"清理孤儿文档: {orphan_id} - {deletion_result.message}")
                        else:
                            clear_results["orphan_deletions"]["failed"] += 1
                            logger.warning(f"孤儿文档删除失败: {orphan_id} - {deletion_result.message}")
                    except Exception as e:
                        clear_results["orphan_deletions"]["failed"] += 1
                        clear_results["errors"].append(f"孤儿文档删除失败 {orphan_id}: {str(e)}")
                        logger.error(f"孤儿文档删除异常 {orphan_id}: {str(e)}")
        except Exception as e:
            clear_results["errors"].append(f"孤儿文档清理失败: {str(e)}")
            logger.error(f"孤儿文档清理异常: {str(e)}")
    
    # 清空内存数据
    documents.clear()
    tasks.clear()
    
    # 关闭所有WebSocket连接
    for ws in active_websockets.values():
        try:
            await ws.close()
        except:
            pass
    active_websockets.clear()
    
    # 生成清空报告
    success_rate = (clear_results["rag_deletions"]["success"] / max(count, 1)) * 100
    message = f"清空完成: {count}个文档, RAG删除成功率{success_rate:.1f}%"
    
    if clear_results["errors"]:
        message += f", {len(clear_results['errors'])}个错误"
    
    logger.info(message)
    logger.info(f"详细结果: {clear_results}")
    
    return {
        "success": True,
        "message": message,
        "details": clear_results
    }

@app.websocket("/ws/task/{task_id}")
async def websocket_task_endpoint(websocket: WebSocket, task_id: str):
    """任务进度WebSocket端点"""
    await websocket.accept()
    active_websockets[task_id] = websocket
    
    try:
        # 发送当前任务状态
        if task_id in tasks:
            await websocket.send_text(json.dumps(tasks[task_id]))
        
        # 保持连接
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
    finally:
        active_websockets.pop(task_id, None)

@app.websocket("/api/v1/documents/progress")
async def websocket_processing_logs(websocket: WebSocket):
    """文档解析过程日志WebSocket端点 - 连接到LightRAG实时日志"""
    # Check origin header for CORS compliance
    origin = websocket.headers.get("origin")
    logger.info(f"WebSocket connection attempt from origin: {origin}")
    
    # Accept all origins (similar to CORS middleware configuration)
    await websocket.accept()
    
    # 添加到智能日志处理器和处理日志WebSocket列表
    websocket_log_handler.add_websocket_client(websocket)
    processing_log_websockets.append(websocket)
    
    print(f"[DEBUG] WebSocket connected! Total connections: {len(processing_log_websockets)}")
    
    try:
        # 立即发送测试消息
        test_message = {
            "type": "log",
            "level": "info", 
            "message": "🎯 WebSocket连接测试消息 - 如果您看到这个，说明连接正常！",
            "timestamp": datetime.now().isoformat(),
            "source": "websocket_test"
        }
        await websocket.send_text(json.dumps(test_message))
        print(f"[DEBUG] Test message sent successfully")
        
        # 发送连接确认 - 通过新的日志系统
        await send_processing_log("WebSocket连接已建立，准备接收LightRAG实时日志...", "info")
        
        # 保持连接
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.error(f"处理日志WebSocket错误: {e}")
    finally:
        # 从智能日志处理器和处理日志WebSocket列表中移除
        websocket_log_handler.remove_websocket_client(websocket)
        if websocket in processing_log_websockets:
            processing_log_websockets.remove(websocket)

@app.post("/api/v1/test/websocket-log")
async def test_websocket_log():
    """测试WebSocket日志发送"""
    test_message = "🧪 WebSocket测试消息 - " + datetime.now().strftime("%H:%M:%S")
    print(f"[DEBUG] Testing WebSocket with message: {test_message}")
    print(f"[DEBUG] processing_log_websockets count: {len(processing_log_websockets)}")
    print(f"[DEBUG] websocket_log_handler.websocket_clients count: {len(websocket_log_handler.websocket_clients)}")
    
    await send_processing_log(test_message, "info")
    
    return {
        "success": True,
        "message": "Test message sent",
        "websocket_count": len(processing_log_websockets),
        "handler_count": len(websocket_log_handler.websocket_clients)
    }

@app.get("/api/v1/batch-operations/{batch_operation_id}", response_model=BatchOperationStatus)
async def get_batch_operation_status(batch_operation_id: str):
    """获取批量操作状态"""
    if batch_operation_id not in batch_operations:
        raise HTTPException(status_code=404, detail="Batch operation not found")
    
    batch_operation = batch_operations[batch_operation_id]
    
    return BatchOperationStatus(
        batch_operation_id=batch_operation["batch_operation_id"],
        operation_type=batch_operation["operation_type"],
        status=batch_operation["status"],
        total_items=batch_operation["total_items"],
        completed_items=batch_operation["completed_items"],
        failed_items=batch_operation["failed_items"],
        progress=batch_operation["progress"],
        started_at=batch_operation["started_at"],
        completed_at=batch_operation.get("completed_at"),
        results=batch_operation.get("results", [])
    )

@app.get("/api/v1/batch-operations")
async def list_batch_operations(limit: int = 50, status: Optional[str] = None):
    """列出批量操作"""
    operations = list(batch_operations.values())
    
    # 按状态过滤
    if status:
        operations = [op for op in operations if op["status"] == status]
    
    # 按开始时间倒序排序
    operations.sort(key=lambda x: x["started_at"], reverse=True)
    
    # 限制返回数量
    operations = operations[:limit]
    
    return {
        "success": True,
        "operations": operations,
        "total": len(operations)
    }

@app.get("/api/v1/cache/statistics")
async def get_cache_statistics():
    """获取缓存统计信息"""
    global cache_enhanced_processor
    
    if not cache_enhanced_processor:
        return {
            "success": False,
            "error": "Cache enhanced processor not initialized"
        }
    
    try:
        stats = cache_enhanced_processor.get_cache_statistics()
        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"获取缓存统计失败: {e}")
        return {
            "success": False,
            "error": f"获取缓存统计失败: {str(e)}"
        }

@app.get("/api/v1/cache/activity")
async def get_cache_activity(limit: int = 50):
    """获取缓存活动记录"""
    global cache_enhanced_processor
    
    if not cache_enhanced_processor:
        return {
            "success": False,
            "error": "Cache enhanced processor not initialized"
        }
    
    try:
        activity = cache_enhanced_processor.get_cache_activity(limit)
        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "activity": activity
        }
    except Exception as e:
        logger.error(f"获取缓存活动失败: {e}")
        return {
            "success": False,
            "error": f"获取缓存活动失败: {str(e)}"
        }

@app.get("/api/v1/cache/status")
async def get_cache_status():
    """获取缓存状态信息"""
    global cache_enhanced_processor
    
    if not cache_enhanced_processor:
        return {
            "success": False,
            "error": "Cache enhanced processor not initialized"
        }
    
    try:
        cache_status = cache_enhanced_processor.is_cache_enabled()
        cache_stats = cache_enhanced_processor.get_cache_statistics()
        
        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "cache_status": cache_status,
            "quick_stats": {
                "total_operations": cache_stats.get("overall_statistics", {}).get("total_operations", 0),
                "hit_ratio": cache_stats.get("overall_statistics", {}).get("hit_ratio_percent", 0),
                "time_saved": cache_stats.get("overall_statistics", {}).get("total_time_saved_seconds", 0),
                "efficiency": cache_stats.get("overall_statistics", {}).get("efficiency_improvement_percent", 0),
                "health": cache_stats.get("cache_health", {}).get("status", "unknown")
            }
        }
    except Exception as e:
        logger.error(f"获取缓存状态失败: {e}")
        return {
            "success": False,
            "error": f"获取缓存状态失败: {str(e)}"
        }

@app.get("/api/v1/batch-progress/{batch_id}")
async def get_batch_progress(batch_id: str):
    """获取批量操作的实时进度"""
    progress = advanced_progress_tracker.get_batch_progress(batch_id)
    if not progress:
        raise HTTPException(status_code=404, detail="批量操作不存在或已完成")
    
    return {
        "success": True,
        "batch_progress": progress,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/v1/batch-progress")
async def get_all_batch_progress():
    """获取所有活跃批量操作的进度"""
    active_batches = advanced_progress_tracker.get_all_active_batches()
    progress_history = advanced_progress_tracker.get_progress_history(limit=20)
    
    return {
        "success": True,
        "active_batches": active_batches,
        "recent_history": progress_history,
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/v1/batch-progress/{batch_id}/cancel")
async def cancel_batch_progress(batch_id: str):
    """取消批量操作"""
    try:
        await advanced_progress_tracker.cancel_batch(batch_id)
        return {
            "success": True,
            "message": f"批量操作 {batch_id} 已取消",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"取消批量操作失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消操作失败: {str(e)}")

@app.get("/api/v1/system/health")
async def get_enhanced_system_health():
    """获取增强的系统健康状态"""
    try:
        # 获取基本系统指标
        metrics = get_system_metrics()
        
        # 获取错误处理器的健康警告
        health_warnings = enhanced_error_handler.get_system_health_warnings()
        
        # 检查GPU状态
        gpu_status = "unavailable"
        gpu_memory_info = {}
        if TORCH_AVAILABLE:
            try:
                if torch.cuda.is_available():
                    gpu_status = "available"
                    gpu_memory_info = {
                        "total": torch.cuda.get_device_properties(0).total_memory,
                        "allocated": torch.cuda.memory_allocated(),
                        "cached": torch.cuda.memory_reserved()
                    }
            except Exception as e:
                gpu_status = f"error: {str(e)}"
        
        # 检查存储空间
        storage_warnings = []
        try:
            working_disk = psutil.disk_usage(WORKING_DIR)
            output_disk = psutil.disk_usage(OUTPUT_DIR)
            upload_disk = psutil.disk_usage(UPLOAD_DIR)
            
            for name, disk_info, path in [
                ("工作目录", working_disk, WORKING_DIR),
                ("输出目录", output_disk, OUTPUT_DIR),
                ("上传目录", upload_disk, UPLOAD_DIR)
            ]:
                free_gb = disk_info.free / (1024**3)
                if free_gb < 5.0:  # Less than 5GB free
                    storage_warnings.append(f"{name} ({path}) 存储空间不足: {free_gb:.1f}GB")
        except Exception as e:
            storage_warnings.append(f"无法检查存储空间: {str(e)}")
        
        # 综合健康评分
        health_score = 100.0
        issues = []
        
        if metrics["memory_usage"] > 85:
            health_score -= 20
            issues.append("内存使用率过高")
        elif metrics["memory_usage"] > 70:
            health_score -= 10
            issues.append("内存使用率较高")
        
        if metrics["cpu_usage"] > 90:
            health_score -= 15
            issues.append("CPU使用率过高")
        elif metrics["cpu_usage"] > 75:
            health_score -= 8
            issues.append("CPU使用率较高")
        
        if metrics["disk_usage"] > 90:
            health_score -= 25
            issues.append("磁盘使用率过高")
        elif metrics["disk_usage"] > 80:
            health_score -= 12
            issues.append("磁盘使用率较高")
        
        if health_warnings:
            health_score -= len(health_warnings) * 5
            issues.extend(health_warnings)
        
        if storage_warnings:
            health_score -= len(storage_warnings) * 10
            issues.extend(storage_warnings)
        
        health_score = max(0, health_score)
        
        # 确定整体状态
        if health_score >= 85:
            overall_status = "excellent"
        elif health_score >= 70:
            overall_status = "good"
        elif health_score >= 50:
            overall_status = "warning"
        else:
            overall_status = "critical"
        
        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "overall_status": overall_status,
            "health_score": round(health_score, 1),
            "system_metrics": metrics,
            "gpu_status": gpu_status,
            "gpu_memory": gpu_memory_info,
            "storage_warnings": storage_warnings,
            "health_warnings": health_warnings,
            "issues": issues,
            "recommendations": [
                "定期清理临时文件和缓存" if metrics["disk_usage"] > 70 else None,
                "考虑增加系统内存" if metrics["memory_usage"] > 80 else None,
                "检查系统负载和后台进程" if metrics["cpu_usage"] > 80 else None,
                "监控GPU温度和使用情况" if gpu_status == "available" else None
            ],
            "processing_stats": {
                "active_batches": len(advanced_progress_tracker.get_all_active_batches()),
                "cache_enabled": cache_enhanced_processor.is_cache_enabled() if cache_enhanced_processor else {},
                "error_handler_status": "active"
            }
        }
    except Exception as e:
        logger.error(f"获取系统健康状态失败: {e}")
        return {
            "success": False,
            "error": f"获取系统健康状态失败: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }

@app.post("/api/v1/cache/clear")
async def clear_cache_statistics():
    """清除缓存统计数据"""
    global cache_enhanced_processor
    
    if not cache_enhanced_processor:
        return {
            "success": False,
            "error": "Cache enhanced processor not initialized"
        }
    
    try:
        cache_enhanced_processor.clear_cache_statistics()
        logger.info("缓存统计数据已清除")
        return {
            "success": True,
            "message": "缓存统计数据已清除",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"清除缓存统计失败: {e}")
        return {
            "success": False,
            "error": f"清除缓存统计失败: {str(e)}"
        }

# ===== 图谱可视化API端点 =====

@app.get("/api/v1/graph/nodes")
async def get_graph_nodes(limit: int = 100):
    """获取知识图谱节点数据"""
    try:
        rag = await initialize_rag()
        if not rag:
            raise HTTPException(status_code=503, detail="RAG系统未初始化")
        
        # 检查存储模式和数据库配置
        db_config = load_database_config()
        nodes = []
        
        if db_config.storage_mode in ["hybrid", "neo4j_only"]:
            # 从Neo4j获取节点数据
            try:
                from neo4j import GraphDatabase
                
                driver = GraphDatabase.driver(
                    db_config.neo4j_uri,
                    auth=(db_config.neo4j_username, db_config.neo4j_password)
                )
                
                with driver.session() as session:
                    # 获取实体节点
                    result = session.run(f"""
                        MATCH (n)
                        RETURN id(n) as node_id, labels(n) as labels, n as properties
                        LIMIT {limit}
                    """)
                    
                    for record in result:
                        node_data = {
                            "id": str(record["node_id"]),
                            "label": record["properties"].get("name", record["properties"].get("id", "Unknown")),
                            "type": record["labels"][0] if record["labels"] else "Entity",
                            "properties": dict(record["properties"])
                        }
                        nodes.append(node_data)
                
                driver.close()
                
            except Exception as e:
                logger.warning(f"Neo4j查询失败，尝试PostgreSQL备用方案: {e}")
                # 备用：从PostgreSQL获取数据
                nodes = _get_nodes_from_postgres(limit)
        elif db_config.storage_mode == "postgres_only":
            # 直接从PostgreSQL获取节点数据
            nodes = _get_nodes_from_postgres(limit)
        else:
            # 从文件存储获取节点数据
            nodes = _get_nodes_from_file_storage(limit)
        
        return {
            "success": True,
            "nodes": nodes,
            "total": len(nodes),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取图谱节点失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取图谱节点失败: {str(e)}")

@app.get("/api/v1/graph/relationships") 
async def get_graph_relationships(limit: int = 100):
    """获取知识图谱关系数据"""
    try:
        rag = await initialize_rag()
        if not rag:
            raise HTTPException(status_code=503, detail="RAG系统未初始化")
        
        # 检查存储模式和数据库配置
        db_config = load_database_config()
        relationships = []
        
        if db_config.storage_mode in ["hybrid", "neo4j_only"]:
            # 从Neo4j获取关系数据
            try:
                from neo4j import GraphDatabase
                
                driver = GraphDatabase.driver(
                    db_config.neo4j_uri,
                    auth=(db_config.neo4j_username, db_config.neo4j_password)
                )
                
                with driver.session() as session:
                    # 获取关系
                    result = session.run(f"""
                        MATCH (a)-[r]->(b)
                        RETURN id(a) as source_id, id(b) as target_id, 
                               type(r) as relationship_type, r as properties
                        LIMIT {limit}
                    """)
                    
                    for record in result:
                        rel_data = {
                            "source": str(record["source_id"]),
                            "target": str(record["target_id"]),
                            "type": record["relationship_type"],
                            "properties": dict(record["properties"])
                        }
                        relationships.append(rel_data)
                
                driver.close()
                
            except Exception as e:
                logger.warning(f"Neo4j关系查询失败，尝试PostgreSQL备用方案: {e}")
                # 备用：从PostgreSQL获取数据
                relationships = _get_relationships_from_postgres(limit)
        elif db_config.storage_mode == "postgres_only":
            # 直接从PostgreSQL获取关系数据
            relationships = _get_relationships_from_postgres(limit)
        else:
            # 从文件存储获取关系数据
            relationships = _get_relationships_from_file_storage(limit)
        
        return {
            "success": True,
            "relationships": relationships,
            "total": len(relationships),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取图谱关系失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取图谱关系失败: {str(e)}")

@app.get("/api/v1/graph/subgraph/{entity_name}")
async def get_entity_subgraph(entity_name: str, depth: int = 2):
    """获取特定实体的子图"""
    try:
        rag = await initialize_rag()
        if not rag:
            raise HTTPException(status_code=503, detail="RAG系统未初始化")
        
        # 检查存储模式和数据库配置
        db_config = load_database_config()
        nodes = []
        relationships = []
        
        if db_config.storage_mode in ["hybrid", "neo4j_only"]:
            # 从Neo4j获取子图数据
            try:
                from neo4j import GraphDatabase
                
                driver = GraphDatabase.driver(
                    db_config.neo4j_uri,
                    auth=(db_config.neo4j_username, db_config.neo4j_password)
                )
                
                with driver.session() as session:
                    # 获取以指定实体为中心的子图
                    result = session.run(f"""
                        MATCH path = (center)-[*1..{depth}]-(connected)
                        WHERE center.name = $entity_name OR center.id = $entity_name
                        WITH nodes(path) as path_nodes, relationships(path) as path_rels
                        UNWIND path_nodes as n
                        RETURN DISTINCT id(n) as node_id, labels(n) as labels, n as properties
                    """, entity_name=entity_name)
                    
                    node_ids = set()
                    for record in result:
                        node_id = str(record["node_id"])
                        if node_id not in node_ids:
                            node_data = {
                                "id": node_id,
                                "label": record["properties"].get("name", record["properties"].get("id", "Unknown")),
                                "type": record["labels"][0] if record["labels"] else "Entity", 
                                "properties": dict(record["properties"])
                            }
                            nodes.append(node_data)
                            node_ids.add(node_id)
                    
                    # 获取子图中的关系
                    if node_ids:
                        result = session.run(f"""
                            MATCH (a)-[r]->(b)
                            WHERE id(a) IN $node_ids AND id(b) IN $node_ids
                            RETURN id(a) as source_id, id(b) as target_id,
                                   type(r) as relationship_type, r as properties
                        """, node_ids=list(map(int, node_ids)))
                        
                        for record in result:
                            rel_data = {
                                "source": str(record["source_id"]),
                                "target": str(record["target_id"]),
                                "type": record["relationship_type"],
                                "properties": dict(record["properties"])
                            }
                            relationships.append(rel_data)
                
                driver.close()
                
            except Exception as e:
                logger.warning(f"Neo4j子图查询失败: {e}")
                return {
                    "success": False,
                    "error": f"子图查询失败: {str(e)}",
                    "nodes": [],
                    "relationships": []
                }
        else:
            # 备用：从文件存储获取相关数据
            nodes, relationships = _get_subgraph_from_file_storage(entity_name, depth)
        
        return {
            "success": True,
            "entity": entity_name,
            "depth": depth,
            "nodes": nodes,
            "relationships": relationships,
            "node_count": len(nodes),
            "relationship_count": len(relationships),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取实体子图失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取实体子图失败: {str(e)}")

def _get_nodes_from_postgres(limit: int = 100):
    """从PostgreSQL获取节点数据"""
    nodes = []
    try:
        import psycopg2
        import json
        
        # 从环境变量获取连接信息
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", 5432),
            database=os.getenv("POSTGRES_DATABASE", "raganything"),
            user=os.getenv("POSTGRES_USER", "ragsvr"),
            password=os.getenv("POSTGRES_PASSWORD", "ragsvr123")
        )
        
        cur = conn.cursor()
        
        # 查询entities表 - lightrag_vdb_entity存储实体向量数据
        # 使用实际的列名: id, entity_name, content
        cur.execute("""
            SELECT id, entity_name, content, workspace, file_path
            FROM lightrag_vdb_entity 
            LIMIT %s
        """, (limit,))
        
        for row in cur.fetchall():
            entity_id = row[0]
            entity_name = row[1] if row[1] else "Unknown"
            content = row[2] if row[2] else ""
            workspace = row[3] if row[3] else ""
            file_path = row[4] if row[4] else ""
            
            # 创建节点数据
            node_data = {
                "id": entity_id,
                "label": entity_name,
                "type": "Entity",  # 可以从content中解析更多类型信息
                "properties": {
                    "name": entity_name,
                    "content": content[:500] if content else "",  # 限制内容长度
                    "workspace": workspace,
                    "file_path": file_path,
                    "description": content[:200] if content else ""  # 简短描述
                }
            }
            nodes.append(node_data)
        
        cur.close()
        conn.close()
        
        logger.info(f"从PostgreSQL获取了 {len(nodes)} 个节点")
        
    except Exception as e:
        logger.error(f"从PostgreSQL读取节点失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    return nodes

def _get_nodes_from_file_storage(limit: int = 100):
    """从文件存储获取节点数据（备用方案）"""
    nodes = []
    try:
        # Phase 2: Use temporary directory for file-based fallback
        entities_file = os.path.join(TEMP_WORKING_DIR, "vdb_entities.json")
        if os.path.exists(entities_file):
            with open(entities_file, 'r', encoding='utf-8') as f:
                entities_data = json.load(f)
                entity_list = entities_data.get("data", [])
                
                for i, entity in enumerate(entity_list[:limit]):
                    if isinstance(entity, list) and len(entity) >= 2:
                        # entity format: [id, entity_name, entity_type, description, content]
                        node_data = {
                            "id": str(i),
                            "label": entity[1] if len(entity) > 1 else "Unknown",
                            "type": entity[2] if len(entity) > 2 else "Entity",
                            "properties": {
                                "name": entity[1] if len(entity) > 1 else "Unknown",
                                "description": entity[3] if len(entity) > 3 else "",
                                "content": entity[4] if len(entity) > 4 else ""
                            }
                        }
                        nodes.append(node_data)
    except Exception as e:
        logger.error(f"从文件存储读取节点失败: {e}")
    
    return nodes

def _get_relationships_from_postgres(limit: int = 100):
    """从PostgreSQL获取关系数据"""
    relationships = []
    try:
        import psycopg2
        import json
        
        # 从环境变量获取连接信息
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", 5432),
            database=os.getenv("POSTGRES_DATABASE", "raganything"),
            user=os.getenv("POSTGRES_USER", "ragsvr"),
            password=os.getenv("POSTGRES_PASSWORD", "ragsvr123")
        )
        
        cur = conn.cursor()
        
        # 首先获取所有实体名称到ID的映射
        cur.execute("""
            SELECT id, entity_name 
            FROM lightrag_vdb_entity
        """)
        
        entity_name_to_id = {}
        for row in cur.fetchall():
            entity_id = row[0]
            entity_name = row[1]
            if entity_name:
                entity_name_to_id[entity_name] = entity_id
        
        logger.info(f"加载了 {len(entity_name_to_id)} 个实体名称到ID的映射")
        
        # 查询relationships表 - lightrag_vdb_relation存储关系向量数据
        # 使用实际的列名: id, source_id, target_id, content
        cur.execute("""
            SELECT id, source_id, target_id, content, workspace, file_path
            FROM lightrag_vdb_relation 
            LIMIT %s
        """, (limit,))
        
        for row in cur.fetchall():
            rel_id = row[0]
            source_name = row[1] if row[1] else ""
            target_name = row[2] if row[2] else ""
            content = row[3] if row[3] else ""
            workspace = row[4] if row[4] else ""
            file_path = row[5] if row[5] else ""
            
            # 将实体名称映射到实体ID
            source_id = entity_name_to_id.get(source_name, source_name)
            target_id = entity_name_to_id.get(target_name, target_name)
            
            # 从content中尝试解析关系类型
            # content通常包含关系描述，例如: "source_entity -> relationship_type -> target_entity"
            relationship_type = "RELATED_TO"  # 默认关系类型
            if content and "->" in content:
                parts = content.split("->")
                if len(parts) >= 3:
                    relationship_type = parts[1].strip()
            elif content and "\t" in content:
                # 处理tab分隔的格式: "source\ttarget\nrelation_type\ndescription"
                lines = content.split("\n")
                if len(lines) > 1:
                    relationship_type = lines[1].strip() if lines[1] else "RELATED_TO"
            
            relationship = {
                "source": source_id,  # 使用实体ID而不是名称
                "target": target_id,  # 使用实体ID而不是名称
                "type": relationship_type,
                "properties": {
                    "source_name": source_name,  # 保留原始名称
                    "target_name": target_name,  # 保留原始名称
                    "content": content[:500] if content else "",  # 限制内容长度
                    "workspace": workspace,
                    "file_path": file_path,
                    "description": content[:200] if content else "",
                    "weight": 1.0  # 默认权重
                }
            }
            relationships.append(relationship)
        
        cur.close()
        conn.close()
        
        logger.info(f"从PostgreSQL获取了 {len(relationships)} 个关系")
        
    except Exception as e:
        logger.error(f"从PostgreSQL读取关系失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    return relationships

def _get_relationships_from_file_storage(limit: int = 100):
    """从文件存储获取关系数据（备用方案）"""
    relationships = []
    try:
        # Phase 2: Use temporary directory for file-based fallback
        relationships_file = os.path.join(TEMP_WORKING_DIR, "vdb_relationships.json")
        if os.path.exists(relationships_file):
            with open(relationships_file, 'r', encoding='utf-8') as f:
                relationships_data = json.load(f)
                rel_list = relationships_data.get("data", [])
                
                for i, rel in enumerate(rel_list[:limit]):
                    if isinstance(rel, list) and len(rel) >= 3:
                        # relationship format: [id, source_entity, target_entity, relationship_type, description, weight]
                        rel_data = {
                            "source": str(hash(rel[1]) % 1000),  # 简化的ID映射
                            "target": str(hash(rel[2]) % 1000),
                            "type": rel[3] if len(rel) > 3 else "RELATED_TO",
                            "properties": {
                                "description": rel[4] if len(rel) > 4 else "",
                                "weight": rel[5] if len(rel) > 5 else 1.0
                            }
                        }
                        relationships.append(rel_data)
    except Exception as e:
        logger.error(f"从文件存储读取关系失败: {e}")
    
    return relationships

def _get_subgraph_from_file_storage(entity_name: str, depth: int = 2):
    """从文件存储获取子图数据（备用方案）"""
    nodes = []
    relationships = []
    
    try:
        # 获取所有节点和关系
        all_nodes = _get_nodes_from_file_storage(1000)
        all_relationships = _get_relationships_from_file_storage(1000)
        
        # 找到中心实体
        center_node = None
        for node in all_nodes:
            if (node["properties"].get("name", "").lower() == entity_name.lower() or 
                node["label"].lower() == entity_name.lower()):
                center_node = node
                break
        
        if not center_node:
            return nodes, relationships
        
        # 简化的子图查找（基于实体名称匹配）
        related_nodes = {center_node["id"]: center_node}
        related_relationships = []
        
        # 找到相关关系
        for rel in all_relationships:
            source_match = any(node["id"] == rel["source"] and 
                             entity_name.lower() in node["label"].lower() 
                             for node in all_nodes)
            target_match = any(node["id"] == rel["target"] and 
                             entity_name.lower() in node["label"].lower() 
                             for node in all_nodes)
            
            if source_match or target_match:
                related_relationships.append(rel)
                
                # 添加相关节点
                for node in all_nodes:
                    if node["id"] == rel["source"] or node["id"] == rel["target"]:
                        related_nodes[node["id"]] = node
        
        nodes = list(related_nodes.values())
        relationships = related_relationships
        
    except Exception as e:
        logger.error(f"从文件存储获取子图失败: {e}")
    
    return nodes, relationships

if __name__ == "__main__":
    print("🚀 Starting RAG-Anything API Server with Enhanced Error Handling & Advanced Progress Tracking")
    print("📋 Available endpoints:")
    print("   🔍 Health: http://127.0.0.1:8001/health")
    print("   📤 Upload: http://127.0.0.1:8001/api/v1/documents/upload") 
    print("   📤 Batch Upload: http://127.0.0.1:8001/api/v1/documents/upload/batch")
    print("   ▶️  Manual Process: http://127.0.0.1:8001/api/v1/documents/{document_id}/process")
    print("   ⚡ Enhanced Batch Process: http://127.0.0.1:8001/api/v1/documents/process/batch")
    print("   📋 Tasks: http://127.0.0.1:8001/api/v1/tasks")
    print("   📊 Detailed Status: http://127.0.0.1:8001/api/v1/tasks/{task_id}/detailed-status")
    print("   📄 Docs: http://127.0.0.1:8001/api/v1/documents")
    print("   🔍 Query: http://127.0.0.1:8001/api/v1/query")
    print("   📊 System Status: http://127.0.0.1:8001/api/system/status")
    print("   📈 Parser Stats: http://127.0.0.1:8001/api/system/parser-stats")
    print("   📋 Batch Operations: http://127.0.0.1:8001/api/v1/batch-operations")
    print("   🔌 WebSocket: ws://127.0.0.1:8001/ws/task/{task_id}")
    print()
    print("📊 Enhanced Progress & Error Tracking:")
    print("   📈 Batch Progress: http://127.0.0.1:8001/api/v1/batch-progress")
    print("   📊 Batch Progress (ID): http://127.0.0.1:8001/api/v1/batch-progress/{batch_id}")
    print("   ❌ Cancel Batch: http://127.0.0.1:8001/api/v1/batch-progress/{batch_id}/cancel")
    print("   🏥 Enhanced Health: http://127.0.0.1:8001/api/v1/system/health")
    print()
    print("💾 Cache Management:")
    print("   📈 Cache Statistics: http://127.0.0.1:8001/api/v1/cache/statistics")
    print("   📊 Cache Status: http://127.0.0.1:8001/api/v1/cache/status")
    print("   📋 Cache Activity: http://127.0.0.1:8001/api/v1/cache/activity")
    print("   🗑️  Clear Cache Stats: http://127.0.0.1:8001/api/v1/cache/clear")
    print()
    print("🔥 Enhanced Features:")
    print("   🛡️  Intelligent error categorization and recovery")
    print("   📊 Real-time progress tracking with ETA calculations")
    print("   💾 Advanced cache performance monitoring")
    print("   🔄 Automatic retry mechanisms with exponential backoff")
    print("   🖥️  GPU memory management and device switching")
    print("   ⚠️  System health warnings and recommendations")
    print("   📈 Performance baseline learning and optimization")
    print("   🎯 User-friendly error messages with solutions")
    print("   📡 WebSocket-based real-time progress updates")
    print("   🏥 Comprehensive system health monitoring")
    print()
    print("🧠 Smart Processing:")
    print("   ⚡ Direct text processing (TXT/MD files)")
    print("   📄 PDF files → MinerU (specialized PDF engine)")
    print("   📊 Office files → Docling (native Office support)")
    print("   🖼️  Image files → MinerU (OCR capability)")
    print("   📈 Real-time parser usage statistics")
    print("   🎯 Manual processing control - upload without auto-processing")
    print("   💾 Intelligent caching with file modification tracking")
    print()
    
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")