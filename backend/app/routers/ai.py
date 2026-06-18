"""AI router — "Catch me up" channel summarization.

POST /api/channels/{channel_id}/summarize
    1. Validate the channel exists and the caller is a member.
    2. If a summary is cached (warm within 5 min) → return it in the body
       immediately (no LLM call, no streaming).
    3. If the channel has no messages → return a short canned line in the body.
    4. Otherwise → generate a correlation id, kick off a background streaming
       task, and return {request_id, cached:false, summary:null}. The summary
       arrives over the caller's WebSocket as `ai_summary` events.

The endpoint never streams chunks itself and never touches the channel's
Redis topic — see app/services/ai.py for the rationale.
"""

import logging
import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_redis, get_session
from app.models import Channel, Membership, Message, User
from app.schemas import AskRequest, AskResponse, SummarizeResponse
from app.services import ai as ai_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _assert_member(session: AsyncSession, channel_id: int, user_id: int) -> None:
    """Raise 403 if the caller is not a member of the channel (no tenancy leak)."""
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


@router.post("/channels/{channel_id}/summarize", response_model=SummarizeResponse)
async def summarize_channel(
    channel_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> SummarizeResponse:
    """Trigger (or return a cached) channel summary."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    await _assert_member(session, channel_id, user.id)

    request_id = uuid.uuid4().hex

    # 1) Cache hit → instant, no LLM call.
    cached = await ai_service.get_cached_summary(redis, channel_id)
    if cached is not None:
        logger.info("AI summary cache HIT for channel_id=%d", channel_id)
        return SummarizeResponse(request_id=request_id, cached=True, summary=cached)

    # 2) Empty channel → nothing to summarize; answer synchronously.
    has_message = await session.scalar(
        select(Message.id).where(Message.channel_id == channel_id).limit(1)
    )
    if has_message is None:
        return SummarizeResponse(
            request_id=request_id,
            cached=False,
            summary="No recent messages to summarize.",
        )

    # 3) Cache miss → check per-user rate limit before hitting the LLM.
    allowed = await ai_service.check_rate_limit(redis, user.id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Summary rate limit reached ({ai_service.RATE_LIMIT_MAX} LLM summaries "
                f"per {ai_service.RATE_LIMIT_WINDOW // 60} minutes). "
                "Try again shortly, or use a cached result."
            ),
        )

    # 4) Within budget → stream over WS. Return the correlation id now.
    logger.info(
        "AI summary cache MISS for channel_id=%d — streaming to user_id=%d (request_id=%s)",
        channel_id,
        user.id,
        request_id,
    )
    ai_service.schedule_summary(user.id, channel_id, request_id)
    return SummarizeResponse(request_id=request_id, cached=False, summary=None)


@router.post("/channels/{channel_id}/ask", response_model=AskResponse)
async def ask_channel(
    channel_id: int,
    payload: AskRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> AskResponse:
    """Ask Hemut a question about this channel and the shipment board.

    Unlike summarize there is no cache (answers are question-specific) and no
    empty-channel short-circuit (the model can still answer from the shipments
    table even if the channel has no messages). Every call is billable, so the
    per-user Ask rate limit is checked before scheduling.
    """
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    await _assert_member(session, channel_id, user.id)

    allowed = await ai_service.check_rate_limit(
        redis,
        user.id,
        key_template=ai_service.ASK_RATE_KEY,
        max_calls=ai_service.RATE_LIMIT_MAX,
        window=ai_service.RATE_LIMIT_WINDOW,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Ask rate limit reached ({ai_service.RATE_LIMIT_MAX} questions "
                f"per {ai_service.RATE_LIMIT_WINDOW // 60} minutes). "
                "Try again shortly."
            ),
        )

    request_id = uuid.uuid4().hex
    logger.info(
        "Ask scheduled for channel_id=%d user_id=%d (request_id=%s)",
        channel_id,
        user.id,
        request_id,
    )
    ai_service.schedule_answer(user.id, channel_id, request_id, payload.question)
    return AskResponse(request_id=request_id)
