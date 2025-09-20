#!/usr/bin/env python3
"""
原子性文档处理器
提供事务性文档处理，支持失败时的自动回滚
"""

import json
import os
import logging
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from contextlib import contextmanager
import traceback

logger = logging.getLogger(__name__)

class DocumentProcessingTransaction:
    """文档处理事务管理器"""
    
    def __init__(self, storage_dir: str = None):
        self.storage_dir = Path(storage_dir or os.environ.get("WORKING_DIR", "./rag_storage"))
        self.rollback_operations = []
        self.completed_operations = []
        self.transaction_id = str(uuid.uuid4())
        
        # 存储文件路径
        self.api_state_file = self.storage_dir / "api_documents_state.json"
        self.doc_status_file = self.storage_dir / "kv_store_doc_status.json"
        self.full_docs_file = self.storage_dir / "kv_store_full_docs.json"
        self.text_chunks_file = self.storage_dir / "kv_store_text_chunks.json"
        
        # 事务日志文件
        self.transaction_log_dir = self.storage_dir / "transaction_logs"
        self.transaction_log_dir.mkdir(exist_ok=True)
        self.transaction_log_file = self.transaction_log_dir / f"transaction_{self.transaction_id}.json"
        
    def log_operation(self, operation: str, details: Dict[str, Any]):
        """记录事务操作"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'operation': operation,
            'details': details,
            'transaction_id': self.transaction_id
        }
        
        try:
            if self.transaction_log_file.exists():
                with open(self.transaction_log_file, 'r+', encoding='utf-8') as f:
                    logs = json.load(f)
                    logs.append(log_entry)
                    f.seek(0)
                    json.dump(logs, f, ensure_ascii=False, indent=2)
                    f.truncate()
            else:
                with open(self.transaction_log_file, 'w', encoding='utf-8') as f:
                    json.dump([log_entry], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"记录事务操作失败: {e}")
    
    def add_rollback_operation(self, operation: Callable, *args, **kwargs):
        """添加回滚操作"""
        self.rollback_operations.append((operation, args, kwargs))
    
    def create_backup(self, file_path: Path) -> Optional[Path]:
        """创建文件备份"""
        if not file_path.exists():
            return None
            
        backup_path = file_path.parent / f"{file_path.stem}.backup_{self.transaction_id}{file_path.suffix}"
        try:
            shutil.copy2(file_path, backup_path)
            logger.debug(f"创建备份: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"创建备份失败 {file_path}: {e}")
            return None
    
    def restore_backup(self, original_path: Path, backup_path: Path):
        """恢复备份文件"""
        try:
            if backup_path.exists():
                shutil.copy2(backup_path, original_path)
                logger.debug(f"恢复备份: {original_path}")
        except Exception as e:
            logger.error(f"恢复备份失败: {e}")
    
    def cleanup_backups(self):
        """清理事务备份文件"""
        try:
            backup_pattern = f"*.backup_{self.transaction_id}*"
            for backup_file in self.storage_dir.glob(backup_pattern):
                backup_file.unlink()
                logger.debug(f"删除备份文件: {backup_file}")
        except Exception as e:
            logger.error(f"清理备份文件失败: {e}")
    
    def rollback(self):
        """执行回滚操作"""
        logger.info(f"开始回滚事务 {self.transaction_id}")
        
        # 执行回滚操作（逆序执行）
        for operation, args, kwargs in reversed(self.rollback_operations):
            try:
                operation(*args, **kwargs)
                logger.debug(f"回滚操作成功: {operation.__name__}")
            except Exception as e:
                logger.error(f"回滚操作失败 {operation.__name__}: {e}")
        
        # 记录回滚完成
        self.log_operation("rollback_completed", {
            'rollback_operations_count': len(self.rollback_operations)
        })
        
        self.rollback_operations.clear()
    
    def commit(self):
        """提交事务"""
        logger.info(f"提交事务 {self.transaction_id}")
        
        # 清理备份文件
        self.cleanup_backups()
        
        # 记录事务完成
        self.log_operation("transaction_committed", {
            'completed_operations_count': len(self.completed_operations)
        })
        
        self.rollback_operations.clear()
        self.completed_operations.clear()


class AtomicDocumentProcessor:
    """原子性文档处理器 - 使用数据库替代JSON文件"""
    
    def __init__(self, storage_dir: str = None):
        self.storage_dir = Path(storage_dir or os.environ.get("WORKING_DIR", "./rag_storage"))
        
        # 导入StateManager
        from ..core.state_manager import get_state_manager
        self.state_manager = get_state_manager()
        
    @contextmanager
    def transaction(self):
        """事务上下文管理器"""
        transaction = DocumentProcessingTransaction(self.storage_dir)
        try:
            logger.info(f"开始事务 {transaction.transaction_id}")
            transaction.log_operation("transaction_started", {})
            yield transaction
            transaction.commit()
            logger.info(f"事务提交成功 {transaction.transaction_id}")
        except Exception as e:
            logger.error(f"事务执行失败 {transaction.transaction_id}: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            transaction.rollback()
            raise
    
    def safe_json_update(self, transaction: DocumentProcessingTransaction, 
                        file_path: Path, update_func: Callable[[Dict], Dict]) -> bool:
        """安全的JSON文件更新"""
        try:
            # 创建备份
            backup_path = transaction.create_backup(file_path)
            if backup_path:
                transaction.add_rollback_operation(
                    transaction.restore_backup, file_path, backup_path
                )
            
            # 加载现有数据
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}
            
            # 执行更新
            updated_data = update_func(data)
            
            # 保存更新后的数据
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(updated_data, f, ensure_ascii=False, indent=2)
            
            transaction.log_operation("json_file_updated", {
                'file_path': str(file_path),
                'backup_created': backup_path is not None
            })
            
            return True
            
        except Exception as e:
            logger.error(f"JSON文件更新失败 {file_path}: {e}")
            return False
    
    async def create_document_atomically(self, document_info: Dict[str, Any], 
                                  content_list: Optional[List[Dict]] = None) -> bool:
        """原子性创建文档 - 使用数据库存储"""
        from ..core.state_manager import Document
        from datetime import datetime
        
        try:
            # 创建Document对象
            doc = Document(
                document_id=document_info['id'],
                file_name=document_info.get('filename', ''),
                file_path=document_info.get('file_path', ''),
                file_size=document_info.get('file_size', 0),
                status=document_info.get('status', 'uploaded'),
                created_at=document_info.get('created_at', datetime.now().isoformat()),
                updated_at=document_info.get('updated_at', datetime.now().isoformat()),
                task_id=document_info.get('task_id'),
                processing_time=document_info.get('processing_time'),
                content_length=document_info.get('content_length'),
                chunks_count=document_info.get('chunks_count'),
                rag_doc_id=document_info.get('rag_doc_id', str(uuid.uuid4())),
                content_summary=document_info.get('content_summary'),
                error_message=document_info.get('error_message'),
                batch_operation_id=document_info.get('batch_operation_id'),
                parser_used=document_info.get('parser_used'),
                parser_reason=document_info.get('parser_reason'),
                content_hash=document_info.get('file_hash')  # 映射file_hash到content_hash
            )
            
            # 使用StateManager添加文档
            await self.state_manager.add_document(doc)
            
            logger.info(f"文档已创建: {doc.document_id} (哈希: {doc.content_hash})")
            return True
            
        except Exception as e:
            logger.error(f"原子性创建文档失败: {e}")
            return False
    
    async def update_document_status_atomically(self, doc_id: str, rag_doc_id: str, 
                                        new_status: str, error_message: str = None) -> bool:
        """原子性更新文档状态 - 使用数据库存储"""
        try:
            # 更新文档状态，包含错误信息（如果有）
            update_kwargs = {
                'rag_doc_id': rag_doc_id
            }
            if error_message:
                update_kwargs['error_message'] = error_message
            
            await self.state_manager.update_document_status(doc_id, new_status, **update_kwargs)
            
            logger.info(f"文档状态已更新: {doc_id} -> {new_status}")
            return True
            
        except Exception as e:
            logger.error(f"原子性更新文档状态失败: {e}")
            return False
    
    async def delete_document_atomically(self, doc_id: str, rag_doc_id: str = None) -> bool:
        """原子性删除文档 - 使用数据库存储"""
        try:
            # 如果未提供rag_doc_id，从数据库获取
            if not rag_doc_id:
                document = await self.state_manager.get_document(doc_id)
                if document:
                    rag_doc_id = document.rag_doc_id
            
            # 从数据库删除文档记录
            deleted = await self.state_manager.remove_document(doc_id)
            
            if deleted:
                logger.info(f"文档已删除: {doc_id} (RAG ID: {rag_doc_id})")
                return True
            else:
                logger.warning(f"文档未找到或删除失败: {doc_id}")
                return False
                
        except Exception as e:
            logger.error(f"原子性删除文档失败: {e}")
            return False
    
    async def batch_process_documents_atomically(self, document_updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """原子性批处理文档 - 使用数据库存储"""
        results = {
            'successful': [],
            'failed': [],
            'total_processed': 0
        }
        
        logger.info(f"开始批处理 {len(document_updates)} 个文档")
        
        for doc_update in document_updates:
            try:
                operation = doc_update.get('operation')
                doc_id = doc_update.get('doc_id')
                
                if operation == 'create':
                    success = await self.create_document_atomically(
                        doc_update['document_info'], 
                        doc_update.get('content_list')
                    )
                    if not success:
                        raise Exception("创建文档失败")
                        
                elif operation == 'update_status':
                    success = await self.update_document_status_atomically(
                        doc_id, doc_update['rag_doc_id'], 
                        doc_update['status'], doc_update.get('error_message')
                    )
                    if not success:
                        raise Exception("更新文档状态失败")
                        
                elif operation == 'delete':
                    success = await self.delete_document_atomically(
                        doc_id, doc_update.get('rag_doc_id')
                    )
                    if not success:
                        raise Exception("删除文档失败")
                
                results['successful'].append(doc_id)
                results['total_processed'] += 1
                
            except Exception as e:
                logger.error(f"批处理文档失败 {doc_id}: {e}")
                results['failed'].append({
                    'doc_id': doc_id,
                    'error': str(e)
                })
                # 不中断批处理，继续处理其他文档
        
        logger.info(f"批处理完成: 成功 {len(results['successful'])} 个, 失败 {len(results['failed'])} 个")
        return results
    
    async def check_duplicate_by_hash(self, file_hash: str) -> Optional[str]:
        """检查是否存在相同哈希的文档 - 使用数据库存储"""
        try:
            return await self.state_manager.check_duplicate_by_hash(file_hash)
        except Exception as e:
            logger.error(f"检查重复文档失败: {e}")
            return None
    
    async def cleanup_failed_documents(self, max_age_hours: int = 24) -> Dict[str, int]:
        """清理长时间处理失败的文档 - 使用数据库存储"""
        from datetime import datetime, timedelta
        
        cleanup_stats = {
            'checked': 0,
            'cleaned': 0,
            'errors': 0
        }
        
        try:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            logger.info(f"开始清理 {max_age_hours} 小时前的失败文档，截止时间: {cutoff_time}")
            
            # 从数据库获取所有文档
            all_documents = await self.state_manager.get_all_documents()
            
            failed_docs_to_remove = []
            
            for document in all_documents:
                cleanup_stats['checked'] += 1
                
                # 检查是否是失败的文档
                if document.status in ['failed', 'error']:
                    try:
                        # 解析创建时间
                        if isinstance(document.created_at, str):
                            created_time = datetime.fromisoformat(document.created_at.replace('Z', '+00:00'))
                        else:
                            created_time = document.created_at
                        
                        if created_time < cutoff_time:
                            failed_docs_to_remove.append((document.document_id, document.rag_doc_id))
                    except (ValueError, AttributeError) as e:
                        logger.warning(f"无法解析文档创建时间 {document.document_id}: {e}")
            
            # 删除过期的失败文档
            for doc_id, rag_doc_id in failed_docs_to_remove:
                try:
                    success = await self.delete_document_atomically(doc_id, rag_doc_id)
                    if success:
                        cleanup_stats['cleaned'] += 1
                    else:
                        cleanup_stats['errors'] += 1
                except Exception as e:
                    logger.error(f"清理失败文档错误 {doc_id}: {e}")
                    cleanup_stats['errors'] += 1
            
            logger.info(f"清理完成: 检查 {cleanup_stats['checked']} 个文档, 清理 {cleanup_stats['cleaned']} 个失败文档")
                
        except Exception as e:
            logger.error(f"清理失败文档过程错误: {e}")
            cleanup_stats['errors'] += 1
        
        return cleanup_stats


def main():
    """测试和演示函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='原子性文档处理器测试')
    parser.add_argument('--storage-dir', default=None, help='存储目录路径')
    parser.add_argument('--action', choices=['test-create', 'test-update', 'test-delete', 'cleanup'],
                       default='test-create', help='测试操作')
    
    args = parser.parse_args()
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    processor = AtomicDocumentProcessor(args.storage_dir)
    
    if args.action == 'test-create':
        # 测试文档创建
        test_doc = {
            'id': f'test_{uuid.uuid4()}',
            'filename': 'test_document.txt',
            'status': 'uploaded',
            'created_at': datetime.now().isoformat(),
            'file_path': '/tmp/test_document.txt',
            'rag_doc_id': str(uuid.uuid4())
        }
        
        try:
            success = processor.create_document_atomically(test_doc)
            print(f"文档创建测试: {'成功' if success else '失败'}")
        except Exception as e:
            print(f"文档创建测试失败: {e}")
    
    elif args.action == 'cleanup':
        # 清理失败的文档
        stats = processor.cleanup_failed_documents(max_age_hours=1)
        print(f"清理统计: {stats}")


if __name__ == "__main__":
    main()