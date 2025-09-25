#!/usr/bin/env python3
"""
Parallel Batch Document Processor
真正的并行批量文档处理器，支持多文档同时处理
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class ParallelBatchProcessor:
    """
    并行批量文档处理器
    支持真正的并发处理，而不是串行处理
    """

    def __init__(self, rag_instance=None, max_workers: int = 3):
        """
        初始化并行处理器

        Args:
            rag_instance: RAGAnything实例
            max_workers: 最大并发工作数（默认3个文档同时处理）
        """
        self.rag_instance = rag_instance
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)

        logger.info(f"并行批量处理器初始化 - 最大并发数: {max_workers}")

    async def process_single_document(
        self,
        file_path: str,
        document_id: str,
        progress_callback: Optional[Callable] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理单个文档（带并发控制）

        Returns:
            处理结果字典
        """
        async with self.semaphore:  # 使用信号量控制并发数
            start_time = datetime.now()
            file_name = Path(file_path).name

            logger.info(f"{'='*60}")
            logger.info(f"[单文档处理] 开始处理: {document_id}")
            logger.info(f"  文件名: {file_name}")
            logger.info(f"  文件路径: {file_path}")
            logger.info(f"  文件大小: {os.path.getsize(file_path) if os.path.exists(file_path) else 0} bytes")
            logger.info(f"{'='*60}")

            try:
                logger.info(f"🚀 开始处理文档: {file_name}")

                # 调用RAG处理
                logger.info(f"  [步骤1] 检查RAG实例...")
                if not self.rag_instance:
                    raise Exception("RAG实例为None")

                if not hasattr(self.rag_instance, 'process_document_complete'):
                    raise Exception("RAG实例没有process_document_complete方法")

                logger.info(f"  [步骤1] ✅ RAG实例检查通过")

                if hasattr(self.rag_instance, 'process_document_complete'):
                    # 使用智能路由选择最优解析器
                    logger.info(f"  [步骤2] 选择最优解析器...")
                    parser = self._select_optimal_parser(file_path)
                    logger.info(f"  [步骤2] ✅ 选择解析器: {parser}")

                    # 报告进度
                    if progress_callback:
                        await progress_callback({
                            "document_id": document_id,
                            "file_name": file_name,
                            "status": "processing",
                            "parser": parser,
                            "message": f"使用{parser}解析器处理中..."
                        })

                    # 执行处理
                    logger.info(f"  [步骤3] 调用RAG处理引擎...")
                    logger.info(f"    调用参数:")
                    logger.info(f"      file_path: {file_path}")
                    for key, value in kwargs.items():
                        logger.info(f"      {key}: {value}")

                    try:
                        # 不传递parser参数，让RAG系统自己决定
                        result = await self.rag_instance.process_document_complete(
                            file_path=file_path,
                            **kwargs
                        )
                        logger.info(f"  [步骤3] ✅ RAG处理完成")
                    except Exception as rag_error:
                        logger.error(f"  [步骤3] ❌ RAG处理失败: {str(rag_error)}")
                        logger.error(f"    异常类型: {type(rag_error).__name__}")
                        import traceback
                        logger.error(f"    异常堆栈:\n{traceback.format_exc()}")
                        raise

                    processing_time = (datetime.now() - start_time).total_seconds()

                    # 报告完成
                    if progress_callback:
                        await progress_callback({
                            "document_id": document_id,
                            "file_name": file_name,
                            "status": "completed",
                            "processing_time": processing_time,
                            "message": f"处理完成，耗时{processing_time:.1f}秒"
                        })

                    logger.info(f"✅ 完成处理: {file_name} ({processing_time:.1f}秒)")
                    logger.info(f"[单文档处理成功] {document_id} - 耗时: {processing_time:.1f}秒")

                    return {
                        "success": True,
                        "document_id": document_id,
                        "file_path": file_path,
                        "processing_time": processing_time,
                        "parser_used": parser,
                        "result": result
                    }
                else:
                    raise Exception("RAG实例未正确初始化或缺少process_document_complete方法")

            except Exception as e:
                processing_time = (datetime.now() - start_time).total_seconds()
                error_msg = str(e)

                logger.error(f"{'='*60}")
                logger.error(f"[单文档处理失败] {document_id}")
                logger.error(f"  文件: {file_name}")
                logger.error(f"  耗时: {processing_time:.1f}秒")
                logger.error(f"  错误类型: {type(e).__name__}")
                logger.error(f"  错误消息: {error_msg}")
                import traceback
                logger.error(f"  完整堆栈:\n{traceback.format_exc()}")
                logger.error(f"{'='*60}")

                # 报告错误
                if progress_callback:
                    await progress_callback({
                        "document_id": document_id,
                        "file_name": file_name,
                        "status": "failed",
                        "error": error_msg,
                        "processing_time": processing_time,
                        "message": f"处理失败: {error_msg}"
                    })

                logger.error(f"❌ 处理失败: {file_name} - {error_msg}")

                return {
                    "success": False,
                    "document_id": document_id,
                    "file_path": file_path,
                    "processing_time": processing_time,
                    "error": error_msg
                }

    def _select_optimal_parser(self, file_path: str) -> str:
        """
        智能选择最优解析器

        Returns:
            解析器名称: "direct_text", "docling", "mineru"
        """
        file_ext = Path(file_path).suffix.lower()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_size_mb = file_size / (1024 * 1024)

        # 纯文本文件直接处理
        if file_ext in ['.txt', '.md', '.rst', '.log', '.csv', '.json', '.xml', '.yaml', '.yml']:
            logger.info(f"📝 选择直接文本处理器: {file_ext}文件")
            return "direct_text"

        # Office文档优先使用Docling
        if file_ext in ['.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls']:
            logger.info(f"📊 选择Docling处理器: Office文档")
            return "docling"

        # PDF文件根据大小和内容选择
        if file_ext == '.pdf':
            # 小PDF文件（<2MB）可能是纯文本PDF，尝试用Docling
            if file_size_mb < 2:
                logger.info(f"📄 选择Docling处理器: 小型PDF ({file_size_mb:.1f}MB)")
                return "docling"
            else:
                # 大PDF或扫描PDF使用MinerU（OCR能力强）
                logger.info(f"🔍 选择MinerU处理器: 大型或扫描PDF ({file_size_mb:.1f}MB)")
                return "mineru"

        # 图片文件使用MinerU（有OCR能力）
        if file_ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif']:
            logger.info(f"🖼️ 选择MinerU处理器: 图片文件")
            return "mineru"

        # 默认使用MinerU
        logger.info(f"🔧 默认选择MinerU处理器")
        return "mineru"

    async def process_batch_parallel(
        self,
        documents: List[Dict[str, str]],  # [{"document_id": "xxx", "file_path": "xxx"}, ...]
        progress_callback: Optional[Callable] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        真正的并行批量处理

        Args:
            documents: 文档列表，每个包含document_id和file_path
            progress_callback: 进度回调函数
            **kwargs: 传递给RAG处理的额外参数

        Returns:
            批量处理结果
        """
        start_time = datetime.now()
        total_documents = len(documents)

        logger.info(f"\n{'#'*80}")
        logger.info(f"[并行批量处理开始]")
        logger.info(f"  文档数量: {total_documents}")
        logger.info(f"  最大并发数: {self.max_workers}")
        logger.info(f"  RAG实例状态: {self.rag_instance is not None}")
        logger.info(f"  文档列表:")
        for i, doc in enumerate(documents):
            logger.info(f"    [{i+1}] {doc['document_id']}: {doc['file_path']}")
        logger.info(f"  额外参数: {kwargs}")
        logger.info(f"{'#'*80}\n")

        # 创建所有处理任务
        tasks = []
        for doc in documents:
            task = asyncio.create_task(
                self.process_single_document(
                    file_path=doc["file_path"],
                    document_id=doc["document_id"],
                    progress_callback=progress_callback,
                    **kwargs
                )
            )
            tasks.append(task)

        # 并行执行所有任务
        logger.info(f"[并行执行] 开始并行执行 {len(tasks)} 个任务...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[并行执行] 所有任务执行完成")

        # 统计结果
        logger.info(f"\n[结果统计]")
        successful = 0
        failed = 0
        total_processing_time = (datetime.now() - start_time).total_seconds()

        processed_results = {}
        for i, result in enumerate(results):
            doc_id = documents[i]["document_id"]

            if isinstance(result, Exception):
                # 处理异常情况
                logger.error(f"  文档 {doc_id}: ❌ 异常 - {str(result)}")
                processed_results[doc_id] = {
                    "success": False,
                    "error": str(result)
                }
                failed += 1
            elif isinstance(result, dict) and result.get("success"):
                logger.info(f"  文档 {doc_id}: ✅ 成功")
                processed_results[doc_id] = result
                successful += 1
            else:
                error_msg = result.get("error", "未知错误") if isinstance(result, dict) else str(result)
                logger.error(f"  文档 {doc_id}: ❌ 失败 - {error_msg}")
                processed_results[doc_id] = result if isinstance(result, dict) else {"success": False, "error": str(result)}
                failed += 1

        # 计算平均处理时间
        avg_time_per_doc = total_processing_time / max(total_documents, 1)

        # 计算并行加速比
        sequential_estimate = avg_time_per_doc * total_documents  # 串行预估时间
        speedup = sequential_estimate / total_processing_time if total_processing_time > 0 else 1.0

        batch_result = {
            "total_documents": total_documents,
            "successful": successful,
            "failed": failed,
            "total_time": total_processing_time,
            "average_time_per_doc": avg_time_per_doc,
            "parallel_speedup": speedup,
            "max_workers_used": self.max_workers,
            "results": processed_results
        }

        logger.info(
            f"📊 批量处理完成:\n"
            f"   - 总文档数: {total_documents}\n"
            f"   - 成功: {successful}\n"
            f"   - 失败: {failed}\n"
            f"   - 总耗时: {total_processing_time:.1f}秒\n"
            f"   - 平均每文档: {avg_time_per_doc:.1f}秒\n"
            f"   - 并行加速比: {speedup:.2f}x"
        )

        return batch_result

    async def process_batch_with_retry(
        self,
        documents: List[Dict[str, str]],
        max_retries: int = 2,
        progress_callback: Optional[Callable] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        带重试机制的批量处理

        Args:
            documents: 文档列表
            max_retries: 失败文档的最大重试次数
            progress_callback: 进度回调
            **kwargs: 额外参数

        Returns:
            批量处理结果
        """
        # 第一轮处理
        result = await self.process_batch_parallel(
            documents, progress_callback, **kwargs
        )

        # 收集失败的文档
        failed_docs = []
        for doc in documents:
            doc_result = result["results"].get(doc["document_id"], {})
            if not doc_result.get("success"):
                failed_docs.append(doc)

        # 重试失败的文档
        retry_count = 0
        while failed_docs and retry_count < max_retries:
            retry_count += 1
            logger.info(f"🔄 重试失败的文档 (第{retry_count}次): {len(failed_docs)}个")

            # 重试处理
            retry_result = await self.process_batch_parallel(
                failed_docs, progress_callback, **kwargs
            )

            # 更新结果
            for doc_id, doc_result in retry_result["results"].items():
                if doc_result.get("success"):
                    result["results"][doc_id] = doc_result
                    result["successful"] += 1
                    result["failed"] -= 1

            # 更新失败列表
            new_failed_docs = []
            for doc in failed_docs:
                if not result["results"][doc["document_id"]].get("success"):
                    new_failed_docs.append(doc)
            failed_docs = new_failed_docs

        return result