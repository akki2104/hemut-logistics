# Submission Evaluation & Rubric Self-Assessment

> Last full analysis: 2026-06-18. Reflects current codebase state on `main`.

**Verdict: Strong on 7 of 8 rubric criteria.**
- The only gap is the **Loom recording** — a required deliverable with no code dependency.
- Thread replies, collapsible sidebar, and reply highlight are shipped and live on `main`.
- AI documentation now fully per-feature (why / how / what would change in production).

---

## Rubric Scorecard

| # | Criterion | Score | Evidence |
|---|---|---|---|
| 1 | Core Chat | **Strong** | Channels, 1:1 DMs, real-time messaging via WebSocket, presence dots, reconnect replay, id-dedup+ordering, thread replies with live count updates |
| 2 | Postgres + Redis | **Strong** | Two Redis pools (commands vs pubsub), Alembic migrations from day one, indexed schema, Postgres is the only durable store, Redis strictly ephemeral (presence, summary cache, rate-limit counters) |
| 3 | AI Feature | **Strong** | Two features: "Catch me up" (streaming summarizer, 5-min cache, SHIP-xxx grounding/hallucination flagging, rate limit) + "Ask Hemut" (tool-calling copilot, two-phase loop, streaming answer, live tool chips, separate rate-limit budget). README answers why / how / production for each. |
| 4 | Code Quality | **Strong** | TypeScript strict (zero `any`), Tailwind-only (no inline styles), full type hints + docstrings in Python, `logging` not `print`, Pydantic responses, specific HTTP status codes, constants at module top |
| 5 | Real-Time Correctness | **Strong** | WS lifecycle: effect-local state defeats stale closures + Strict Mode double-mount; capped exponential backoff reconnect; `connectionEpoch` replay via `after_id`; id-keyed dedup+sort. Reply count updates live via WS. |
| 6 | Testing | **Strong** | 104 backend tests: happy paths + failure paths (auth enforcement, membership isolation, blank input, wrong password, cursor pagination, idempotent DM, cache hit/miss, LLM fallback, tool dispatch, Ask Hemut rate limit, channel-scope isolation, thread replies: post/fetch/count/reply-to-reply/cross-channel). LLM mocked — deterministic, non-billable. Transaction-rollback isolation via `testcontainers`. |
| 7 | Documentation | **Strong** | README: setup, ASCII architecture diagram, Redis dual-role explained, AI features each with why/how/what-would-change-in-production, 9 real challenges documented, API reference, security notes, tradeoffs |
| 8 | Logistics Context | **Passes** | 2 of 3 surfaces: inline shipment card (SHIP-xxx regex → `ShipmentCard`), `/shipment <id>` slash command (ephemeral preview + not-found feedback). Shipments sidebar not built. AI grounding footer ties "Catch me up" directly to shipment data. |

---

## Required Deliverable Status

| Deliverable | Status |
|---|---|
| Channels + 1:1 DMs + real-time messaging | ✅ Done |
| Presence | ✅ Done |
| Shipment surface | ✅ Passes (2/3 surfaces) |
| One AI feature with README justification | ✅ Done (two features) |
| Postgres + Redis | ✅ Done |
| WebSockets | ✅ Done |
| Backend tests (LLM mocked) | ✅ Done — 104 tests |
| README + Loom | README ✅ — **Loom ❌ (still needed)** |

---

## XHR Requirement — Fully Satisfied

`frontend/lib/xhr.ts` covers all five graded call sites with raw XMLHttpRequest:
- `xhrLogin`, `xhrRegister`, `xhrSendMessage` (the three explicitly graded)
- `xhrCreateChannel`, `xhrAddMember` (bonus — XHR reused consistently)

Full lifecycle wired: `onload`, `onerror`, `ontimeout`, `onabort`, `onprogress`, `upload.onprogress`, `AbortSignal` integration.

---

## Logistics Context Detail

| Surface | Status |
|---|---|
| Inline shipment card on SHIP-xxx mention | ✅ `lib/ship.ts` regex → `ShipmentCard.tsx` → `GET /api/shipments/{ref}` |
| `/shipment <id>` slash command | ✅ Ephemeral preview in `MessageComposer.tsx`, `showNotFound` variant |
| Shipments sidebar panel | ❌ Not built |

The "Catch me up" grounding footer also ties AI output directly to the shipments table (validates SHIP-xxx refs, flags hallucinated ones).

---

## AI Feature Detail

### "Ask Hemut" — Conversational Copilot
- **Why:** dispatcher asks questions ("Which shipments are delayed?") instead of scanning tables
- **How:** two-phase loop — Phase 1 non-streamed with tools (`query_shipments`, `get_shipment`, `get_channel_history`), Phase 2 streamed final answer; live tool-chip UI; private `ai_answer` WS frames with `request_id`; separate `ask_rate:{user_id}` budget
- **Production:** pgvector context retrieval, Phase 2 fallback frame, rate-limit before Phase 1, tool query bounds, refusal guard

### "Catch me up" — Thread Summarizer
- **Why:** dispatchers catching up on overnight activity need a structured summary, not raw scrollback
- **How:** last 50 messages → logistics system prompt → streaming `ai_summary` WS frames (requester only, never Redis pub); 5-min Redis cache; SHIP-xxx grounding footer with hallucination flags; 20s timeout + graceful fallback; `summary_rate:{user_id}` rate limit (cache-miss path only)
- **Production:** pgvector at scale, smarter cache invalidation, source-message citation, rate-limit tuning

---

## Bonus Features Shipped

These are beyond the rubric baseline and strengthen the demo:

| Feature | What it does |
|---|---|
| Thread replies | Self-referential `parent_id` on `messages` table; right-rail `ThreadPanel`; live reply count via WS; reply button highlights on new reply arrival |
| Collapsible sidebar | `‹` / `›` toggle; CSS `transition-all duration-200`; sidebar manages own width |
| Two AI features | "Ask Hemut" (tool-calling copilot) on top of "Catch me up" (summarizer) |
| Provider-agnostic LLM | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` — Gemini, Groq, OpenRouter all tested |

---

## Remaining Gap

- **Loom recording** — required deliverable. Script: register → real-time msg (two browsers) → DM → presence dot → shipment card + `/shipment` command → "Catch me up" streaming → kill+reopen tab (reconnect replay) → thread reply → narrate Redis dual-role.

---

## Architecture / Interview Prep Notes

**Known documented tradeoffs (ready to defend):**
- Channel-name has no DB unique constraint — soft check is racy; fix is a partial unique index (`WHERE is_dm=false`). Documented in README tradeoffs.
- No RBAC on `add_member` / `create_channel` — any member can add. Fix: JWT role claim + FastAPI dependency. Documented.
- Force-reconnect on `channel_added` — brief ~100ms blip vs. dynamic subscription on live connection. Simpler, documented.
- One-level thread depth — reply-to-reply returns 400. Intentional; enforced server-side.
- Unread count is a correlated subquery per `list_channels` — fine at this scale; denormalize at Slack scale.

**Security posture:**
- `sender_id` always from JWT, never client body ✅
- All queries scoped by membership ✅
- bcrypt + vague login errors ✅
- WS JWT validated pre-accept ✅
- React auto-escape (no `dangerouslySetInnerHTML`) ✅
- Prompt injection: messages framed as DATA; tool queries use bound parameters ✅
