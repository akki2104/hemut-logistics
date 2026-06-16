"""Auth router — register and login endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, hash_password, verify_password
from app.db import get_session
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Create a new user account and return a JWT."""
    result = await session.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    await session.flush()  # populate user.id before commit

    token = create_access_token(user.id)
    logger.info("Registered new user id=%d email=%s", user.id, user.email)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Validate credentials and return a JWT."""
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Deliberate vague message — don't reveal which field was wrong
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user.id)
    logger.info("User id=%d logged in", user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))
