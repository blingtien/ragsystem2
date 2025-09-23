"""
Multi-level Caching System for Multimodal Content
Redis for hot data, PostgreSQL for persistent cache
"""

import json
import logging
import pickle
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta
import asyncio
import hashlib

# Try to import optional dependencies
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

logger = logging.getLogger(__name__)

class CacheLevel:
    """Cache level enumeration"""
    MEMORY = "memory"
    REDIS = "redis"
    POSTGRES = "postgres"

class CacheManager:
    """Multi-level cache manager with TTL and invalidation"""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        postgres_config: Dict = None,
        enable_memory_cache: bool = True,
        memory_cache_size: int = 100
    ):
        self.redis_url = redis_url
        self.postgres_config = postgres_config or {}
        self.enable_memory_cache = enable_memory_cache
        self.memory_cache_size = memory_cache_size

        # Memory cache (LRU)
        self.memory_cache = {}
        self.memory_cache_order = []

        # Redis client
        self.redis_client = None

        # PostgreSQL connection
        self.pg_conn = None

        # Cache statistics
        self.stats = {
            'hits': {'memory': 0, 'redis': 0, 'postgres': 0},
            'misses': 0,
            'writes': {'memory': 0, 'redis': 0, 'postgres': 0},
            'evictions': 0
        }

    async def initialize(self):
        """Initialize cache connections"""
        # Initialize Redis if available
        if REDIS_AVAILABLE:
            try:
                self.redis_client = await redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=False
                )
                await self.redis_client.ping()
                logger.info("Redis cache connected")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}, will use fallback")
                self.redis_client = None
        else:
            logger.info("Redis not available, using memory cache only")
            self.redis_client = None

        # Initialize PostgreSQL if available
        if POSTGRES_AVAILABLE and self.postgres_config:
            try:
                self.pg_conn = psycopg2.connect(
                    **self.postgres_config,
                    cursor_factory=RealDictCursor
                )
                await self._create_cache_table()
                logger.info("PostgreSQL cache connected")
            except Exception as e:
                logger.warning(f"PostgreSQL connection failed: {e}, will use fallback")
                self.pg_conn = None
        else:
            logger.info("PostgreSQL not available or not configured")
            self.pg_conn = None

    async def _create_cache_table(self):
        """Create cache table if not exists"""
        if not self.pg_conn:
            return

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS multimodal_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            content_type VARCHAR(50),
            content_data BYTEA,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_cache_expires ON multimodal_cache(expires_at);
        CREATE INDEX IF NOT EXISTS idx_cache_type ON multimodal_cache(content_type);
        CREATE INDEX IF NOT EXISTS idx_cache_accessed ON multimodal_cache(last_accessed);
        """

        with self.pg_conn.cursor() as cursor:
            cursor.execute(create_table_sql)
            self.pg_conn.commit()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache (checks all levels)"""
        # Level 1: Memory cache
        if self.enable_memory_cache:
            value = self._get_from_memory(key)
            if value is not None:
                self.stats['hits']['memory'] += 1
                return value

        # Level 2: Redis cache
        if self.redis_client:
            value = await self._get_from_redis(key)
            if value is not None:
                self.stats['hits']['redis'] += 1
                # Promote to memory cache
                if self.enable_memory_cache:
                    self._set_to_memory(key, value)
                return value

        # Level 3: PostgreSQL cache
        if self.pg_conn:
            value = await self._get_from_postgres(key)
            if value is not None:
                self.stats['hits']['postgres'] += 1
                # Promote to higher levels
                if self.redis_client:
                    await self._set_to_redis(key, value, ttl=3600)
                if self.enable_memory_cache:
                    self._set_to_memory(key, value)
                return value

        self.stats['misses'] += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 3600, content_type: str = None):
        """Set value in cache (writes to all levels)"""
        # Write to all cache levels
        if self.enable_memory_cache:
            self._set_to_memory(key, value)

        if self.redis_client:
            await self._set_to_redis(key, value, ttl)

        if self.pg_conn:
            await self._set_to_postgres(key, value, ttl, content_type)

    def _get_from_memory(self, key: str) -> Optional[Any]:
        """Get from memory cache"""
        if key in self.memory_cache:
            # Move to end (LRU)
            self.memory_cache_order.remove(key)
            self.memory_cache_order.append(key)
            return self.memory_cache[key]['value']
        return None

    def _set_to_memory(self, key: str, value: Any):
        """Set in memory cache with LRU eviction"""
        # Remove if exists
        if key in self.memory_cache:
            self.memory_cache_order.remove(key)

        # Add to cache
        self.memory_cache[key] = {
            'value': value,
            'timestamp': datetime.now()
        }
        self.memory_cache_order.append(key)

        # Evict if necessary
        while len(self.memory_cache_order) > self.memory_cache_size:
            oldest_key = self.memory_cache_order.pop(0)
            del self.memory_cache[oldest_key]
            self.stats['evictions'] += 1

        self.stats['writes']['memory'] += 1

    async def _get_from_redis(self, key: str) -> Optional[Any]:
        """Get from Redis cache"""
        try:
            data = await self.redis_client.get(f"multimodal:{key}")
            if data:
                return pickle.loads(data)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        return None

    async def _set_to_redis(self, key: str, value: Any, ttl: int):
        """Set in Redis cache"""
        try:
            data = pickle.dumps(value)
            await self.redis_client.setex(
                f"multimodal:{key}",
                ttl,
                data
            )
            self.stats['writes']['redis'] += 1
        except Exception as e:
            logger.error(f"Redis set error: {e}")

    async def _get_from_postgres(self, key: str) -> Optional[Any]:
        """Get from PostgreSQL cache"""
        try:
            with self.pg_conn.cursor() as cursor:
                cursor.execute("""
                    SELECT content_data FROM multimodal_cache
                    WHERE cache_key = %s
                    AND (expires_at IS NULL OR expires_at > NOW())
                """, (key,))

                result = cursor.fetchone()
                if result:
                    # Update access count and timestamp
                    cursor.execute("""
                        UPDATE multimodal_cache
                        SET access_count = access_count + 1,
                            last_accessed = NOW()
                        WHERE cache_key = %s
                    """, (key,))
                    self.pg_conn.commit()

                    return pickle.loads(result['content_data'])
        except Exception as e:
            logger.error(f"PostgreSQL get error: {e}")
        return None

    async def _set_to_postgres(self, key: str, value: Any, ttl: int, content_type: str = None):
        """Set in PostgreSQL cache"""
        try:
            data = pickle.dumps(value)
            expires_at = datetime.now() + timedelta(seconds=ttl) if ttl else None

            with self.pg_conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO multimodal_cache
                    (cache_key, content_type, content_data, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE
                    SET content_data = EXCLUDED.content_data,
                        expires_at = EXCLUDED.expires_at,
                        last_accessed = NOW()
                """, (key, content_type, data, expires_at))

                self.pg_conn.commit()
                self.stats['writes']['postgres'] += 1
        except Exception as e:
            logger.error(f"PostgreSQL set error: {e}")

    async def invalidate(self, key: str):
        """Invalidate cache entry at all levels"""
        # Remove from memory
        if key in self.memory_cache:
            del self.memory_cache[key]
            self.memory_cache_order.remove(key)

        # Remove from Redis
        if self.redis_client:
            try:
                await self.redis_client.delete(f"multimodal:{key}")
            except Exception as e:
                logger.error(f"Redis delete error: {e}")

        # Remove from PostgreSQL
        if self.pg_conn:
            try:
                with self.pg_conn.cursor() as cursor:
                    cursor.execute("DELETE FROM multimodal_cache WHERE cache_key = %s", (key,))
                    self.pg_conn.commit()
            except Exception as e:
                logger.error(f"PostgreSQL delete error: {e}")

    async def invalidate_pattern(self, pattern: str):
        """Invalidate cache entries matching pattern"""
        # Clear memory cache entries matching pattern
        keys_to_remove = [k for k in self.memory_cache.keys() if pattern in k]
        for key in keys_to_remove:
            del self.memory_cache[key]
            if key in self.memory_cache_order:
                self.memory_cache_order.remove(key)

        # Clear Redis entries
        if self.redis_client:
            try:
                cursor = '0'
                while cursor != 0:
                    cursor, keys = await self.redis_client.scan(
                        cursor,
                        match=f"multimodal:*{pattern}*"
                    )
                    if keys:
                        await self.redis_client.delete(*keys)
            except Exception as e:
                logger.error(f"Redis pattern delete error: {e}")

        # Clear PostgreSQL entries
        if self.pg_conn:
            try:
                with self.pg_conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM multimodal_cache WHERE cache_key LIKE %s",
                        (f"%{pattern}%",)
                    )
                    self.pg_conn.commit()
            except Exception as e:
                logger.error(f"PostgreSQL pattern delete error: {e}")

    async def cleanup_expired(self):
        """Clean up expired cache entries"""
        # PostgreSQL cleanup
        if self.pg_conn:
            try:
                with self.pg_conn.cursor() as cursor:
                    cursor.execute("""
                        DELETE FROM multimodal_cache
                        WHERE expires_at IS NOT NULL AND expires_at < NOW()
                    """)
                    deleted = cursor.rowcount
                    self.pg_conn.commit()
                    logger.info(f"Cleaned up {deleted} expired cache entries")
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")

    async def warm_cache(self, keys: List[str]):
        """Pre-warm cache with frequently accessed keys"""
        # Load from PostgreSQL to Redis/Memory
        if self.pg_conn:
            try:
                with self.pg_conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT cache_key, content_data
                        FROM multimodal_cache
                        WHERE cache_key = ANY(%s)
                        AND (expires_at IS NULL OR expires_at > NOW())
                        ORDER BY access_count DESC
                        LIMIT 50
                    """, (keys,))

                    for row in cursor.fetchall():
                        value = pickle.loads(row['content_data'])
                        key = row['cache_key']

                        # Promote to higher cache levels
                        if self.redis_client:
                            await self._set_to_redis(key, value, ttl=3600)
                        if self.enable_memory_cache:
                            self._set_to_memory(key, value)

                    logger.info(f"Warmed cache with {cursor.rowcount} entries")
            except Exception as e:
                logger.error(f"Cache warming error: {e}")

    def get_statistics(self) -> Dict:
        """Get cache statistics"""
        total_hits = sum(self.stats['hits'].values())
        total_requests = total_hits + self.stats['misses']
        hit_rate = (total_hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'writes': self.stats['writes'],
            'evictions': self.stats['evictions'],
            'hit_rate': f"{hit_rate:.2f}%",
            'memory_cache_size': len(self.memory_cache),
            'total_requests': total_requests
        }

    async def close(self):
        """Close cache connections"""
        if self.redis_client:
            await self.redis_client.close()

        if self.pg_conn:
            self.pg_conn.close()

class CacheKeyGenerator:
    """Generate consistent cache keys"""

    @staticmethod
    def generate_content_key(content_type: str, content_hash: str) -> str:
        """Generate cache key for content"""
        return f"{content_type}:{content_hash}"

    @staticmethod
    def generate_query_key(query: str, multimodal_hashes: List[str]) -> str:
        """Generate cache key for query"""
        combined = query + ''.join(sorted(multimodal_hashes))
        query_hash = hashlib.sha256(combined.encode()).hexdigest()
        return f"query:{query_hash}"

    @staticmethod
    def generate_embedding_key(content_hash: str, model_name: str) -> str:
        """Generate cache key for embeddings"""
        return f"embed:{model_name}:{content_hash}"