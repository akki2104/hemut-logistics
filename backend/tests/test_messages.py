"""Integration tests for the messages router.

Redis publish is mocked (pytest-mock) so tests are self-contained and don't
depend on pub/sub side effects. DB writes use the transactional rollback
fixture from conftest.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_redis
from app.main import app

CHANNELS_URL = "/api/channels"


@pytest.fixture(autouse=True)
def mock_redis(mocker):
    """Replace the Redis dependency with a mock for every test in this module."""
    mock = AsyncMock()
    mock.publish = AsyncMock(return_value=1)
    mock.aclose = AsyncMock()

    async def _override():
        yield mock

    app.dependency_overrides[get_redis] = _override
    yield mock
    # conftest client fixture clears overrides; remove just the redis one here
    app.dependency_overrides.pop(get_redis, None)


def _msgs_url(channel_id: int) -> str:
    return f"{CHANNELS_URL}/{channel_id}/messages"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_post_message_requires_auth(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "room"}, headers=headers)
    cid = ch.json()["id"]

    resp = await client.post(_msgs_url(cid), json={"content": "hello"})
    assert resp.status_code == 401


async def test_get_messages_requires_auth(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "room"}, headers=headers)
    cid = ch.json()["id"]

    resp = await client.get(_msgs_url(cid))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST — basic posting
# ---------------------------------------------------------------------------


async def test_post_message_success(
    client: AsyncClient, register_user, mock_redis
) -> None:
    headers, user = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "chat"}, headers=headers)
    cid = ch.json()["id"]
    # create_channel publishes channel_added to the creator; ignore that here
    # so the assertions below isolate the message publish.
    mock_redis.publish.reset_mock()

    resp = await client.post(_msgs_url(cid), json={"content": "hello world"}, headers=headers)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content"] == "hello world"
    assert body["channel_id"] == cid
    assert body["sender_id"] == user["id"]
    assert body["sender_name"] == user["display_name"]
    assert "id" in body
    assert "created_at" in body

    # Redis publish called once for the new message
    mock_redis.publish.assert_awaited_once()
    topic, payload = mock_redis.publish.call_args[0]
    assert f"channel:{cid}" == topic
    import json
    data = json.loads(payload)
    assert data["type"] == "message"
    assert data["data"]["content"] == "hello world"


async def test_post_blank_content_rejected(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "chat"}, headers=headers)
    cid = ch.json()["id"]

    resp = await client.post(_msgs_url(cid), json={"content": "   "}, headers=headers)
    assert resp.status_code == 422


async def test_post_to_nonexistent_channel(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    resp = await client.post(_msgs_url(999999), json={"content": "hi"}, headers=headers)
    assert resp.status_code == 404


async def test_post_non_member_forbidden(client: AsyncClient, register_user) -> None:
    alice_h, _ = await register_user(email="alice@hemut.com", display_name="Alice")
    bob_h, _ = await register_user(email="bob@hemut.com", display_name="Bob")

    ch = await client.post(CHANNELS_URL, json={"name": "alices-room"}, headers=alice_h)
    cid = ch.json()["id"]

    # Bob is not a member
    resp = await client.post(_msgs_url(cid), json={"content": "hi"}, headers=bob_h)
    assert resp.status_code == 403


async def test_post_advances_sender_read_cursor(
    client: AsyncClient, register_user
) -> None:
    """Posting a message should zero out the sender's own unread count."""
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "cursor-test"}, headers=headers)
    cid = ch.json()["id"]

    await client.post(_msgs_url(cid), json={"content": "msg1"}, headers=headers)
    await client.post(_msgs_url(cid), json={"content": "msg2"}, headers=headers)

    listed = await client.get(CHANNELS_URL, headers=headers)
    room = next(c for c in listed.json() if c["id"] == cid)
    assert room["unread_count"] == 0


# ---------------------------------------------------------------------------
# GET — cursor pagination
# ---------------------------------------------------------------------------


async def test_get_messages_empty_channel(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "empty"}, headers=headers)
    cid = ch.json()["id"]

    resp = await client.get(_msgs_url(cid), headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == []
    assert body["has_more"] is False


async def test_get_messages_ascending_order(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "ordered"}, headers=headers)
    cid = ch.json()["id"]

    for i in range(3):
        await client.post(_msgs_url(cid), json={"content": f"msg{i}"}, headers=headers)

    resp = await client.get(_msgs_url(cid), headers=headers)
    msgs = resp.json()["messages"]
    assert len(msgs) == 3
    assert msgs[0]["content"] == "msg0"
    assert msgs[2]["content"] == "msg2"
    ids = [m["id"] for m in msgs]
    assert ids == sorted(ids)


async def test_get_messages_before_id_cursor(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "history"}, headers=headers)
    cid = ch.json()["id"]

    for i in range(5):
        await client.post(_msgs_url(cid), json={"content": f"msg{i}"}, headers=headers)

    # Get all first to grab ids
    all_resp = await client.get(_msgs_url(cid), headers=headers)
    all_ids = [m["id"] for m in all_resp.json()["messages"]]

    # Fetch before the 4th message → should return first 3
    before_id = all_ids[3]
    resp = await client.get(_msgs_url(cid), params={"before_id": before_id}, headers=headers)
    msgs = resp.json()["messages"]
    assert len(msgs) == 3
    assert all(m["id"] < before_id for m in msgs)
    # Still ascending order
    returned_ids = [m["id"] for m in msgs]
    assert returned_ids == sorted(returned_ids)


async def test_get_messages_after_id_replay(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "replay"}, headers=headers)
    cid = ch.json()["id"]

    for i in range(4):
        await client.post(_msgs_url(cid), json={"content": f"msg{i}"}, headers=headers)

    all_resp = await client.get(_msgs_url(cid), headers=headers)
    all_ids = [m["id"] for m in all_resp.json()["messages"]]

    # Replay from after the 2nd message → expect last 2
    after_id = all_ids[1]
    resp = await client.get(_msgs_url(cid), params={"after_id": after_id}, headers=headers)
    msgs = resp.json()["messages"]
    assert len(msgs) == 2
    assert all(m["id"] > after_id for m in msgs)


async def test_get_messages_limit_and_has_more(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "paginated"}, headers=headers)
    cid = ch.json()["id"]

    for i in range(5):
        await client.post(_msgs_url(cid), json={"content": f"msg{i}"}, headers=headers)

    resp = await client.get(_msgs_url(cid), params={"limit": 3}, headers=headers)
    body = resp.json()
    assert len(body["messages"]) == 3
    assert body["has_more"] is True


async def test_get_messages_non_member_forbidden(client: AsyncClient, register_user) -> None:
    alice_h, _ = await register_user(email="alice@hemut.com", display_name="Alice")
    bob_h, _ = await register_user(email="bob@hemut.com", display_name="Bob")

    ch = await client.post(CHANNELS_URL, json={"name": "private"}, headers=alice_h)
    cid = ch.json()["id"]

    resp = await client.get(_msgs_url(cid), headers=bob_h)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Thread replies
# ---------------------------------------------------------------------------


async def test_post_reply_success(client: AsyncClient, register_user) -> None:
    """Posting with parent_id creates a reply linked to the root message."""
    headers, user = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "threads"}, headers=headers)
    cid = ch.json()["id"]

    root = await client.post(_msgs_url(cid), json={"content": "root message"}, headers=headers)
    root_id = root.json()["id"]

    resp = await client.post(
        _msgs_url(cid),
        json={"content": "a reply", "parent_id": root_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["parent_id"] == root_id
    assert body["content"] == "a reply"


async def test_get_thread_replies(client: AsyncClient, register_user) -> None:
    """GET ?parent_id=N returns only replies to that message."""
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "thread-fetch"}, headers=headers)
    cid = ch.json()["id"]

    root = await client.post(_msgs_url(cid), json={"content": "root"}, headers=headers)
    root_id = root.json()["id"]

    # Post a second root message (should NOT appear in the thread)
    other = await client.post(_msgs_url(cid), json={"content": "other root"}, headers=headers)
    other_id = other.json()["id"]

    for i in range(3):
        await client.post(
            _msgs_url(cid),
            json={"content": f"reply {i}", "parent_id": root_id},
            headers=headers,
        )

    resp = await client.get(_msgs_url(cid), params={"parent_id": root_id}, headers=headers)
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) == 3
    assert all(m["parent_id"] == root_id for m in msgs)
    # Other root message must not be in thread results
    assert all(m["id"] != other_id for m in msgs)


async def test_channel_timeline_excludes_replies(client: AsyncClient, register_user) -> None:
    """Default GET (no parent_id) returns only root messages, not replies."""
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "root-only"}, headers=headers)
    cid = ch.json()["id"]

    root = await client.post(_msgs_url(cid), json={"content": "root"}, headers=headers)
    root_id = root.json()["id"]

    await client.post(
        _msgs_url(cid),
        json={"content": "reply", "parent_id": root_id},
        headers=headers,
    )

    resp = await client.get(_msgs_url(cid), headers=headers)
    msgs = resp.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["id"] == root_id


async def test_reply_count_on_root_message(client: AsyncClient, register_user) -> None:
    """Root messages in the channel timeline carry an accurate reply_count."""
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "counts"}, headers=headers)
    cid = ch.json()["id"]

    root = await client.post(_msgs_url(cid), json={"content": "root"}, headers=headers)
    root_id = root.json()["id"]

    for _ in range(2):
        await client.post(
            _msgs_url(cid),
            json={"content": "reply", "parent_id": root_id},
            headers=headers,
        )

    resp = await client.get(_msgs_url(cid), headers=headers)
    msg = next(m for m in resp.json()["messages"] if m["id"] == root_id)
    assert msg["reply_count"] == 2


async def test_reply_to_reply_rejected(client: AsyncClient, register_user) -> None:
    """One level of threading only — replying to a reply returns 400."""
    headers, _ = await register_user()
    ch = await client.post(CHANNELS_URL, json={"name": "nested"}, headers=headers)
    cid = ch.json()["id"]

    root = await client.post(_msgs_url(cid), json={"content": "root"}, headers=headers)
    root_id = root.json()["id"]
    reply = await client.post(
        _msgs_url(cid), json={"content": "reply", "parent_id": root_id}, headers=headers
    )
    reply_id = reply.json()["id"]

    resp = await client.post(
        _msgs_url(cid),
        json={"content": "nested", "parent_id": reply_id},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_reply_cross_channel_rejected(client: AsyncClient, register_user) -> None:
    """parent_id pointing to a message in a different channel returns 400."""
    headers, _ = await register_user()
    ch1 = await client.post(CHANNELS_URL, json={"name": "chan1"}, headers=headers)
    ch2 = await client.post(CHANNELS_URL, json={"name": "chan2"}, headers=headers)
    cid1, cid2 = ch1.json()["id"], ch2.json()["id"]

    root = await client.post(_msgs_url(cid1), json={"content": "root"}, headers=headers)
    root_id = root.json()["id"]

    # Try to reply in channel 2 to a message from channel 1
    resp = await client.post(
        _msgs_url(cid2),
        json={"content": "reply", "parent_id": root_id},
        headers=headers,
    )
    assert resp.status_code == 400
