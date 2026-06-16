"""Seed script — populate channels, users, memberships, and shipments.

Run with: python -m app.seed
Idempotent: skips rows that already exist (checked by natural key).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.db import async_session_factory
from app.models import Channel, Membership, Shipment, User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHANNELS = [
    {"name": "general", "description": "Company-wide announcements and updates"},
    {"name": "route-east", "description": "Eastern corridor route coordination"},
    {"name": "warehouse-mumbai", "description": "Mumbai warehouse operations"},
    {"name": "dispatch-ops", "description": "Dispatch team daily operations"},
    {"name": "delays", "description": "Delay alerts and escalations"},
]

USERS = [
    {"email": "dispatcher@hemut.com", "display_name": "Dispatcher", "password": "password123"},
    {"email": "driver@hemut.com", "display_name": "Driver", "password": "password123"},
]

_now = datetime.now(timezone.utc)

SHIPMENTS = [
    {
        "shipment_ref": "SHIP-001",
        "origin": "Bangalore",
        "destination": "Mumbai",
        "carrier": "FedEx",
        "status": "IN_TRANSIT",
        "eta": _now + timedelta(days=1),
    },
    {
        "shipment_ref": "SHIP-002",
        "origin": "Delhi",
        "destination": "Chennai",
        "carrier": "DHL",
        "status": "DELIVERED",
        "eta": _now - timedelta(days=1),
    },
    {
        "shipment_ref": "SHIP-003",
        "origin": "Hyderabad",
        "destination": "Pune",
        "carrier": "Ecom Express",
        "status": "IN_TRANSIT",
        "eta": _now + timedelta(days=3),
    },
    {
        "shipment_ref": "SHIP-004",
        "origin": "Kolkata",
        "destination": "Ahmedabad",
        "carrier": "Blue Dart",
        "status": "DELAYED",
        "eta": _now + timedelta(hours=6),
    },
    {
        "shipment_ref": "SHIP-005",
        "origin": "Chennai",
        "destination": "Delhi",
        "carrier": "DTDC",
        "status": "IN_TRANSIT",
        "eta": _now + timedelta(days=2),
    },
    {
        "shipment_ref": "SHIP-006",
        "origin": "Mumbai",
        "destination": "Kolkata",
        "carrier": "FedEx",
        "status": "DELIVERED",
        "eta": _now - timedelta(days=2),
    },
    {
        "shipment_ref": "SHIP-007",
        "origin": "Pune",
        "destination": "Hyderabad",
        "carrier": "DHL",
        "status": "DELAYED",
        "eta": _now + timedelta(hours=12),
    },
    {
        "shipment_ref": "SHIP-008",
        "origin": "Ahmedabad",
        "destination": "Bangalore",
        "carrier": "Blue Dart",
        "status": "IN_TRANSIT",
        "eta": _now + timedelta(days=4),
    },
    {
        "shipment_ref": "SHIP-009",
        "origin": "Jaipur",
        "destination": "Mumbai",
        "carrier": "Ecom Express",
        "status": "IN_TRANSIT",
        "eta": _now + timedelta(days=1, hours=6),
    },
    {
        "shipment_ref": "SHIP-010",
        "origin": "Nagpur",
        "destination": "Delhi",
        "carrier": "DTDC",
        "status": "DELAYED",
        "eta": _now + timedelta(hours=3),
    },
]


async def seed_channels(session: AsyncSession) -> list[Channel]:
    """Insert channels that don't exist yet; return all 5."""
    channels = []
    for ch in CHANNELS:
        result = await session.execute(
            select(Channel).where(Channel.name == ch["name"], Channel.is_dm == False)  # noqa: E712
        )
        existing = result.scalar_one_or_none()
        if existing:
            channels.append(existing)
            logger.info("Channel #%s already exists, skipping", ch["name"])
        else:
            channel = Channel(name=ch["name"], description=ch["description"], is_dm=False)
            session.add(channel)
            await session.flush()
            channels.append(channel)
            logger.info("Created channel #%s (id=%d)", ch["name"], channel.id)
    return channels


async def seed_users(session: AsyncSession) -> list[User]:
    """Insert users that don't exist yet; return both."""
    users = []
    for u in USERS:
        result = await session.execute(select(User).where(User.email == u["email"]))
        existing = result.scalar_one_or_none()
        if existing:
            users.append(existing)
            logger.info("User %s already exists, skipping", u["email"])
        else:
            user = User(
                email=u["email"],
                display_name=u["display_name"],
                password_hash=hash_password(u["password"]),
            )
            session.add(user)
            await session.flush()
            users.append(user)
            logger.info("Created user %s (id=%d)", u["email"], user.id)
    return users


async def seed_memberships(
    session: AsyncSession, users: list[User], channels: list[Channel]
) -> None:
    """Join both users to all 5 channels if not already members."""
    for user in users:
        for channel in channels:
            result = await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.channel_id == channel.id,
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(Membership(user_id=user.id, channel_id=channel.id))
                logger.info(
                    "Joined user %s to #%s", user.email, channel.name
                )


async def seed_shipments(session: AsyncSession) -> None:
    """Insert shipments that don't exist yet."""
    for s in SHIPMENTS:
        result = await session.execute(
            select(Shipment).where(Shipment.shipment_ref == s["shipment_ref"])
        )
        if result.scalar_one_or_none() is None:
            session.add(Shipment(**s))
            logger.info("Created shipment %s", s["shipment_ref"])
        else:
            logger.info("Shipment %s already exists, skipping", s["shipment_ref"])


async def main() -> None:
    """Run all seed steps in a single transaction."""
    async with async_session_factory() as session:
        channels = await seed_channels(session)
        users = await seed_users(session)
        await seed_memberships(session, users, channels)
        await seed_shipments(session)
        await session.commit()
    logger.info("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
