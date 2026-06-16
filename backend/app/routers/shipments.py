"""Shipments router — inline card lookup by shipment reference.

GET /api/shipments/{shipment_ref}
    Returns a shipment row from Postgres by its human-readable ref (e.g. SHIP-001).

    How this fits the product:
    The frontend detects SHIP-\\d+ patterns inside chat message text and silently
    calls this endpoint to hydrate an inline shipment card — origin, destination,
    carrier, status, ETA — without any extra action from the user. This is the
    same pattern Slack uses for link unfurling.

    404 is intentional: if the ref is unknown the client degrades gracefully to
    plain text. This also handles typos like SHIP-99 without crashing.

    Production extension point:
    Add a Redis cache layer here (cache-aside, 5 min TTL) before hitting Postgres,
    and an optional background refresh from a live carrier API. The endpoint shape
    stays the same; the frontend never needs to know.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Shipment, User
from app.schemas import ShipmentOut

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{shipment_ref}", response_model=ShipmentOut)
async def get_shipment(
    shipment_ref: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShipmentOut:
    """Look up a shipment by its reference code.

    Any authenticated user can look up any shipment — no per-shipment ACL needed
    for a single-tenant logistics platform. 404 for unknown refs so the frontend
    can degrade to plain text instead of rendering a broken card.
    """
    result = await session.execute(
        select(Shipment).where(Shipment.shipment_ref == shipment_ref.upper())
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shipment {shipment_ref!r} not found",
        )

    logger.info("Shipment %s fetched by user_id=%d", shipment.shipment_ref, user.id)
    return ShipmentOut.model_validate(shipment)
