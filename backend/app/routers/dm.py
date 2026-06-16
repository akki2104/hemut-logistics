"""Direct messages router.

DMs are virtual channels: Channel.name = dm_{min(A,B)}_{max(A,B)}, is_dm=True.
Zero new message-path code — once the dm channel id is known the frontend
reuses the standard channel message endpoints.

POST /api/dm/{peer_user_id}  — find-or-create the DM channel + both memberships
GET  /api/dm                 — list caller's DM conversations with peer info

Why the lower id goes first in the name:
Both users independently call POST /api/dm/{other}. The deterministic name
means they always reference the same channel, so the find-or-create is
idempotent regardless of who initiates first.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Channel, Membership, Message, User
from app.schemas import DMConversationOut, DMOpenOut, PeerOut

logger = logging.getLogger(__name__)

router = APIRouter()


def _dm_name(a: int, b: int) -> str:
    lo, hi = min(a, b), max(a, b)
    return f"dm_{lo}_{hi}"


async def _ensure_membership(session: AsyncSession, user_id: int, channel_id: int) -> None:
    """Add a membership row only if it doesn't already exist (idempotent)."""
    existing = await session.execute(
        select(Membership.id).where(
            Membership.user_id == user_id, Membership.channel_id == channel_id
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(Membership(user_id=user_id, channel_id=channel_id))


@router.post("/{peer_user_id}", response_model=DMOpenOut, status_code=status.HTTP_200_OK)
async def open_dm(
    peer_user_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DMOpenOut:
    """Find or create a DM channel between caller and peer.

    Idempotent: calling twice returns the same channel id. Both memberships
    are created (or confirmed) in the same transaction — no partial state.
    """
    if peer_user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot open a DM with yourself",
        )

    peer = await session.get(User, peer_user_id)
    if peer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    name = _dm_name(user.id, peer_user_id)

    result = await session.execute(
        select(Channel).where(Channel.name == name, Channel.is_dm.is_(True))
    )
    channel = result.scalar_one_or_none()

    if channel is None:
        channel = Channel(name=name, is_dm=True, created_by=user.id)
        session.add(channel)
        await session.flush()  # populate channel.id
        logger.info(
            "Created DM channel id=%d between user_id=%d and user_id=%d",
            channel.id,
            user.id,
            peer_user_id,
        )
    else:
        logger.info(
            "DM channel id=%d already exists for user_id=%d and user_id=%d",
            channel.id,
            user.id,
            peer_user_id,
        )

    await _ensure_membership(session, user.id, channel.id)
    await _ensure_membership(session, peer_user_id, channel.id)
    await session.flush()

    return DMOpenOut(channel_id=channel.id, peer=PeerOut.model_validate(peer))


@router.get("", response_model=list[DMConversationOut])
async def list_dms(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DMConversationOut]:
    """List the caller's DM conversations with peer info and unread counts.

    Each DM channel name encodes both user ids; the peer is whichever id
    is not the caller's.
    """
    stmt = (
        select(Channel, Membership)
        .join(Membership, Membership.channel_id == Channel.id)
        .where(Membership.user_id == user.id, Channel.is_dm.is_(True))
        .order_by(Channel.id)
    )
    rows = (await session.execute(stmt)).all()

    result: list[DMConversationOut] = []
    for channel, membership in rows:
        parts = channel.name.split("_")
        if len(parts) != 3:
            continue
        try:
            id_a, id_b = int(parts[1]), int(parts[2])
        except ValueError:
            continue

        peer_id = id_b if id_a == user.id else id_a
        peer = await session.get(User, peer_id)
        if peer is None:
            continue

        if membership.last_read_message_id is None:
            unread_stmt = select(func.count(Message.id)).where(
                Message.channel_id == channel.id
            )
        else:
            unread_stmt = select(func.count(Message.id)).where(
                Message.channel_id == channel.id,
                Message.id > membership.last_read_message_id,
            )
        unread_count = (await session.execute(unread_stmt)).scalar_one()

        result.append(
            DMConversationOut(
                channel_id=channel.id,
                peer_id=peer.id,
                peer_display_name=peer.display_name,
                unread_count=unread_count,
            )
        )

    return result
