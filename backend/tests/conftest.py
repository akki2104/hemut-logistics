"""Shared pytest fixtures.

Each test gets an AsyncSession bound to a connection-level transaction that
rolls back at teardown, so every test starts with a clean slate.

Postgres runs in a temporary Docker container spun up at session start and
torn down when the session ends — no manual database setup required.
"""

import asyncio
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db import get_session, redis_pool
from app.main import app
from app.models import Base

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _create_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(scope="session")
def pg_url():
    """Start a temporary Postgres 15 container for the entire test session.

    Picks a random host port to avoid collisions. Schema is created via
    create_all on a fresh loop so it doesn't interfere with pytest-asyncio's
    per-test loop management. Container is torn down when the session ends.
    """
    with PostgresContainer("postgres:15") as pg:
        url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_create_schema(url))
        loop.close()
        yield url


@pytest.fixture(scope="session")
def test_engine(pg_url):
    return create_async_engine(pg_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def _reset_redis_pool():
    """Drop pooled Redis connections after each test.

    The module-level redis_pool caches connections bound to the event loop
    that opened them. pytest-asyncio creates a fresh loop per test, so
    connections from a prior (closed) loop raise "Event loop is closed".
    Disconnecting per-test forces a fresh connection on the current loop.
    """
    yield
    await redis_pool.disconnect()


@pytest.fixture
async def db_session(test_engine):  # yields AsyncSession
    """Yield a session whose changes are always rolled back after the test."""
    async with test_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest.fixture
async def client(db_session: AsyncSession):  # yields AsyncClient
    """HTTP client wired to FastAPI; DB writes go to the rolled-back session."""

    async def _override_get_session():
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
    rolled-back session (e.g. to exercise membership isolation).
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
