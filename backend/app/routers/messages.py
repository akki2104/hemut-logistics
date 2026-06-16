"""Messages router — post and retrieve channel messages.

POST /api/channels/{channel_id}/messages
    Persists the message, advances the sender's read cursor, then publishes
    to Redis so the WebSocket fan-out layer can deliver it to all members.
    sender_id is always derived from the JWT — never trusted from the body.

GET /api/channels/{channel_id}/messages
    Cursor-based pagination by message id. Supports both directions:
    - ?before_id=N&limit=50  → history scroll-back
    - ?after_id=N&limit=50   → reconnect replay (client sends last seen id)
"""

import json
import logging
from typing import Annotated, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_redis, get_session
from app.models import Channel, Membership, Message, User
from app.schemas import MessageCreate, MessageListOut, MessageOut

logger = logging.getLogger(__name__)

router = APIRouter()

REDIS_CHANNEL_TOPIC = "channel:{channel_id}"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


async def _assert_member(
    session: AsyncSession, channel_id: int, user_id: int
) -> None:
    """Raise 403 if the user is not a member of the channel."""
    result = await session.execute(
        select(Membership.id).where(
            Membership.channel_id == channel_id,
            Membership.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this channel",
        )


@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    channel_id: int,
    body: MessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> MessageOut:
    """Persist a message, advance sender read cursor, publish to Redis."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    await _assert_member(session, channel_id, user.id)

    msg = Message(
        channel_id=channel_id,
        sender_id=user.id,
        content=body.content.strip(),
    )
    session.add(msg)
    await session.flush()  # populates msg.id and msg.created_at

    # Advance the sender's own read cursor so their unread count stays 0
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.channel_id == channel_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is not None and (
        membership.last_read_message_id is None
        or msg.id > membership.last_read_message_id
    ):
        membership.last_read_message_id = msg.id
        await session.flush()

    out = MessageOut(
        id=msg.id,
        channel_id=msg.channel_id,
        sender_id=msg.sender_id,
        sender_name=user.display_name,
        content=msg.content,
        created_at=msg.created_at,
    )

    # Publish to Redis for WebSocket fan-out — fire-and-forget (non-blocking
    # for the HTTP response, but awaited so the event is reliably queued).
    topic = REDIS_CHANNEL_TOPIC.format(channel_id=channel_id)
    payload = json.dumps(
        {
            "type": "message",
            "data": {
                "id": out.id,
                "channel_id": out.channel_id,
                "sender_id": out.sender_id,
                "sender_name": out.sender_name,
                "content": out.content,
                "created_at": out.created_at.isoformat(),
            },
        }
    )
    try:
        await redis.publish(topic, payload)
    except Exception:
        # Redis failure must NOT fail the HTTP response — message is already
        # durably in Postgres; WS delivery is best-effort.
        logger.exception("Redis publish failed for channel_id=%d", channel_id)

    logger.info("Message id=%d posted to channel_id=%d", msg.id, channel_id)
    return out


@router.get("/channels/{channel_id}/messages", response_model=MessageListOut)
async def get_messages(
    channel_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    before_id: Optional[int] = Query(None, description="Cursor: return messages before this id"),
    after_id: Optional[int] = Query(None, description="Cursor: return messages after this id (reconnect replay)"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> MessageListOut:
    """Cursor-based message history for a channel.

    Use before_id for scroll-back (infinite scroll upward).
    Use after_id for reconnect replay (client sends last seen message id).
    Results are always returned in ascending id order.
    has_more=True means there are additional pages in the requested direction.
    """
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    await _assert_member(session, channel_id, user.id)

    # Build the cursor filter
    filters = [Message.channel_id == channel_id]
    if before_id is not None:
        filters.append(Message.id < before_id)
    if after_id is not None:
        filters.append(Message.id > after_id)

    if before_id is not None:
        # Scroll-back: fetch the newest N messages before the cursor, then reverse
        stmt = (
            select(Message, User.display_name)
            .join(User, User.id == Message.sender_id)
            .where(and_(*filters))
            .order_by(Message.id.desc())
            .limit(limit + 1)
        )
    else:
        # After-id or initial load: ascending order
        stmt = (
            select(Message, User.display_name)
            .join(User, User.id == Message.sender_id)
            .where(and_(*filters))
            .order_by(Message.id.asc())
            .limit(limit + 1)
        )

    rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    # Always return messages in ascending order
    if before_id is not None:
        rows = list(reversed(rows))

    messages = [
        MessageOut(
            id=msg.id,
            channel_id=msg.channel_id,
            sender_id=msg.sender_id,
            sender_name=display_name,
            content=msg.content,
            created_at=msg.created_at,
        )
        for msg, display_name in rows
    ]

    return MessageListOut(messages=messages, has_more=has_more)
