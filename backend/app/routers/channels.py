"""Channels router — list, create, join, leave, and mark-read.

A channel is a chat room (Channel table). Membership rows link users to
channels. DMs reuse the Channel table with is_dm=True and are excluded from
the public list here — they are created/listed via the /api/dm router.

The caller is always derived from the JWT (get_current_user); the client
never supplies a user id, so no query can leak across tenants.
"""

import json
import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_redis, get_session
from app.models import Channel, Membership, Message, User
from app.schemas import (
    ActionResponse,
    AddMemberRequest,
    ChannelCreate,
    ChannelOut,
    MarkReadRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _unread_count_subquery(membership_alias: type[Membership]):
    """Correlated scalar subquery: messages newer than the caller's read cursor.

    Counts messages in the channel whose id is greater than
    last_read_message_id. A NULL cursor (never read) counts every message.
    """
    return (
        select(func.count(Message.id))
        .where(
            Message.channel_id == Channel.id,
            or_(
                membership_alias.last_read_message_id.is_(None),
                Message.id > membership_alias.last_read_message_id,
            ),
        )
        .correlate(Channel, membership_alias)
        .scalar_subquery()
    )


async def _load_channel_for_user(
    session: AsyncSession, channel_id: int, user_id: int
) -> ChannelOut:
    """Return a single joined channel as ChannelOut, or 404 if not a member."""
    unread = _unread_count_subquery(Membership).label("unread_count")
    stmt = (
        select(Channel, unread)
        .join(Membership, Membership.channel_id == Channel.id)
        .where(Membership.channel_id == channel_id, Membership.user_id == user_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found or you are not a member",
        )
    channel, unread_count = row
    out = ChannelOut.model_validate(channel)
    out.unread_count = unread_count
    return out


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ChannelOut]:
    """List the caller's joined public channels with per-channel unread counts.

    Excludes is_dm=True channels — those surface via /api/dm.
    """
    unread = _unread_count_subquery(Membership).label("unread_count")
    stmt = (
        select(Channel, unread)
        .join(Membership, Membership.channel_id == Channel.id)
        .where(Membership.user_id == user.id, Channel.is_dm.is_(False))
        .order_by(Channel.id)
    )
    rows = (await session.execute(stmt)).all()

    result: list[ChannelOut] = []
    for channel, unread_count in rows:
        out = ChannelOut.model_validate(channel)
        out.unread_count = unread_count
        result.append(out)
    return result


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> ChannelOut:
    """Create a public channel and auto-join the creator.

    Soft-checks for a duplicate public channel name. NOTE: there is no DB
    unique constraint on channel.name, so this is racy under concurrency — a
    partial unique index (WHERE is_dm=false) would be the durable fix.
    """
    existing = await session.execute(
        select(Channel.id).where(
            Channel.name == body.name, Channel.is_dm.is_(False)
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A channel named '{body.name}' already exists",
        )

    channel = Channel(
        name=body.name,
        description=body.description,
        is_dm=False,
        created_by=user.id,
    )
    session.add(channel)
    await session.flush()  # populate channel.id

    session.add(Membership(user_id=user.id, channel_id=channel.id))
    await session.flush()

    logger.info("User id=%d created channel id=%d name=%s", user.id, channel.id, channel.name)

    # Notify the creator's own WebSocket so it re-subscribes to the new
    # channel's Redis topic. The subscriber task captured its topic list at
    # connect time — before this channel existed — so without this signal the
    # creator's socket isn't subscribed to channel:{id} and the echo of their
    # own first message never arrives (they'd see "No messages yet" until a
    # manual refresh). channel_added → forceReconnect → fresh _load_channel_ids().
    # Same pattern as add_member (for the added user) and the DM router.
    try:
        payload = json.dumps({
            "type": "channel_added",
            "data": {
                "id": channel.id,
                "name": channel.name,
                "description": channel.description,
                "is_dm": channel.is_dm,
                "created_by": channel.created_by,
                "created_at": channel.created_at.isoformat(),
                "unread_count": 0,
            },
        })
        await redis.publish(f"user:{user.id}", payload)
    except Exception:
        logger.warning("Failed to publish channel_added to creator user_id=%d", user.id)

    out = ChannelOut.model_validate(channel)
    out.unread_count = 0
    return out


@router.post("/{channel_id}/members", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    channel_id: int,
    body: AddMemberRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> ChannelOut:
    """Add another user to a channel. Caller must already be a member. Idempotent."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    if channel.is_dm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot add members to a DM channel",
        )

    # Caller must be a member (creator always is after POST /api/channels)
    caller = await session.execute(
        select(Membership.id).where(
            Membership.user_id == user.id, Membership.channel_id == channel_id
        )
    )
    if caller.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this channel")

    # Verify target user exists
    target = await session.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = await session.execute(
        select(Membership.id).where(
            Membership.user_id == body.user_id, Membership.channel_id == channel_id
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(Membership(user_id=body.user_id, channel_id=channel_id))
        await session.flush()
        logger.info("User id=%d added user id=%d to channel id=%d", user.id, body.user_id, channel_id)

        # Notify the new member in real-time so their sidebar updates without
        # a page refresh. Publishes to their personal user:{id} Redis topic;
        # the WS subscriber task relays it to their open socket (if any).
        try:
            payload = json.dumps({
                "type": "channel_added",
                "data": {
                    "id": channel.id,
                    "name": channel.name,
                    "description": channel.description,
                    "is_dm": channel.is_dm,
                    "created_by": channel.created_by,
                    "created_at": channel.created_at.isoformat(),
                    "unread_count": 0,
                },
            })
            await redis.publish(f"user:{body.user_id}", payload)
        except Exception:
            logger.warning("Failed to publish channel_added to user_id=%d", body.user_id)

    return await _load_channel_for_user(session, channel_id, user.id)


@router.post("/{channel_id}/leave", response_model=ActionResponse)
async def leave_channel(
    channel_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActionResponse:
    """Leave a channel. 404 if the caller is not a member."""
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id, Membership.channel_id == channel_id
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not a member of this channel",
        )

    await session.delete(membership)
    await session.flush()
    logger.info("User id=%d left channel id=%d", user.id, channel_id)
    return ActionResponse(detail="Left channel")


@router.post("/{channel_id}/read", response_model=ChannelOut)
async def mark_read(
    channel_id: int,
    body: MarkReadRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelOut:
    """Advance the caller's read cursor; resets unread count for the channel.

    If message_id is omitted, marks read up to the channel's latest message.
    """
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id, Membership.channel_id == channel_id
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found or you are not a member",
        )

    if body.message_id is not None:
        target_id = body.message_id
    else:
        max_id = await session.execute(
            select(func.max(Message.id)).where(Message.channel_id == channel_id)
        )
        target_id = max_id.scalar_one_or_none()

    # Never move the cursor backwards (out-of-order reads, stale clients)
    if target_id is not None and (
        membership.last_read_message_id is None
        or target_id > membership.last_read_message_id
    ):
        membership.last_read_message_id = target_id
        await session.flush()

    return await _load_channel_for_user(session, channel_id, user.id)
