"""Tests for the WebSocket endpoint and presence REST API.

WebSocket tests use starlette's sync TestClient because httpx does not support
WebSocket connections. The async AsyncClient (from conftest) is used for the
presence REST endpoint. Both sets mock Redis to avoid a real connection.
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from app.auth import create_access_token
from app.main import app


# ---------------------------------------------------------------------------
# Shared async generator that simulates pubsub.listen() blocking indefinitely
# ---------------------------------------------------------------------------


async def _blocking_listen():
    """Mock pubsub.listen() — blocks until the subscriber task is cancelled."""
    try:
        await asyncio.sleep(86400)
    except asyncio.CancelledError:
        return
    if False:
        yield  # noqa: unreachable — but makes Python treat this as an async generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ws_deps(mocker):
    """Patch Redis and _load_channel_ids for WebSocket endpoint tests.

    Uses a sync fixture so it works with both sync TestClient tests and
    async AsyncClient tests in this module.
    """
    mock_redis = AsyncMock()
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.aclose = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.listen = _blocking_listen
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

    mocker.patch("app.routers.ws.aioredis.Redis", return_value=mock_redis)
    mocker.patch("app.routers.ws._load_channel_ids", return_value=[])
    return mock_redis


# ---------------------------------------------------------------------------
# WebSocket auth rejection (sync TestClient)
# ---------------------------------------------------------------------------


def test_ws_no_token_rejected(mock_ws_deps):
    """Connecting without a token must be rejected."""
    with TestClient(app, raise_server_exceptions=False) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/api/ws") as ws:
                ws.receive_json()


def test_ws_invalid_token_rejected(mock_ws_deps):
    """Connecting with a malformed JWT must be rejected."""
    with TestClient(app, raise_server_exceptions=False) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/api/ws?token=not.a.valid.jwt") as ws:
                ws.receive_json()


# ---------------------------------------------------------------------------
# WebSocket success path (sync TestClient)
# ---------------------------------------------------------------------------


def test_ws_connect_receives_connected_event(mock_ws_deps):
    """Valid JWT → server sends {"type":"connected","user_id":...}."""
    token = create_access_token(user_id=999)
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/ws?token={token}") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["user_id"] == 999


def test_ws_ping_pong(mock_ws_deps):
    """Client ping → server pong + presence refreshed."""
    token = create_access_token(user_id=999)
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/ws?token={token}") as ws:
            ws.receive_json()  # consume "connected"
            ws.send_text(json.dumps({"type": "ping"}))
            data = ws.receive_json()
            assert data["type"] == "pong"
            # setex called at least twice: once on connect, once on ping
            assert mock_ws_deps.setex.await_count >= 2


def test_ws_presence_set_on_connect(mock_ws_deps):
    """Connecting must write a presence key to Redis."""
    token = create_access_token(user_id=999)
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/ws?token={token}") as ws:
            ws.receive_json()  # consume "connected"
    mock_ws_deps.setex.assert_awaited()


def test_ws_presence_cleared_on_disconnect(mock_ws_deps):
    """Disconnecting must delete the presence key from Redis."""
    token = create_access_token(user_id=999)
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/ws?token={token}") as ws:
            ws.receive_json()  # consume "connected"
    mock_ws_deps.delete.assert_awaited()


# ---------------------------------------------------------------------------
# Presence REST endpoint (async AsyncClient)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_presence_redis(mocker):
    """Lightweight Redis mock for presence GET tests (no pubsub needed)."""
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()
    mocker.patch("app.routers.ws.aioredis.Redis", return_value=mock_redis)
    return mock_redis


async def test_presence_returns_offline_when_key_missing(
    client: AsyncClient, mock_presence_redis
) -> None:
    mock_presence_redis.get = AsyncMock(return_value=None)
    resp = await client.get("/api/presence?user_ids=1,2")
    assert resp.status_code == 200
    data = resp.json()["presence"]
    assert data["1"] == "offline"
    assert data["2"] == "offline"


async def test_presence_returns_online_for_recent_heartbeat(
    client: AsyncClient, mock_presence_redis
) -> None:
    recent = datetime.now(timezone.utc).isoformat()
    mock_presence_redis.get = AsyncMock(return_value=recent)
    resp = await client.get("/api/presence?user_ids=42")
    assert resp.status_code == 200
    assert resp.json()["presence"]["42"] == "online"


async def test_presence_returns_away_for_stale_heartbeat(
    client: AsyncClient, mock_presence_redis
) -> None:
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    mock_presence_redis.get = AsyncMock(return_value=old)
    resp = await client.get("/api/presence?user_ids=42")
    assert resp.status_code == 200
    assert resp.json()["presence"]["42"] == "away"


async def test_presence_empty_user_ids(
    client: AsyncClient, mock_presence_redis
) -> None:
    resp = await client.get("/api/presence?user_ids=")
    assert resp.status_code == 200
    assert resp.json()["presence"] == {}


async def test_presence_mixed_states(
    client: AsyncClient, mock_presence_redis
) -> None:
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

    async def _side_effect(key: str) -> str | None:
        if "1" in key:
            return recent
        if "2" in key:
            return old
        return None

    mock_presence_redis.get = _side_effect
    resp = await client.get("/api/presence?user_ids=1,2,3")
    assert resp.status_code == 200
    data = resp.json()["presence"]
    assert data["1"] == "online"
    assert data["2"] == "away"
    assert data["3"] == "offline"
