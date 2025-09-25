#!/usr/bin/env python3
"""
实时监控数据库删除操作
监控前端删除文件时对数据库各表的影响
"""

import asyncio
import asyncpg
import os
import sys
from datetime import datetime
from typing import Dict, List
import json

class DatabaseDeleteMonitor:
    def __init__(self):
        # 数据库连接配置
        self.db_dsn = self._get_db_dsn()
        self.monitored_tables = [
            'documents',
            'batch_operations',
            'tasks',
            'lightrag_doc_chunks',
            'lightrag_doc_full',
            'lightrag_doc_status',
            'lightrag_full_entities',
            'lightrag_full_relations',
            'lightrag_llm_cache',
            'lightrag_vdb_chunks',
            'lightrag_vdb_entity',
            'lightrag_vdb_relation',
            'rag_documents',
            'rag_embeddings',
            'rag_entities',
            'rag_kv_store',
            'rag_relationships'
        ]
        
    def _get_db_dsn(self):
        """构建数据库连接字符串"""
        postgres_host = os.getenv('POSTGRES_HOST', '/var/run/postgresql')
        postgres_port = os.getenv('POSTGRES_PORT', '5432')
        postgres_db = os.getenv('POSTGRES_DB', 'raganything')
        postgres_user = os.getenv('POSTGRES_USER', 'ragsvr')
        postgres_password = os.getenv('POSTGRES_PASSWORD', '')
        
        if postgres_host.startswith('/'):
            return f"postgresql://{postgres_user}@/{postgres_db}?host={postgres_host}"
        else:
            return f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"
    
    async def get_table_counts(self) -> Dict[str, int]:
        """获取所有表的记录数"""
        conn = await asyncpg.connect(self.db_dsn)
        counts = {}
        
        for table in self.monitored_tables:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                counts[table] = count
            except Exception as e:
                counts[table] = f"Error: {e}"
        
        await conn.close()
        return counts
    
    async def get_document_samples(self, limit=5) -> List[Dict]:
        """获取文档样本"""
        conn = await asyncpg.connect(self.db_dsn)
        
        samples = await conn.fetch("""
            SELECT id, file_path, status, rag_doc_id, created_at
            FROM documents
            ORDER BY created_at DESC
            LIMIT $1
        """, limit)
        
        await conn.close()
        
        return [dict(s) for s in samples]
    
    async def monitor_continuous(self, interval=2):
        """持续监控数据库变化"""
        print("=" * 80)
        print("数据库删除操作监控器")
        print("=" * 80)
        print(f"监控开始时间: {datetime.now()}")
        print(f"监控间隔: {interval}秒")
        print("\n请在前端执行删除操作...")
        print("按 Ctrl+C 停止监控\n")
        print("-" * 80)
        
        # 获取初始状态
        initial_counts = await self.get_table_counts()
        initial_samples = await self.get_document_samples()
        
        print("初始状态:")
        for table, count in initial_counts.items():
            if isinstance(count, int) and count > 0:
                print(f"  {table}: {count} 条记录")
        
        print("\n最近的文档:")
        for doc in initial_samples[:3]:
            print(f"  ID: {doc['id']}")
            print(f"  文件: {doc['file_path']}")
            print(f"  状态: {doc['status']}")
            print()
        
        print("-" * 80)
        print("监控中... (执行删除操作后会显示变化)")
        print("-" * 80)
        
        previous_counts = initial_counts.copy()
        change_detected = False
        changes_log = []
        
        try:
            while True:
                await asyncio.sleep(interval)
                
                # 获取当前状态
                current_counts = await self.get_table_counts()
                current_samples = await self.get_document_samples()
                
                # 检测变化
                changes = []
                for table in self.monitored_tables:
                    if isinstance(current_counts.get(table), int) and isinstance(previous_counts.get(table), int):
                        diff = current_counts[table] - previous_counts[table]
                        if diff != 0:
                            changes.append({
                                'table': table,
                                'before': previous_counts[table],
                                'after': current_counts[table],
                                'change': diff
                            })
                
                # 如果检测到变化
                if changes:
                    change_detected = True
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"\n[{timestamp}] 检测到数据库变化!")
                    print("=" * 60)
                    
                    for change in changes:
                        change_str = f"+{change['change']}" if change['change'] > 0 else str(change['change'])
                        print(f"表: {change['table']}")
                        print(f"  变化: {change['before']} → {change['after']} ({change_str})")
                        
                        # 记录变化
                        changes_log.append({
                            'timestamp': timestamp,
                            'table': change['table'],
                            'before': change['before'],
                            'after': change['after'],
                            'change': change['change']
                        })
                    
                    # 如果documents表有变化，显示具体删除的文档
                    if any(c['table'] == 'documents' for c in changes):
                        print("\n检查删除的文档...")
                        
                        # 找出被删除的文档ID
                        old_ids = {doc['id'] for doc in initial_samples}
                        new_ids = {doc['id'] for doc in current_samples}
                        deleted_ids = old_ids - new_ids
                        
                        if deleted_ids:
                            print(f"已删除的文档ID: {deleted_ids}")
                    
                    print("=" * 60)
                    
                    # 更新前一次的状态
                    previous_counts = current_counts.copy()
                
                # 定期显示心跳
                elif not change_detected:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                    
        except KeyboardInterrupt:
            print("\n\n监控已停止")
            
            if changes_log:
                print("\n" + "=" * 80)
                print("删除操作总结")
                print("=" * 80)
                
                # 统计每个表的总变化
                table_summary = {}
                for log in changes_log:
                    table = log['table']
                    if table not in table_summary:
                        table_summary[table] = 0
                    table_summary[table] += log['change']
                
                print("\n受影响的表:")
                for table, total_change in table_summary.items():
                    if total_change < 0:
                        print(f"  {table}: 删除了 {abs(total_change)} 条记录")
                    elif total_change > 0:
                        print(f"  {table}: 新增了 {total_change} 条记录")
                
                # 保存日志
                log_file = f"delete_monitor_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(log_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        'initial_counts': initial_counts,
                        'final_counts': current_counts,
                        'changes_log': changes_log,
                        'summary': table_summary
                    }, f, indent=2, default=str)
                
                print(f"\n日志已保存到: {log_file}")
            else:
                print("\n未检测到任何删除操作")

async def main():
    monitor = DatabaseDeleteMonitor()
    await monitor.monitor_continuous(interval=1)  # 每秒检查一次

if __name__ == "__main__":
    # 设置环境变量
    os.environ.setdefault("POSTGRES_HOST", "/var/run/postgresql")
    os.environ.setdefault("POSTGRES_DB", "raganything")
    os.environ.setdefault("POSTGRES_USER", "ragsvr")
    
    print("启动数据库删除操作监控器...")
    
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()