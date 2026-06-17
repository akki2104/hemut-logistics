"""Async SQLAlchemy engine, session factory, and Redis pool."""

import logging
from typing import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

# Shared pool for regular Redis commands (publish, get, setex, etc.)
redis_pool = aioredis.ConnectionPool.from_url(
    settings.REDIS_URL, max_connections=20, decode_responses=True
)

# Dedicated pool for pub/sub connections — socket_timeout=None so listen()
# blocks indefinitely instead of timing out every few seconds on idle channels.
redis_pubsub_pool = aioredis.ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=100,  # one dedicated connection per connected user
    decode_responses=True,
    socket_timeout=None,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency: yields a Redis client from the shared pool."""
    client = aioredis.Redis(connection_pool=redis_pool)
    try:
        yield client
    finally:
        await client.aclose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session with auto commit/rollback."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
