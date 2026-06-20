"""WebSocket endpoint — multiple connections per user, Redis pub/sub fan-out.

Architecture:
  - Multiple WS connections per user are supported (multiple tabs/devices).
  - JWT auth via ?token= query param (browsers cannot set headers on WS).
  - On connect: load channel memberships → subscribe to Redis channel:{id} topics.
  - Each connection gets its own subscriber task that relays Redis events directly
    to that connection's WebSocket — no central fan-out between tabs.
  - Ping/pong heartbeat (client sends {"type":"ping"} every 30s) refreshes
    presence TTL so the key stays alive.
  - Presence stored as Redis key  presence:{user_id} = last_seen ISO  TTL=90s.
    Expired key = offline.  Key present within 35s = online, else away.
    Presence is cleared only when the user's LAST connection closes.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.auth import decode_access_token
from app.db import async_session_factory, redis_pool, redis_pubsub_pool
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

    Supports multiple simultaneous connections per user (multiple tabs/devices).
    Each connection is independent — connect() never evicts an existing socket.
    Imported by the AI service to stream chunks to the requester across all tabs.
    """

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        """Accept ws and add it to the user's connection set.

        Does not close existing connections — all tabs stay live.
        """
        await ws.accept()
        if user_id not in self._connections:
            self._connections[user_id] = set()
        self._connections[user_id].add(ws)

    def disconnect(self, user_id: int, ws: WebSocket) -> None:
        """Remove one connection. Clears the user entry when the last socket closes."""
        sockets = self._connections.get(user_id)
        if sockets is None:
            return
        sockets.discard(ws)
        if not sockets:
            self._connections.pop(user_id, None)

    async def send_to(self, user_id: int, data: dict) -> None:
        """Send JSON to all live sockets for this user (all open tabs).

        Iterates a snapshot so a mid-send disconnect cannot corrupt the set.
        Dead sockets are silently removed.
        """
        for ws in list(self._connections.get(user_id, set())):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(user_id, ws)

    def is_connected(self, user_id: int) -> bool:
        """True if the user has at least one live socket."""
        return bool(self._connections.get(user_id))


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


async def _subscriber_task(user_id: int, ws: WebSocket, topics: list[str]) -> None:
    """Continuously relay Redis channel events to one WebSocket connection.

    Each connection owns its own subscriber task and pub/sub connection. The
    task sends directly to its `ws` — not via manager.send_to — so each tab
    independently receives every event exactly once with no cross-tab fan-out.

    Reconnects with exponential backoff on transient Redis errors. Exits cleanly
    on asyncio.CancelledError (the handler cancels it on WS close). If the
    WebSocket itself dies mid-stream the task exits immediately rather than
    retrying, since there is nothing left to deliver to.
    """
    backoff = 1.0
    while True:
        pubsub: Optional[aioredis.client.PubSub] = None
        try:
            r: aioredis.Redis = aioredis.Redis(connection_pool=redis_pubsub_pool)
            pubsub = r.pubsub()
            if topics:
                await pubsub.subscribe(*topics)
                logger.info(
                    "user_id=%d (re)subscribed to %d topics", user_id, len(topics)
                )
            backoff = 1.0
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Malformed Redis message for user_id=%d", user_id)
                    continue
                try:
                    await ws.send_json(data)
                except Exception:
                    # WebSocket is dead — nothing more to deliver, exit cleanly.
                    return
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning(
                "Subscriber task error for user_id=%d, reconnecting in %.1fs",
                user_id,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if pubsub is not None:
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
    if not token:
        await ws.close(code=4003, reason="token required")
        return
    try:
        user_id = decode_access_token(token)
    except Exception:
        await ws.close(code=4003, reason="invalid token")
        return

    await manager.connect(user_id, ws)

    redis: aioredis.Redis = aioredis.Redis(connection_pool=redis_pool)
    subscriber: Optional[asyncio.Task] = None

    try:
        await _refresh_presence(redis, user_id)

        channel_ids = await _load_channel_ids(user_id)
        topics = [f"channel:{cid}" for cid in channel_ids] + [f"user:{user_id}"]

        subscriber = asyncio.create_task(
            _subscriber_task(user_id, ws, topics),
            name=f"ws-sub-{user_id}",
        )

        await ws.send_json({"type": "connected", "user_id": user_id})

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
        if subscriber is not None:
            subscriber.cancel()
            try:
                await subscriber
            except asyncio.CancelledError:
                pass

        manager.disconnect(user_id, ws)

        # Clear presence only when the last tab/device closes.
        if not manager.is_connected(user_id):
            try:
                await _clear_presence(redis, user_id)
            except Exception:
                pass

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
