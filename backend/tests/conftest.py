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


@pytest.fixture
async def register_user(client: AsyncClient):
    """Factory: register a user and return (auth_headers, user_dict).

    Lets a single test create multiple authenticated identities on the same
    rollback-ed session (e.g. to exercise membership isolation).
    """

    async def _make(
        email: str = "user@hemut.com",
        password: str = "password123",
        display_name: str = "User",
    ) -> tuple[dict[str, str], dict]:
        resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "display_name": display_name},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        return headers, body["user"]

    return _make
