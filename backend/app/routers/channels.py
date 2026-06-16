"""Channels router — list, create, join, leave, and mark-read.

A channel is a chat room (Channel table). Membership rows link users to
channels. DMs reuse the Channel table with is_dm=True and are excluded from
the public list here — they are created/listed via the /api/dm router.

The caller is always derived from the JWT (get_current_user); the client
never supplies a user id, so no query can leak across tenants.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Channel, Membership, Message, User
from app.schemas import (
    ActionResponse,
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
    out = ChannelOut.model_validate(channel)
    out.unread_count = 0
    return out


@router.post("/{channel_id}/join", response_model=ChannelOut)
async def join_channel(
    channel_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelOut:
    """Join a public channel. Idempotent. DM channels cannot be joined here."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    if channel.is_dm:
        # DMs are private; joining one by id would be a tenancy leak
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct message channels cannot be joined directly",
        )

    existing = await session.execute(
        select(Membership.id).where(
            Membership.user_id == user.id, Membership.channel_id == channel_id
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(Membership(user_id=user.id, channel_id=channel_id))
        await session.flush()
        logger.info("User id=%d joined channel id=%d", user.id, channel_id)

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
