"""Tests for AI summarization.

The LLM is always mocked — CI must be deterministic and non-billable.
Two layers are tested:
  1. The endpoint (auth, membership, cache hit, empty channel, cache miss).
  2. The streaming service (chunks delivered to WS, caching, fallback).
"""

import json
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message
from app.services import ai

CHANNELS_URL = "/api/channels"


# ---------------------------------------------------------------------------
# Helpers: fake an OpenAI streaming response
# ---------------------------------------------------------------------------


def _chunk(text: str) -> SimpleNamespace:
    """Mimic one openai stream event: event.choices[0].delta.content."""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class _FakeStream:
    """Async-iterable standing in for the openai AsyncStream."""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for c in self._chunks:
            yield c


class _FakeResult:
    """Stands in for a SQLAlchemy Result: .scalars() yields the rows."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self):
        return self._rows


class _FakeSession:
    """Async-context-manager session whose execute() returns fixed rows."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


def _shipment(ref: str, status: str = "DELAYED", eta=None) -> SimpleNamespace:
    return SimpleNamespace(
        shipment_ref=ref,
        status=status,
        origin="Mumbai",
        destination="Delhi",
        carrier="FedEx",
        eta=eta,
    )


# --- Fakes for the Ask tool-calling loop -----------------------------------


def _tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    """Mimic one openai tool_call: .id, .function.name, .function.arguments."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _assistant_with_tools(
    tool_calls: list[SimpleNamespace], content: str = ""
) -> SimpleNamespace:
    """A non-streamed completion whose message carries tool_calls."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _assistant_no_tools(content: str = "") -> SimpleNamespace:
    """A non-streamed completion with no tool_calls (model is ready to answer)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


class _ToolScalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ToolResult:
    """Result supporting both .scalars().all()/.first() and .all() (tuples)."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _ToolScalars:
        return _ToolScalars(self._rows)

    def all(self) -> list:
        return list(self._rows)


class _ToolSession:
    """Plain session for direct tool-fn tests; records every statement.

    `batches` lets a test return different rows per successive execute() call —
    needed by search_messages, which first selects seed ids, then the windowed
    rows. When only `rows` is given, the same batch answers every call.
    """

    def __init__(self, rows: list, *, batches: list | None = None) -> None:
        self._batches = batches if batches is not None else [rows]
        self._i = 0
        self.last_stmt = None
        self.statements: list = []

    async def execute(self, stmt):
        self.last_stmt = stmt
        self.statements.append(stmt)
        batch = self._batches[min(self._i, len(self._batches) - 1)]
        self._i += 1
        return _ToolResult(batch)


async def _create_channel(client: AsyncClient, headers: dict, name: str) -> int:
    resp = await client.post(
        CHANNELS_URL, headers=headers, json={"name": name, "description": None}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Endpoint: auth + membership
# ---------------------------------------------------------------------------


async def test_summarize_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(f"{CHANNELS_URL}/1/summarize")
    assert resp.status_code == 401


async def test_summarize_channel_not_found(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    resp = await client.post(f"{CHANNELS_URL}/9999999/summarize", headers=headers)
    assert resp.status_code == 404


async def test_summarize_non_member_forbidden(
    client: AsyncClient, register_user
) -> None:
    headers_a, _ = await register_user(email="a@hemut.com", display_name="A")
    headers_b, _ = await register_user(email="b@hemut.com", display_name="B")

    channel_id = await _create_channel(client, headers_a, "private-room")

    # B is not a member of A's channel
    resp = await client.post(f"{CHANNELS_URL}/{channel_id}/summarize", headers=headers_b)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Endpoint: synchronous paths (empty channel, cache hit)
# ---------------------------------------------------------------------------


async def test_summarize_empty_channel_returns_canned(
    client: AsyncClient, register_user
) -> None:
    headers, _ = await register_user()
    channel_id = await _create_channel(client, headers, "quiet")

    resp = await client.post(f"{CHANNELS_URL}/{channel_id}/summarize", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert "No recent messages" in body["summary"]
    assert body["request_id"]


async def test_summarize_cache_hit_returns_body(
    client: AsyncClient, register_user, mocker
) -> None:
    """A warm cache returns the summary in the body with cached=true — no LLM call."""
    headers, _ = await register_user()
    channel_id = await _create_channel(client, headers, "warm")

    mocker.patch(
        "app.services.ai.get_cached_summary",
        mocker.AsyncMock(return_value="• Cached summary line"),
    )
    sched = mocker.patch("app.services.ai.schedule_summary")

    resp = await client.post(f"{CHANNELS_URL}/{channel_id}/summarize", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["summary"] == "• Cached summary line"
    sched.assert_not_called()  # cache hit must not trigger streaming


# ---------------------------------------------------------------------------
# Endpoint: cache miss schedules the streaming task
# ---------------------------------------------------------------------------


async def test_summarize_cache_miss_schedules_stream(
    client: AsyncClient, db_session: AsyncSession, register_user, mocker
) -> None:
    headers, user = await register_user()
    channel_id = await _create_channel(client, headers, "busy")

    # Give the channel a message so it isn't treated as empty
    db_session.add(
        Message(channel_id=channel_id, sender_id=user["id"], content="SHIP-001 is delayed")
    )
    await db_session.flush()

    mocker.patch("app.services.ai.get_cached_summary", mocker.AsyncMock(return_value=None))
    sched = mocker.patch("app.services.ai.schedule_summary")

    resp = await client.post(f"{CHANNELS_URL}/{channel_id}/summarize", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["summary"] is None
    assert body["request_id"]

    sched.assert_called_once()
    args = sched.call_args.args
    assert args[0] == user["id"]      # user_id
    assert args[1] == channel_id      # channel_id
    assert args[2] == body["request_id"]


# ---------------------------------------------------------------------------
# Service: streaming, caching, fallback
# ---------------------------------------------------------------------------


async def test_run_summary_stream_delivers_chunks_and_caches(mocker) -> None:
    """Happy path: each delta is pushed to the requester's WS, then cached."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        return_value=_FakeStream([_chunk("Hello "), _chunk("world")])
    )
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch(
        "app.services.ai._fetch_recent",
        mocker.AsyncMock(return_value=("general", [("Alice", "SHIP-001 delayed")])),
    )
    fake_redis = mocker.AsyncMock()
    mocker.patch("app.services.ai.aioredis.Redis", return_value=fake_redis)
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_summary_stream(user_id=1, channel_id=5, request_id="rid-1")

    # Every frame went to user 1 with the right correlation id
    assert send_mock.await_count >= 3  # 2 chunks + final done
    for call in send_mock.await_args_list:
        uid, payload = call.args
        assert uid == 1
        assert payload["type"] == "ai_summary"
        assert payload["data"]["request_id"] == "rid-1"

    # Streamed text concatenates to the full summary
    streamed = "".join(
        c.args[1]["data"]["chunk"] for c in send_mock.await_args_list
    )
    assert "Hello world" in streamed

    # Final frame closes the stream
    assert send_mock.await_args_list[-1].args[1]["data"]["done"] is True

    # Full summary cached with TTL
    fake_redis.setex.assert_awaited_once()
    cache_args = fake_redis.setex.await_args.args
    assert cache_args[0] == ai.SUMMARY_CACHE_KEY.format(channel_id=5)
    assert cache_args[1] == ai.SUMMARY_CACHE_TTL
    assert "Hello world" == cache_args[2]


async def test_run_summary_stream_empty_channel(mocker) -> None:
    """Guard path: no rows → single done frame, no LLM call, no cache write."""
    mock_client = mocker.MagicMock()
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch(
        "app.services.ai._fetch_recent",
        mocker.AsyncMock(return_value=("general", [])),
    )
    fake_redis = mocker.AsyncMock()
    mocker.patch("app.services.ai.aioredis.Redis", return_value=fake_redis)
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_summary_stream(user_id=1, channel_id=5, request_id="rid-2")

    mock_client.chat.completions.create.assert_not_called()
    fake_redis.setex.assert_not_called()
    assert send_mock.await_count == 1
    payload = send_mock.await_args_list[0].args[1]
    assert payload["data"]["done"] is True
    assert "No recent messages" in payload["data"]["chunk"]


async def test_run_summary_stream_fallback_on_llm_error(mocker) -> None:
    """LLM error before any chunk → one fallback frame, no cache write, no raise."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        side_effect=RuntimeError("LLM down")
    )
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch(
        "app.services.ai._fetch_recent",
        mocker.AsyncMock(return_value=("general", [("Alice", "hi")])),
    )
    fake_redis = mocker.AsyncMock()
    mocker.patch("app.services.ai.aioredis.Redis", return_value=fake_redis)
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    # Must not raise — the task has no caller to catch it
    await ai.run_summary_stream(user_id=1, channel_id=5, request_id="rid-3")

    fake_redis.setex.assert_not_called()
    assert send_mock.await_count == 1
    payload = send_mock.await_args_list[0].args[1]
    assert payload["data"]["done"] is True
    assert payload["data"]["chunk"] == ai.FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# Per-user rate limiting
# ---------------------------------------------------------------------------


async def test_check_rate_limit_allows_up_to_max(mocker) -> None:
    """First RATE_LIMIT_MAX calls return True; the next one returns False."""
    fake_redis = mocker.AsyncMock()
    # Simulate INCR returning 1, 2, ... up to max+1 on successive calls.
    fake_redis.incr = mocker.AsyncMock(side_effect=list(range(1, ai.RATE_LIMIT_MAX + 2)))

    for i in range(1, ai.RATE_LIMIT_MAX + 1):
        result = await ai.check_rate_limit(fake_redis, user_id=42)
        assert result is True, f"call {i} should be allowed"

    over_budget = await ai.check_rate_limit(fake_redis, user_id=42)
    assert over_budget is False

    # TTL is only set on the first call (count == 1).
    fake_redis.expire.assert_awaited_once_with(
        ai.RATE_LIMIT_KEY.format(user_id=42), ai.RATE_LIMIT_WINDOW
    )


async def test_summarize_rate_limit_returns_429(
    client: AsyncClient, db_session: AsyncSession, register_user, mocker
) -> None:
    """Once the budget is exhausted, the endpoint returns 429 — no LLM call."""
    headers, user = await register_user(email="rl@hemut.com", display_name="RL")
    channel_id = await _create_channel(client, headers, "rate-limit-chan")

    db_session.add(
        Message(channel_id=channel_id, sender_id=user["id"], content="some activity")
    )
    await db_session.flush()

    mocker.patch("app.services.ai.get_cached_summary", mocker.AsyncMock(return_value=None))
    mocker.patch("app.services.ai.check_rate_limit", mocker.AsyncMock(return_value=False))
    sched = mocker.patch("app.services.ai.schedule_summary")

    resp = await client.post(f"{CHANNELS_URL}/{channel_id}/summarize", headers=headers)
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()
    sched.assert_not_called()  # LLM must not be triggered when over budget


# ---------------------------------------------------------------------------
# Grounding / anti-hallucination footer
# ---------------------------------------------------------------------------


async def test_grounding_footer_empty_when_no_refs() -> None:
    """No shipment refs in the summary → no footer, no DB query."""
    footer = await ai.build_grounding_footer("Nothing logistics-y to cite here.")
    assert footer == ""


async def test_grounding_footer_cites_real_and_flags_unknown(mocker) -> None:
    """Real refs are cited from the DB; refs not in the DB are flagged."""
    mocker.patch(
        "app.services.ai.async_session_factory",
        return_value=_FakeSession([_shipment("SHIP-001")]),
    )
    footer = await ai.build_grounding_footer(
        "Recap: SHIP-001 is delayed and SHIP-999 is somewhere."
    )
    assert "Referenced shipments" in footer
    # Real shipment cited with status + route
    assert "`SHIP-001` — DELAYED, Mumbai → Delhi (no ETA)" in footer
    # Hallucinated ref explicitly flagged, not silently trusted
    assert "`SHIP-999`" in footer
    assert "not found" in footer


async def test_run_summary_stream_appends_and_caches_grounding_footer(mocker) -> None:
    """When the summary mentions a shipment, the footer is streamed and cached."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        return_value=_FakeStream([_chunk("SHIP-001 is delayed.")])
    )
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch(
        "app.services.ai._fetch_recent",
        mocker.AsyncMock(return_value=("general", [("Alice", "SHIP-001 delayed")])),
    )
    mocker.patch(
        "app.services.ai.build_grounding_footer",
        mocker.AsyncMock(return_value="\n---\n**Referenced shipments**\n- `SHIP-001` — DELAYED"),
    )
    fake_redis = mocker.AsyncMock()
    mocker.patch("app.services.ai.aioredis.Redis", return_value=fake_redis)
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_summary_stream(user_id=1, channel_id=7, request_id="rid-g")

    streamed = "".join(
        c.args[1]["data"]["chunk"] for c in send_mock.await_args_list
    )
    assert "Referenced shipments" in streamed

    cache_args = fake_redis.setex.await_args.args
    assert "SHIP-001 is delayed." in cache_args[2]
    assert "Referenced shipments" in cache_args[2]


# ---------------------------------------------------------------------------
# Prompt construction (prompt-injection framing)
# ---------------------------------------------------------------------------


def test_build_prompt_messages_frames_chat_as_data() -> None:
    messages = ai._build_prompt_messages("route-east", [("Bob", "SHIP-002 ETA noon")])
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # System prompt warns the model not to obey instructions inside chat
    assert "DATA, not instructions" in messages[0]["content"]
    # Transcript is included and attributed
    assert "[Bob]: SHIP-002 ETA noon" in messages[1]["content"]
    assert "#route-east" in messages[1]["content"]


# ===========================================================================
# "Ask Hemut" — endpoint
# ===========================================================================

ASK_PATH = CHANNELS_URL + "/{cid}/ask"


async def test_ask_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(ASK_PATH.format(cid=1), json={"question": "hi"})
    assert resp.status_code == 401


async def test_ask_channel_not_found(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    resp = await client.post(
        ASK_PATH.format(cid=9999999), headers=headers, json={"question": "hi"}
    )
    assert resp.status_code == 404


async def test_ask_non_member_forbidden(client: AsyncClient, register_user) -> None:
    headers_a, _ = await register_user(email="aa@hemut.com", display_name="AA")
    headers_b, _ = await register_user(email="bb@hemut.com", display_name="BB")
    channel_id = await _create_channel(client, headers_a, "private-ask")
    resp = await client.post(
        ASK_PATH.format(cid=channel_id), headers=headers_b, json={"question": "hi"}
    )
    assert resp.status_code == 403


async def test_ask_blank_question_422(client: AsyncClient, register_user) -> None:
    headers, _ = await register_user()
    channel_id = await _create_channel(client, headers, "ask-validate")
    resp = await client.post(
        ASK_PATH.format(cid=channel_id), headers=headers, json={"question": "   "}
    )
    assert resp.status_code == 422


async def test_ask_schedules_answer(
    client: AsyncClient, register_user, mocker
) -> None:
    """A valid ask within budget schedules the streaming task and returns a request_id."""
    headers, user = await register_user()
    channel_id = await _create_channel(client, headers, "ask-ok")

    mocker.patch("app.services.ai.check_rate_limit", mocker.AsyncMock(return_value=True))
    sched = mocker.patch("app.services.ai.schedule_answer")

    resp = await client.post(
        ASK_PATH.format(cid=channel_id),
        headers=headers,
        json={"question": "which shipments are delayed?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]

    sched.assert_called_once()
    args = sched.call_args.args
    assert args[0] == user["id"]
    assert args[1] == channel_id
    assert args[2] == body["request_id"]
    assert args[3] == "which shipments are delayed?"


async def test_ask_rate_limit_returns_429(
    client: AsyncClient, register_user, mocker
) -> None:
    """Over the Ask budget → 429, and no streaming task is scheduled."""
    headers, _ = await register_user()
    channel_id = await _create_channel(client, headers, "ask-rl")

    mocker.patch("app.services.ai.check_rate_limit", mocker.AsyncMock(return_value=False))
    sched = mocker.patch("app.services.ai.schedule_answer")

    resp = await client.post(
        ASK_PATH.format(cid=channel_id), headers=headers, json={"question": "hello?"}
    )
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()
    sched.assert_not_called()


# ===========================================================================
# "Ask Hemut" — tools
# ===========================================================================


async def test_tool_query_shipments_filters_and_serializes() -> None:
    session = _ToolSession([_shipment("SHIP-004", status="DELAYED")])
    rows = await ai._tool_query_shipments(session, status="DELAYED")
    assert rows == [
        {
            "shipment_ref": "SHIP-004",
            "status": "DELAYED",
            "origin": "Mumbai",
            "destination": "Delhi",
            "carrier": "FedEx",
            "eta": None,
        }
    ]


async def test_tool_get_shipment_found_and_missing() -> None:
    found = await ai._tool_get_shipment(
        _ToolSession([_shipment("SHIP-004")]), ref="ship-004"
    )
    assert found["found"] is True
    assert found["shipment_ref"] == "SHIP-004"

    missing = await ai._tool_get_shipment(_ToolSession([]), ref="SHIP-999")
    assert missing == {"ref": "SHIP-999", "found": False}


async def test_tool_get_channel_history_scoped_to_channel() -> None:
    """get_channel_history must hard-scope its WHERE to the given channel_id."""
    session = _ToolSession([("Alice", "SHIP-004 is delayed"), ("Bob", "network issues")])
    rows = await ai._tool_get_channel_history(session, 5)
    # Returns chronological order (reversed from DESC fetch); both messages present.
    assert {"sender": "Alice", "content": "SHIP-004 is delayed"} in rows
    assert {"sender": "Bob", "content": "network issues"} in rows

    compiled = str(session.last_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "channel_id" in compiled
    assert "5" in compiled  # the channel id is bound — no cross-channel leak


async def test_tool_get_channel_history_empty_channel() -> None:
    """Empty channel returns an empty list, no error."""
    rows = await ai._tool_get_channel_history(_ToolSession([]), 99)
    assert rows == []


async def test_dispatch_tool_unknown_returns_error() -> None:
    out = await ai._dispatch_tool("nope", {}, 1, _ToolSession([]))
    assert json.loads(out) == {"error": "unknown tool nope"}


# ===========================================================================
# "Ask Hemut" — streaming task
# ===========================================================================


async def test_run_answer_stream_executes_tools_then_streams(mocker) -> None:
    """Tool round runs, a tool_status frame is sent, then the answer streams."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        side_effect=[
            _assistant_with_tools(
                [_tool_call("c1", "query_shipments", {"status": "DELAYED"})]
            ),
            _assistant_no_tools(""),  # round 2: no more tools → break
            _FakeStream([_chunk("3 shipments are delayed.")]),  # phase 2 stream
        ]
    )
    mocker.patch("app.services.ai._client", mock_client)
    dispatch = mocker.patch(
        "app.services.ai._dispatch_tool",
        mocker.AsyncMock(return_value='[{"shipment_ref": "SHIP-004"}]'),
    )
    mocker.patch(
        "app.services.ai.async_session_factory",
        return_value=_FakeSession([]),
    )
    mocker.patch(
        "app.services.ai.build_grounding_footer", mocker.AsyncMock(return_value="")
    )
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_answer_stream(
        user_id=1, channel_id=5, request_id="rid-a", question="which are delayed?"
    )

    dispatch.assert_awaited_once()

    frames = [c.args[1] for c in send_mock.await_args_list]
    # All frames are ai_answer to user 1 with the right correlation id
    for f in frames:
        assert f["type"] == "ai_answer"
        assert f["data"]["request_id"] == "rid-a"
    for c in send_mock.await_args_list:
        assert c.args[0] == 1

    # A live tool_status line was emitted
    assert any(f["data"].get("tool_status") for f in frames)
    # The answer text streamed through
    streamed = "".join(f["data"].get("chunk", "") for f in frames)
    assert "delayed" in streamed
    # Final frame closes the stream
    assert frames[-1]["data"]["done"] is True


async def test_run_answer_stream_no_tools_direct_answer(mocker) -> None:
    """If the model calls no tools, dispatch never runs and it answers directly."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        side_effect=[
            _assistant_no_tools(""),
            _FakeStream([_chunk("Here is a direct answer.")]),
        ]
    )
    mocker.patch("app.services.ai._client", mock_client)
    dispatch = mocker.patch("app.services.ai._dispatch_tool", mocker.AsyncMock())
    mocker.patch(
        "app.services.ai.async_session_factory", return_value=_FakeSession([])
    )
    mocker.patch(
        "app.services.ai.build_grounding_footer", mocker.AsyncMock(return_value="")
    )
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_answer_stream(
        user_id=1, channel_id=5, request_id="rid-b", question="hi"
    )

    dispatch.assert_not_awaited()
    streamed = "".join(
        c.args[1]["data"].get("chunk", "") for c in send_mock.await_args_list
    )
    assert "direct answer" in streamed
    assert send_mock.await_args_list[-1].args[1]["data"]["done"] is True


async def test_run_answer_stream_appends_grounding_footer(mocker) -> None:
    """A shipment ref in the answer triggers the grounding footer, then done."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        side_effect=[
            _assistant_no_tools(""),
            _FakeStream([_chunk("SHIP-004 is delayed.")]),
        ]
    )
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch("app.services.ai._dispatch_tool", mocker.AsyncMock())
    mocker.patch(
        "app.services.ai.async_session_factory", return_value=_FakeSession([])
    )
    mocker.patch(
        "app.services.ai.build_grounding_footer",
        mocker.AsyncMock(
            return_value="\n---\n**Referenced shipments**\n- `SHIP-004` — DELAYED"
        ),
    )
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_answer_stream(
        user_id=1, channel_id=5, request_id="rid-c", question="status of SHIP-004?"
    )

    streamed = "".join(
        c.args[1]["data"].get("chunk", "") for c in send_mock.await_args_list
    )
    assert "Referenced shipments" in streamed
    assert send_mock.await_args_list[-1].args[1]["data"]["done"] is True


async def test_run_answer_stream_fallback_on_error(mocker) -> None:
    """LLM error before any chunk → single fallback frame, never raises."""
    mock_client = mocker.MagicMock()
    mock_client.chat.completions.create = mocker.AsyncMock(
        side_effect=RuntimeError("LLM down")
    )
    mocker.patch("app.services.ai._client", mock_client)
    mocker.patch(
        "app.services.ai.async_session_factory", return_value=_FakeSession([])
    )
    send_mock = mocker.AsyncMock()
    mocker.patch.object(ai.manager, "send_to", send_mock)

    await ai.run_answer_stream(
        user_id=1, channel_id=5, request_id="rid-d", question="anything?"
    )

    assert send_mock.await_count == 1
    payload = send_mock.await_args_list[0].args[1]
    assert payload["data"]["done"] is True
    assert payload["data"]["chunk"] == ai.FALLBACK_MESSAGE
