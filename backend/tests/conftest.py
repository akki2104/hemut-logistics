"""Shared pytest fixtures.

Each test gets an AsyncSession bound to a connection-level transaction that
rolls back at teardown, so every test starts with a clean slate without
needing a separate test DB or truncation scripts.
"""

import asyncio
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import get_session, redis_pool
from app.main import app

# Windows defaults to ProactorEventLoop. The app's module-level Redis connection
# pool binds connections to the loop that first used them; pytest-asyncio creates
# a fresh loop per test, so a pooled connection from a prior (now-closed) loop
# raises "Event loop is closed" on reuse under Proactor. SelectorEventLoop tolerates
# this. (Unrelated to DB auth — that was a host port collision, fixed via 5433.)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Tests run against a dedicated, empty `hemut_test` database — never the dev DB,
# whose seeded shipments/users would collide with test fixtures. Derived from
# DATABASE_URL so it tracks host/port/credentials with a single source of truth.
# Create + migrate once: see README "Running tests".
TEST_DATABASE_URL = settings.DATABASE_URL.rsplit("/", 1)[0] + "/hemut_test"

# NullPool: no connection reuse between tests — avoids async generator teardown races.
_test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def _reset_redis_pool():
    """Drop pooled Redis connections after each test.

    The app's module-level `redis_pool` caches connections bound to the event
    loop that opened them. pytest-asyncio runs each test on a fresh loop, so a
    connection left over from a prior (closed) loop raises "Event loop is closed"
    when the next test reuses it. Disconnecting per-test forces a fresh connection
    on the current loop.
    """
    yield
    await redis_pool.disconnect()


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
