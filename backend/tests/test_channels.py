"""Integration tests for the channels router.

Covers create/list/join/leave/read, unread-count computation, is_dm
exclusion, membership isolation, and auth enforcement. Each test runs inside
a rolled-back transaction (see conftest).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Membership, Message

CHANNELS_URL = "/api/channels"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_list_requires_auth(client: AsyncClient) -> None:
    resp = await client.get(CHANNELS_URL)
    assert resp.status_code == 401


async def test_create_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(CHANNELS_URL, json={"name": "nope"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_channel_auto_joins_creator(register_user, client: AsyncClient) -> None:
    headers, user = await register_user()

    resp = await client.post(
        CHANNELS_URL,
        json={"name": "dispatch-ops", "description": "ops room"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "dispatch-ops"
    assert body["is_dm"] is False
    assert body["created_by"] == user["id"]
    assert body["unread_count"] == 0

    # Creator is a member → it shows up in their list
    listed = await client.get(CHANNELS_URL, headers=headers)
    names = [c["name"] for c in listed.json()]
    assert "dispatch-ops" in names


async def test_create_blank_name_rejected(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    resp = await client.post(CHANNELS_URL, json={"name": "   "}, headers=headers)
    assert resp.status_code == 422


async def test_create_duplicate_name_rejected(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    await client.post(CHANNELS_URL, json={"name": "general"}, headers=headers)
    resp = await client.post(CHANNELS_URL, json={"name": "general"}, headers=headers)
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# List — isolation + is_dm exclusion
# ---------------------------------------------------------------------------


async def test_list_only_shows_joined_channels(register_user, client: AsyncClient) -> None:
    alice_h, _ = await register_user(email="alice@hemut.com", display_name="Alice")
    bob_h, _ = await register_user(email="bob@hemut.com", display_name="Bob")

    # Alice creates a channel; Bob is not a member
    await client.post(CHANNELS_URL, json={"name": "alice-only"}, headers=alice_h)

    bob_list = await client.get(CHANNELS_URL, headers=bob_h)
    assert bob_list.status_code == 200
    assert bob_list.json() == []


async def test_list_excludes_dm_channels(
    register_user, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user = await register_user()

    # A normal channel via the API
    await client.post(CHANNELS_URL, json={"name": "public-room"}, headers=headers)

    # A DM channel inserted directly (no DM endpoint yet) + membership
    dm = Channel(name="dm_1_2", is_dm=True, created_by=user["id"])
    db_session.add(dm)
    await db_session.flush()
    db_session.add(Membership(user_id=user["id"], channel_id=dm.id))
    await db_session.flush()

    resp = await client.get(CHANNELS_URL, headers=headers)
    names = [c["name"] for c in resp.json()]
    assert "public-room" in names
    assert "dm_1_2" not in names  # is_dm excluded from public list


# ---------------------------------------------------------------------------
# Join / leave
# ---------------------------------------------------------------------------


async def test_join_channel(register_user, client: AsyncClient) -> None:
    creator_h, _ = await register_user(email="creator@hemut.com")
    joiner_h, _ = await register_user(email="joiner@hemut.com")

    created = await client.post(CHANNELS_URL, json={"name": "warehouse"}, headers=creator_h)
    cid = created.json()["id"]

    resp = await client.post(f"{CHANNELS_URL}/{cid}/join", headers=joiner_h)
    assert resp.status_code == 200
    assert resp.json()["id"] == cid

    joiner_list = await client.get(CHANNELS_URL, headers=joiner_h)
    assert cid in [c["id"] for c in joiner_list.json()]


async def test_join_is_idempotent(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    created = await client.post(CHANNELS_URL, json={"name": "room"}, headers=headers)
    cid = created.json()["id"]

    # Creator already a member; joining again must not error or duplicate
    r1 = await client.post(f"{CHANNELS_URL}/{cid}/join", headers=headers)
    assert r1.status_code == 200

    listed = await client.get(CHANNELS_URL, headers=headers)
    assert [c["id"] for c in listed.json()].count(cid) == 1


async def test_join_missing_channel_404(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    resp = await client.post(f"{CHANNELS_URL}/999999/join", headers=headers)
    assert resp.status_code == 404


async def test_join_dm_channel_forbidden(
    register_user, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user = await register_user()
    dm = Channel(name="dm_5_9", is_dm=True, created_by=user["id"])
    db_session.add(dm)
    await db_session.flush()

    resp = await client.post(f"{CHANNELS_URL}/{dm.id}/join", headers=headers)
    assert resp.status_code == 403


async def test_leave_channel(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    created = await client.post(CHANNELS_URL, json={"name": "leaving"}, headers=headers)
    cid = created.json()["id"]

    resp = await client.post(f"{CHANNELS_URL}/{cid}/leave", headers=headers)
    assert resp.status_code == 200

    listed = await client.get(CHANNELS_URL, headers=headers)
    assert cid not in [c["id"] for c in listed.json()]


async def test_leave_when_not_member_404(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    resp = await client.post(f"{CHANNELS_URL}/123456/leave", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Unread count + mark read
# ---------------------------------------------------------------------------


async def test_unread_count_and_mark_read(
    register_user, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user = await register_user()
    created = await client.post(CHANNELS_URL, json={"name": "busy"}, headers=headers)
    cid = created.json()["id"]

    # Insert 3 messages directly (messages endpoint not built yet)
    for i in range(3):
        db_session.add(Message(channel_id=cid, sender_id=user["id"], content=f"m{i}"))
    await db_session.flush()

    # last_read is NULL → all 3 count as unread
    listed = await client.get(CHANNELS_URL, headers=headers)
    busy = next(c for c in listed.json() if c["id"] == cid)
    assert busy["unread_count"] == 3

    # Mark read with no message_id → catches up to latest
    read = await client.post(f"{CHANNELS_URL}/{cid}/read", json={}, headers=headers)
    assert read.status_code == 200
    assert read.json()["unread_count"] == 0

    # Confirmed via list too
    listed2 = await client.get(CHANNELS_URL, headers=headers)
    busy2 = next(c for c in listed2.json() if c["id"] == cid)
    assert busy2["unread_count"] == 0


async def test_mark_read_specific_id(
    register_user, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user = await register_user()
    created = await client.post(CHANNELS_URL, json={"name": "partial"}, headers=headers)
    cid = created.json()["id"]

    msgs = [Message(channel_id=cid, sender_id=user["id"], content=f"m{i}") for i in range(5)]
    db_session.add_all(msgs)
    await db_session.flush()
    third_id = sorted(m.id for m in msgs)[2]

    # Mark read up to the 3rd message → 2 newer remain unread
    read = await client.post(
        f"{CHANNELS_URL}/{cid}/read", json={"message_id": third_id}, headers=headers
    )
    assert read.json()["unread_count"] == 2


async def test_mark_read_does_not_go_backwards(
    register_user, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user = await register_user()
    created = await client.post(CHANNELS_URL, json={"name": "monotonic"}, headers=headers)
    cid = created.json()["id"]

    msgs = [Message(channel_id=cid, sender_id=user["id"], content=f"m{i}") for i in range(4)]
    db_session.add_all(msgs)
    await db_session.flush()
    ids = sorted(m.id for m in msgs)

    # Read up to latest, then attempt to "read" an older id
    await client.post(f"{CHANNELS_URL}/{cid}/read", json={"message_id": ids[3]}, headers=headers)
    resp = await client.post(
        f"{CHANNELS_URL}/{cid}/read", json={"message_id": ids[1]}, headers=headers
    )
    # Cursor must not regress → still 0 unread
    assert resp.json()["unread_count"] == 0


async def test_read_when_not_member_404(register_user, client: AsyncClient) -> None:
    headers, _ = await register_user()
    resp = await client.post(f"{CHANNELS_URL}/424242/read", json={}, headers=headers)
    assert resp.status_code == 404
