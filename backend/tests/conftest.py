"""Shared pytest fixtures.

Each test gets an AsyncSession bound to a connection-level transaction that
rolls back at teardown, so every test starts with a clean slate without
needing a separate test DB or truncation scripts.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import get_session
from app.main import app

# NullPool: no connection reuse between tests — avoids async generator teardown races
_test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


@pytest.fixture
async def db_session() -> AsyncSession:
    """Yield a session whose changes are always rolled back after the test."""
    async with _test_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """HTTP client wired to FastAPI; DB writes go to the rollback-ed session."""

    async def _override_get_session():
        # Yield the test session directly — no commit so the rollback fixture wins
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
