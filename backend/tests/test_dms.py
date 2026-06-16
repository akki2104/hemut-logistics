"""Integration tests for the DMs router.

DMs are virtual channels (is_dm=True, name=dm_{min}_{max}). All tests use
the transactional rollback session so the DB is clean after every test.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Membership, Message

DM_URL = "/api/dm"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_open_dm_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(f"{DM_URL}/999")
    assert resp.status_code == 401


async def test_list_dms_requires_auth(client: AsyncClient) -> None:
    resp = await client.get(DM_URL)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/dm/{peer_user_id} — find-or-create
# ---------------------------------------------------------------------------


async def test_open_dm_creates_channel(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    """Opening a DM creates a channel with is_dm=True and returns channel_id + peer."""
    headers_a, user_a = await register_user(email="alice@hemut.com", display_name="Alice")
    headers_b, user_b = await register_user(email="bob@hemut.com", display_name="Bob")

    resp = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert "channel_id" in body
    assert body["peer"]["id"] == user_b["id"]
    assert body["peer"]["display_name"] == "Bob"

    # Channel row should exist in DB and be a DM
    channel = await db_session.get(Channel, body["channel_id"])
    assert channel is not None
    assert channel.is_dm is True
    expected_name = f"dm_{min(user_a['id'], user_b['id'])}_{max(user_a['id'], user_b['id'])}"
    assert channel.name == expected_name


async def test_open_dm_creates_both_memberships(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    """Both users become members of the DM channel atomically."""
    from sqlalchemy import select

    headers_a, user_a = await register_user(email="alice2@hemut.com", display_name="Alice2")
    headers_b, user_b = await register_user(email="bob2@hemut.com", display_name="Bob2")

    resp = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    channel_id = resp.json()["channel_id"]

    for uid in (user_a["id"], user_b["id"]):
        row = (
            await db_session.execute(
                select(Membership).where(
                    Membership.user_id == uid, Membership.channel_id == channel_id
                )
            )
        ).scalar_one_or_none()
        assert row is not None, f"Missing membership for user_id={uid}"


async def test_open_dm_idempotent(
    client: AsyncClient, register_user
) -> None:
    """Calling POST /api/dm/{peer} twice returns the same channel_id."""
    headers_a, user_a = await register_user(email="alice3@hemut.com", display_name="Alice3")
    _, user_b = await register_user(email="bob3@hemut.com", display_name="Bob3")

    resp1 = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    resp2 = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["channel_id"] == resp2.json()["channel_id"]


async def test_open_dm_channel_name_ordering(
    client: AsyncClient, register_user
) -> None:
    """Lower user id is always first regardless of who initiates the DM."""
    headers_a, user_a = await register_user(email="alice4@hemut.com", display_name="Alice4")
    headers_b, user_b = await register_user(email="bob4@hemut.com", display_name="Bob4")

    # User A opens DM with B
    resp_ab = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    # User B opens DM with A (same channel, reversed initiator)
    resp_ba = await client.post(f"{DM_URL}/{user_a['id']}", headers=headers_b)

    assert resp_ab.json()["channel_id"] == resp_ba.json()["channel_id"]


async def test_open_dm_self_400(
    client: AsyncClient, register_user
) -> None:
    headers, user = await register_user()
    resp = await client.post(f"{DM_URL}/{user['id']}", headers=headers)
    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"].lower()


async def test_open_dm_unknown_peer_404(
    client: AsyncClient, register_user
) -> None:
    headers, _ = await register_user()
    resp = await client.post(f"{DM_URL}/99999999", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/dm — list conversations
# ---------------------------------------------------------------------------


async def test_list_dms_empty(client: AsyncClient, register_user) -> None:
    """New user with no DMs gets an empty list."""
    headers, _ = await register_user()
    resp = await client.get(DM_URL, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_dms_shows_conversation(
    client: AsyncClient, register_user
) -> None:
    """After opening a DM it appears in both users' lists with correct peer info."""
    headers_a, user_a = await register_user(email="alice5@hemut.com", display_name="Alice5")
    headers_b, user_b = await register_user(email="bob5@hemut.com", display_name="Bob5")

    resp = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    channel_id = resp.json()["channel_id"]

    # Alice sees Bob
    list_a = await client.get(DM_URL, headers=headers_a)
    assert list_a.status_code == 200
    convos_a = list_a.json()
    assert len(convos_a) == 1
    assert convos_a[0]["channel_id"] == channel_id
    assert convos_a[0]["peer_id"] == user_b["id"]
    assert convos_a[0]["peer_display_name"] == "Bob5"

    # Bob sees Alice
    list_b = await client.get(DM_URL, headers=headers_b)
    assert list_b.status_code == 200
    convos_b = list_b.json()
    assert len(convos_b) == 1
    assert convos_b[0]["peer_id"] == user_a["id"]
    assert convos_b[0]["peer_display_name"] == "Alice5"


async def test_list_dms_unread_count(
    client: AsyncClient, db_session: AsyncSession, register_user
) -> None:
    """Unread count increases when new messages land after the read cursor."""
    headers_a, user_a = await register_user(email="alice6@hemut.com", display_name="Alice6")
    _, user_b = await register_user(email="bob6@hemut.com", display_name="Bob6")

    resp = await client.post(f"{DM_URL}/{user_b['id']}", headers=headers_a)
    channel_id = resp.json()["channel_id"]

    # Insert 3 messages directly (skipping the full messages router to keep test simple)
    for i in range(3):
        db_session.add(
            Message(channel_id=channel_id, sender_id=user_b["id"], content=f"Hey {i}")
        )
    await db_session.flush()

    list_resp = await client.get(DM_URL, headers=headers_a)
    convo = list_resp.json()[0]
    assert convo["unread_count"] == 3


async def test_list_dms_excludes_public_channels(
    client: AsyncClient, register_user
) -> None:
    """Public channels must not appear in the DM list."""
    from sqlalchemy import select

    headers, _ = await register_user(email="alice7@hemut.com", display_name="Alice7")

    # GET /api/dm must be empty (the seed channels are public, not DMs)
    resp = await client.get(DM_URL, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []
