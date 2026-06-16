"""Pydantic request/response models shared across routers."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        """Enforce minimum password length."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def display_name_not_blank(cls, v: str) -> str:
        """Ensure display name is not blank."""
        if not v.strip():
            raise ValueError("Display name cannot be blank")
        return v.strip()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ChannelCreate(BaseModel):
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        """Channel name must contain non-whitespace and fit the column."""
        v = v.strip()
        if not v:
            raise ValueError("Channel name cannot be blank")
        if len(v) > 100:
            raise ValueError("Channel name must be 100 characters or fewer")
        return v


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    is_dm: bool
    created_by: Optional[int]
    created_at: datetime
    # Count of messages newer than the caller's last_read_message_id
    unread_count: int = 0


class MarkReadRequest(BaseModel):
    # If omitted, the server marks the channel read up to its latest message
    message_id: Optional[int] = None


class ActionResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageCreate(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message content cannot be blank")
        return v


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    sender_id: int
    sender_name: str  # denormalized from users table at query time
    content: str
    created_at: datetime


class MessageListOut(BaseModel):
    messages: list[MessageOut]
    has_more: bool  # true when more pages exist in the requested direction


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AI summarization
# ---------------------------------------------------------------------------


class SummarizeResponse(BaseModel):
    """Response from POST /api/channels/{id}/summarize.

    `summary` is non-null only when the result is available synchronously —
    a cache hit, or an empty channel. Otherwise it is null and the summary
    streams over the requester's WebSocket as `ai_summary` events keyed by
    `request_id`.
    """

    request_id: str
    cached: bool
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------------


class PeerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str


class DMOpenOut(BaseModel):
    """Returned by POST /api/dm/{peer_user_id} — the resolved channel id + peer."""

    channel_id: int
    peer: PeerOut


class DMConversationOut(BaseModel):
    """One entry in the caller's DM conversation list."""

    channel_id: int
    peer_id: int
    peer_display_name: str
    unread_count: int = 0


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_ref: str
    origin: str
    destination: str
    carrier: str
    status: str  # IN_TRANSIT | DELIVERED | DELAYED
    eta: Optional[datetime]
    created_at: datetime
