#!/usr/bin/env python3
"""
简单的数据库连接和基本操作测试
验证数据库迁移的核心功能
"""

import asyncio
import hashlib
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
import asyncpg
import json

async def test_database_connection():
    """测试数据库连接"""
    print("=== 测试数据库连接 ===")
    
    try:
        # 从环境变量构建数据库连接字符串
        postgres_host = os.getenv('POSTGRES_HOST', '/var/run/postgresql')
        postgres_port = os.getenv('POSTGRES_PORT', '5432')
        postgres_db = os.getenv('POSTGRES_DATABASE', 'raganything')
        postgres_user = os.getenv('POSTGRES_USER', 'ragsvr')
        postgres_password = os.getenv('POSTGRES_PASSWORD', '')
        
        if postgres_host.startswith('/'):  # Unix socket
            db_dsn = f"postgresql://{postgres_user}@/{postgres_db}?host={postgres_host}"
        else:
            db_dsn = f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"
        
        print(f"连接字符串: {db_dsn}")
        
        # 创建连接池
        pool = await asyncpg.create_pool(
            db_dsn,
            min_size=1,
            max_size=5,
            command_timeout=60
        )
        
        print("数据库连接池创建成功")
        
        # 测试基本查询
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT version()")
            print(f"数据库版本: {result}")
            
            # 检查documents表是否存在
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'documents')"
            )
            print(f"documents表存在: {table_exists}")
            
            if table_exists:
                # 检查表结构
                columns = await conn.fetch("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'documents'
                    ORDER BY ordinal_position
                """)
                print("documents表结构:")
                for col in columns:
                    print(f"  {col['column_name']}: {col['data_type']}")
        
        await pool.close()
        return True
        
    except Exception as e:
        print(f"数据库连接失败: {e}")
        return False

async def test_document_crud():
    """测试文档CRUD操作"""
    print("\n=== 测试文档CRUD操作 ===")
    
    try:
        # 连接数据库
        postgres_host = os.getenv('POSTGRES_HOST', '/var/run/postgresql')
        postgres_port = os.getenv('POSTGRES_PORT', '5432') 
        postgres_db = os.getenv('POSTGRES_DATABASE', 'raganything')
        postgres_user = os.getenv('POSTGRES_USER', 'ragsvr')
        postgres_password = os.getenv('POSTGRES_PASSWORD', '')
        
        if postgres_host.startswith('/'):  # Unix socket
            db_dsn = f"postgresql://{postgres_user}@/{postgres_db}?host={postgres_host}"
        else:
            db_dsn = f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"
        
        pool = await asyncpg.create_pool(db_dsn, min_size=1, max_size=5)
        
        async with pool.acquire() as conn:
            # 测试数据
            doc_id = str(uuid.uuid4())
            test_hash = hashlib.sha256(b'test content for database migration').hexdigest()
            
            print(f"测试文档ID: {doc_id}")
            print(f"测试哈希: {test_hash}")
            
            # 创建文档
            print("\n1. 创建文档")
            await conn.execute("""
                INSERT INTO documents (
                    id, workspace, file_name, file_path, file_size, status, created_at, updated_at,
                    task_id, processing_time, content_length, chunks_count, rag_doc_id,
                    content_summary, error_message, batch_operation_id, parser_used, parser_reason, content_hash
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                ON CONFLICT (id, workspace) DO NOTHING
            """, doc_id, 'default', 'test_document.pdf', '/tmp/test_document.pdf', 12345, 
                 'uploaded', datetime.now(), datetime.now(), str(uuid.uuid4()), None,
                 12345, 0, str(uuid.uuid4()), '测试文档摘要', None, None, 
                 'test_parser', '测试原因', test_hash)
            
            print("文档创建成功")
            
            # 查询文档
            print("\n2. 查询文档")
            row = await conn.fetchrow("""
                SELECT id, file_name, file_path, file_size, status, content_hash
                FROM documents WHERE id = $1 AND workspace = 'default'
            """, doc_id)
            
            if row:
                print(f"查询成功: {row['file_name']} ({row['status']}) - 哈希: {row['content_hash'][:16]}...")
            else:
                print("查询失败：文档未找到")
                return False
            
            # 测试哈希重复检查
            print("\n3. 测试哈希重复检查")
            duplicate_row = await conn.fetchrow("""
                SELECT id FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
                LIMIT 1
            """, test_hash)
            
            if duplicate_row and duplicate_row['id'] == doc_id:
                print(f"哈希重复检查成功: 找到重复文档 {duplicate_row['id']}")
            else:
                print("哈希重复检查失败")
                return False
            
            # 更新文档状态
            print("\n4. 更新文档状态")
            await conn.execute("""
                UPDATE documents SET status = $2, updated_at = $3, error_message = $4 
                WHERE id = $1 AND workspace = 'default'
            """, doc_id, 'completed', datetime.now(), None)
            
            # 验证更新
            updated_row = await conn.fetchrow("""
                SELECT status FROM documents WHERE id = $1 AND workspace = 'default'
            """, doc_id)
            
            if updated_row and updated_row['status'] == 'completed':
                print("状态更新成功: completed")
            else:
                print("状态更新失败")
                return False
            
            # 删除文档
            print("\n5. 删除文档")
            result = await conn.execute("""
                DELETE FROM documents WHERE id = $1 AND workspace = 'default'
            """, doc_id)
            
            deleted_count = int(result.split()[-1])
            if deleted_count == 1:
                print("文档删除成功")
            else:
                print("文档删除失败")
                return False
            
            # 验证删除
            deleted_row = await conn.fetchrow("""
                SELECT id FROM documents WHERE id = $1 AND workspace = 'default'
            """, doc_id)
            
            if deleted_row is None:
                print("删除验证成功：文档已不存在")
            else:
                print("删除验证失败：文档仍然存在")
                return False
        
        await pool.close()
        return True
        
    except Exception as e:
        print(f"CRUD操作测试失败: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def test_deduplication_logic():
    """测试去重逻辑"""
    print("\n=== 测试去重逻辑 ===")
    
    try:
        # 连接数据库
        postgres_host = os.getenv('POSTGRES_HOST', '/var/run/postgresql')
        postgres_port = os.getenv('POSTGRES_PORT', '5432')
        postgres_db = os.getenv('POSTGRES_DATABASE', 'raganything')
        postgres_user = os.getenv('POSTGRES_USER', 'ragsvr')
        postgres_password = os.getenv('POSTGRES_PASSWORD', '')
        
        if postgres_host.startswith('/'):  # Unix socket
            db_dsn = f"postgresql://{postgres_user}@/{postgres_db}?host={postgres_host}"
        else:
            db_dsn = f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"
        
        pool = await asyncpg.create_pool(db_dsn, min_size=1, max_size=5)
        
        async with pool.acquire() as conn:
            # 测试内容和哈希
            test_content = b'This is a test document for deduplication testing'
            test_hash = hashlib.sha256(test_content).hexdigest()
            
            print(f"测试哈希: {test_hash}")
            
            # 清理可能存在的测试数据
            await conn.execute("""
                DELETE FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
            """, test_hash)
            
            # 创建第一个文档
            doc1_id = str(uuid.uuid4())
            print(f"\n1. 创建第一个文档: {doc1_id}")
            
            await conn.execute("""
                INSERT INTO documents (
                    id, workspace, file_name, file_path, file_size, status, created_at, updated_at,
                    task_id, rag_doc_id, content_hash
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """, doc1_id, 'default', 'document1.pdf', '/tmp/document1.pdf', len(test_content),
                 'uploaded', datetime.now(), datetime.now(), str(uuid.uuid4()), str(uuid.uuid4()), test_hash)
            
            print("第一个文档创建成功")
            
            # 检查去重
            print("\n2. 检查哈希重复")
            duplicate_doc = await conn.fetchrow("""
                SELECT id FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
                LIMIT 1
            """, test_hash)
            
            if duplicate_doc and duplicate_doc['id'] == doc1_id:
                print(f"去重检查成功: 找到文档 {duplicate_doc['id']}")
            else:
                print("去重检查失败: 未找到预期的文档")
                return False
            
            # 模拟第二个相同内容的文档上传（应该被去重检测到）
            print("\n3. 模拟重复文档检测")
            existing_doc = await conn.fetchrow("""
                SELECT id, file_name FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
                LIMIT 1
            """, test_hash)
            
            if existing_doc:
                print(f"发现重复文档: {existing_doc['id']} ({existing_doc['file_name']})")
                print("应该返回现有文档而不是创建新文档")
            else:
                print("重复检测失败: 应该找到重复文档")
                return False
            
            # 测试不同内容的文档
            print("\n4. 测试不同内容的文档")
            different_content = b'This is a different document'
            different_hash = hashlib.sha256(different_content).hexdigest()
            
            no_duplicate = await conn.fetchrow("""
                SELECT id FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
                LIMIT 1
            """, different_hash)
            
            if no_duplicate is None:
                print(f"不同哈希检查成功: 哈希 {different_hash[:16]}... 未找到重复")
            else:
                print("不同哈希检查失败: 意外找到重复文档")
                return False
            
            # 清理测试数据
            print("\n5. 清理测试数据")
            await conn.execute("""
                DELETE FROM documents 
                WHERE content_hash = $1 AND workspace = 'default'
            """, test_hash)
            print("测试数据清理完成")
        
        await pool.close()
        return True
        
    except Exception as e:
        print(f"去重逻辑测试失败: {e}")
        import traceback
        print(traceback.format_exc())
        return False

async def main():
    """主测试函数"""
    print("开始测试数据库存储机制和去重功能")
    print("=" * 60)
    
    # 设置环境变量（如果需要）
    os.environ.setdefault("POSTGRES_HOST", "/var/run/postgresql")
    os.environ.setdefault("POSTGRES_DATABASE", "raganything")
    os.environ.setdefault("POSTGRES_USER", "ragsvr")
    
    try:
        # 测试数据库连接
        test1_result = await test_database_connection()
        print(f"\n数据库连接测试: {'✅ 通过' if test1_result else '❌ 失败'}")
        
        if not test1_result:
            print("数据库连接失败，终止测试")
            return False
        
        # 测试CRUD操作
        test2_result = await test_document_crud()
        print(f"文档CRUD操作测试: {'✅ 通过' if test2_result else '❌ 失败'}")
        
        # 测试去重逻辑
        test3_result = await test_deduplication_logic()
        print(f"去重逻辑测试: {'✅ 通过' if test3_result else '❌ 失败'}")
        
        # 总结
        all_passed = test1_result and test2_result and test3_result
        print("\n" + "=" * 60)
        print(f"测试总结: {'🎉 所有测试通过!' if all_passed else '⚠️ 部分测试失败'}")
        
        if all_passed:
            print("✅ 数据库连接正常")
            print("✅ 基本CRUD操作工作正常") 
            print("✅ 哈希去重功能工作正常")
            print("✅ 数据库存储机制迁移验证成功")
        
        return all_passed
        
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        import traceback
        print(traceback.format_exc())
        return False

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)