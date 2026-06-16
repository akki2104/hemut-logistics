"""Users router — directory listing for the DM picker.

GET /api/users
    Returns every user except the caller, so the frontend can offer a
    "start a direct message with…" picker. DMs are keyed by user id
    (POST /api/dm/{peer_user_id}), so the client needs a way to discover the
    ids of people it can message.

    Single-tenant platform: any authenticated user may see the team roster.
    Only non-sensitive fields are exposed (id, email, display_name) — never
    the password hash.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import User
from app.schemas import DirectoryUserOut

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[DirectoryUserOut])
async def list_users(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DirectoryUserOut]:
    """List all users except the caller, ordered by display name."""
    result = await session.execute(
        select(User).where(User.id != user.id).order_by(User.display_name)
    )
    users = result.scalars().all()
    return [DirectoryUserOut.model_validate(u) for u in users]
