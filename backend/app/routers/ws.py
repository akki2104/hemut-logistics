"""WebSocket endpoint — single connection per user, Redis pub/sub fan-out.

Architecture:
  - One WS connection per user; a new connection replaces the old one.
  - JWT auth via ?token= query param (browsers cannot set headers on WS).
  - On connect: load channel memberships → subscribe to Redis channel:{id} topics.
  - A background asyncio.Task fans every Redis event to the user's WebSocket.
  - Ping/pong heartbeat (client sends {"type":"ping"} every 30s) refreshes
    presence TTL so the key stays alive.
  - Presence stored as Redis key  presence:{user_id} = last_seen ISO  TTL=90s.
    Expired key = offline.  Key present within 35s = online, else away.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.auth import decode_access_token
from app.db import async_session_factory, redis_pool
from app.models import Membership
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter()

PRESENCE_TTL = 90  # seconds; 3× heartbeat interval
PRESENCE_KEY_TMPL = "presence:{user_id}"


# ---------------------------------------------------------------------------
# ConnectionManager — module-level singleton
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks live WebSocket connections keyed by user_id.

    Imported by the AI summarization router to stream chunks to the requester
    without publishing to the channel Redis topic.
    """

    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        """Accept ws, closing any existing connection for this user first."""
        old = self._connections.get(user_id)
        if old is not None:
            try:
                await old.close(code=4001)
            except Exception:
                pass
        await ws.accept()
        self._connections[user_id] = ws

    def disconnect(self, user_id: int) -> None:
        self._connections.pop(user_id, None)

    async def send_to(self, user_id: int, data: dict) -> None:
        """Send JSON to one user. Silently no-ops if the user is not connected."""
        ws = self._connections.get(user_id)
        if ws is None:
            return
        try:
            await ws.send_json(data)
        except Exception:
            # Connection broke — clean up so we don't attempt again.
            self.disconnect(user_id)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self._connections


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Presence helpers
# ---------------------------------------------------------------------------


async def _refresh_presence(redis: aioredis.Redis, user_id: int) -> None:
    key = PRESENCE_KEY_TMPL.format(user_id=user_id)
    last_seen = datetime.now(timezone.utc).isoformat()
    await redis.setex(key, PRESENCE_TTL, last_seen)


async def _clear_presence(redis: aioredis.Redis, user_id: int) -> None:
    key = PRESENCE_KEY_TMPL.format(user_id=user_id)
    await redis.delete(key)


# ---------------------------------------------------------------------------
# Redis pub/sub fan-out task
# ---------------------------------------------------------------------------


async def _load_channel_ids(user_id: int) -> list[int]:
    """Return the list of channel_ids the user is a member of."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Membership.channel_id).where(Membership.user_id == user_id)
        )
        return list(result.scalars().all())


async def _subscriber_task(user_id: int, pubsub: aioredis.client.PubSub) -> None:
    """Continuously relay Redis channel events to the user's WebSocket.

    Runs until cancelled (on WS disconnect). The finally block unsubscribes
    and closes the pubsub connection regardless of how the task ends.
    """
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed Redis message for user_id=%d", user_id)
                continue
            await manager.send_to(user_id, data)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Subscriber task crashed for user_id=%d", user_id)
    finally:
        try:
            await pubsub.unsubscribe()
            await pubsub.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: Optional[str] = Query(None, description="JWT access token"),
) -> None:
    """Primary WebSocket for a user.

    Connect: ws://host/ws?token=<JWT>
    Client must send {"type":"ping"} every 30 seconds to keep presence alive.
    Server responds {"type":"pong"}.
    Inbound channel events arrive as {"type":"message","data":{...}}.
    """
    # -- Auth: JWT via query param (browsers can't set WS headers) --
    if not token:
        await ws.close(code=4003, reason="token required")
        return
    try:
        user_id = decode_access_token(token)
    except Exception:
        await ws.close(code=4003, reason="invalid token")
        return

    # -- Accept connection, replacing any prior one for this user --
    await manager.connect(user_id, ws)

    redis: aioredis.Redis = aioredis.Redis(connection_pool=redis_pool)
    pubsub: aioredis.client.PubSub = redis.pubsub()
    subscriber: Optional[asyncio.Task] = None

    try:
        # Stamp presence immediately
        await _refresh_presence(redis, user_id)

        # Subscribe to all channel topics the user is a member of
        channel_ids = await _load_channel_ids(user_id)
        if channel_ids:
            topics = [f"channel:{cid}" for cid in channel_ids]
            await pubsub.subscribe(*topics)
            logger.info(
                "user_id=%d subscribed to %d channel topics", user_id, len(topics)
            )

        # Start background fan-out task
        subscriber = asyncio.create_task(
            _subscriber_task(user_id, pubsub),
            name=f"ws-sub-{user_id}",
        )

        # Acknowledge the connection
        await ws.send_json({"type": "connected", "user_id": user_id})

        # -- Main receive loop: only ping/pong matters here --
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ping":
                await _refresh_presence(redis, user_id)
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for user_id=%d", user_id)
    finally:
        # Cancel and await the subscriber task so pubsub is cleaned up
        if subscriber is not None:
            subscriber.cancel()
            try:
                await subscriber
            except asyncio.CancelledError:
                pass

        # Remove presence on clean disconnect
        try:
            await _clear_presence(redis, user_id)
        except Exception:
            pass

        manager.disconnect(user_id)
        await redis.aclose()
        logger.info("WebSocket disconnected for user_id=%d", user_id)


# ---------------------------------------------------------------------------
# Presence REST endpoint — lets the frontend poll without a second WS
# ---------------------------------------------------------------------------


@router.get("/presence", tags=["presence"])
async def get_presence(
    user_ids: str = Query(..., description="Comma-separated user ids, e.g. 1,2,3"),
) -> dict:
    """Return online/away/offline status for a set of users.

    Thresholds (calibrated to 30s client heartbeat):
      online  — last_seen within 35 s (one missed heartbeat tolerance)
      away    — last_seen within 90 s (still within TTL window)
      offline — Redis key missing (TTL expired or never connected)
    """
    try:
        ids = [int(x.strip()) for x in user_ids.split(",") if x.strip()]
    except ValueError:
        return {"presence": {}}

    redis: aioredis.Redis = aioredis.Redis(connection_pool=redis_pool)
    try:
        now = datetime.now(timezone.utc)
        result: dict[int, str] = {}
        for uid in ids:
            raw = await redis.get(PRESENCE_KEY_TMPL.format(user_id=uid))
            if raw is None:
                result[uid] = "offline"
                continue
            try:
                last_seen = datetime.fromisoformat(raw)
                diff = (now - last_seen).total_seconds()
                result[uid] = "online" if diff <= 35 else "away"
            except ValueError:
                result[uid] = "offline"
        return {"presence": result}
    finally:
        await redis.aclose()
