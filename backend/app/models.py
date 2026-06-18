"""SQLAlchemy 2.0 ORM models — the durable Postgres schema.

Presence is NOT stored here; it lives in Redis (see docs/ARCHITECTURE.md).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    String,
    TIMESTAMP,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # DMs reuse this table — is_dm=True channels are excluded from the public list
    is_dm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uq_membership_user_channel"),
        Index("idx_memberships_user_id", "user_id"),
        Index("idx_memberships_channel_id", "channel_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id"), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    # Tracks unread boundary; NULL means the user has never read the channel
    last_read_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Composite index powers cursor pagination (WHERE channel_id=? AND id < ?)
        Index("idx_messages_channel_id", "channel_id", "id"),
        Index("idx_messages_channel_created_at", "channel_id", "created_at"),
        # Index for fast thread fetch (WHERE parent_id = ?)
        Index("idx_messages_parent_id", "parent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id"), nullable=False
    )
    # sender_id is ALWAYS derived from the JWT on the server — never trust client body
    sender_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    # NULL = root message; set to parent message id for thread replies
    parent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("messages.id"), nullable=True
    )


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # e.g. SHIP-001 — unique human-readable ref used in chat card lookups
    shipment_ref: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(100), nullable=False)
    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    carrier: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # IN_TRANSIT | DELIVERED | DELAYED
    eta: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
