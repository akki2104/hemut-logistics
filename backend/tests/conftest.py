"""Shared pytest fixtures.

DB fixtures are added per-feature as auth/channels/messages tests land.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncClient:
    """HTTP client wired to the FastAPI ASGI app — no live server needed."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
