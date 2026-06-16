"""Integration tests for the shipments router.

No Redis mocking needed — shipments are pure Postgres reads.
Each test inserts rows via the transactional rollback session from conftest,
so the DB is clean after every test.
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Shipment

SHIPMENTS_URL = "/api/shipments"

# Naive UTC — Shipment.eta is TIMESTAMP WITHOUT TIME ZONE; asyncpg rejects tz-aware here.
_eta = datetime.utcnow() + timedelta(days=2)


def _make_shipment(**kwargs) -> Shipment:
    defaults = {
        "shipment_ref": "SHIP-TEST",
        "origin": "Bangalore",
        "destination": "Mumbai",
        "carrier": "FedEx",
        "status": "IN_TRANSIT",
        "eta": _eta,
    }
    defaults.update(kwargs)
    return Shipment(**defaults)


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_get_shipment_requires_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(_make_shipment())
    await db_session.flush()

    resp = await client.get(f"{SHIPMENTS_URL}/SHIP-TEST")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_get_shipment_success(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    headers, _ = await register_user()
    db_session.add(_make_shipment(shipment_ref="SHIP-001"))
    await db_session.flush()

    resp = await client.get(f"{SHIPMENTS_URL}/SHIP-001", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipment_ref"] == "SHIP-001"
    assert body["origin"] == "Bangalore"
    assert body["destination"] == "Mumbai"
    assert body["carrier"] == "FedEx"
    assert body["status"] == "IN_TRANSIT"
    assert "eta" in body
    assert "id" in body
    assert "created_at" in body


async def test_get_shipment_case_insensitive_ref(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    """The endpoint normalises the ref to uppercase before querying."""
    headers, _ = await register_user()
    db_session.add(_make_shipment(shipment_ref="SHIP-042"))
    await db_session.flush()

    # Send lowercase — should still find it
    resp = await client.get(f"{SHIPMENTS_URL}/ship-042", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["shipment_ref"] == "SHIP-042"


async def test_get_shipment_delayed_status(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    headers, _ = await register_user()
    db_session.add(_make_shipment(shipment_ref="SHIP-099", status="DELAYED"))
    await db_session.flush()

    resp = await client.get(f"{SHIPMENTS_URL}/SHIP-099", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELAYED"


async def test_get_shipment_no_eta(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    """eta is nullable; response must handle None without error."""
    headers, _ = await register_user()
    db_session.add(_make_shipment(shipment_ref="SHIP-088", eta=None))
    await db_session.flush()

    resp = await client.get(f"{SHIPMENTS_URL}/SHIP-088", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["eta"] is None


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------


async def test_get_shipment_unknown_ref_404(
    client: AsyncClient, register_user
) -> None:
    headers, _ = await register_user()
    resp = await client.get(f"{SHIPMENTS_URL}/SHIP-99999", headers=headers)
    assert resp.status_code == 404


async def test_get_shipment_typo_ref_404(
    client: AsyncClient, register_user
) -> None:
    """Typos return 404 cleanly — frontend degrades to plain text."""
    headers, _ = await register_user()
    resp = await client.get(f"{SHIPMENTS_URL}/INVALID-REF", headers=headers)
    assert resp.status_code == 404
