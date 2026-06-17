# Hemut — Real-Time Logistics Collaboration Platform

A Slack-style collaboration platform for a logistics company: channels, 1:1 DMs, real-time
messaging, presence, an inline shipment surface, and one well-executed AI feature
(**"Catch me up"** thread summarization). Built with FastAPI + PostgreSQL + Redis on the
backend and Next.js 14 (App Router, TypeScript strict) on the frontend.

> **Loom walkthrough:** _<add link here>_
> **GitHub repo:** _<add link here>_
> **Deployed URL:** _not deployed — run locally per the steps below_

---

## Table of contents

- [Quick start](#quick-start)
- [Required screens](#required-screens)
- [XHR constraint](#xhr-constraint)
- [Backend endpoints](#backend-endpoints)
- [AI feature — "Catch me up"](#ai-feature--catch-me-up)
- [Infrastructure](#infrastructure)
- [Architecture overview](#architecture-overview)
- [How Redis is used (two distinct roles)](#how-redis-is-used-two-distinct-roles)
- [Real-time correctness](#real-time-correctness)
- [Security](#security)
- [Testing](#testing)
- [Tradeoffs & known limitations](#tradeoffs--known-limitations)
- [Rubric self-assessment](#rubric-self-assessment)
- [Project layout](#project-layout)
- [Full API reference](#full-api-reference)

---

## Quick start

### Prerequisites

- **Docker** (for Postgres 15 + Redis 7)
- **Python 3.11+**
- **Node ≥ 18.17** (developed on Node 24 LTS; Next.js 14 requires ≥ 18.17)
- A **Gemini API key** from [aistudio.google.com](https://aistudio.google.com) (free tier works)

### 1. Infrastructure

```bash
docker compose up -d        # starts Postgres (:5432) and Redis (:6379)
docker compose ps           # verify both are healthy
```

> If you have a local Postgres already bound to `:5432`, stop it first.

### 2. Configuration

```bash
cp .env.example .env        # then edit .env: set GEMINI_API_KEY and a real JWT_SECRET
```

The backend reads a single root `.env`. The frontend reads `frontend/.env.local`:

```bash
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > frontend/.env.local
```

`.env` template:

```
DATABASE_URL=postgresql+asyncpg://hemut:hemut@localhost:5432/hemut
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me
JWT_ALGORITHM=HS256
JWT_EXPIRE_DAYS=7
GEMINI_API_KEY=<your key>
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-flash
```

> Model ids change. `LLM_MODEL` is a one-line swap — the client is provider-agnostic.

### 3. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

alembic upgrade head           # apply migrations (creates all tables + indexes)
python -m app.seed             # seed channels, 2 users, 10 shipments
uvicorn app.main:app --reload  # http://localhost:8000   (Swagger at /docs)
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                    # http://localhost:3000
```

### 5. Seed credentials

Both users have password `password123`:

| Email | Role |
|---|---|
| `dispatcher@hemut.com` | dispatcher |
| `driver@hemut.com` | driver |

Both belong to all seed channels (`#general`, `#route-east`, `#warehouse-mumbai`,
`#dispatch-ops`, `#delays`). Open two browsers (or normal + incognito), log in as each, and
watch messages, presence, and DMs update in real time.

Type a message containing `SHIP-001` through `SHIP-010` to render an inline shipment card.

---

## Required screens

Every required screen is reachable and functional:

| Screen | Route | Notes |
|---|---|---|
| **Login** | `/login` | DB-backed, bcrypt, JWT. Form submits via XHR (see below). |
| **Register** | `/register` | Validates email + password ≥ 8 chars + display name. XHR. |
| **Channel list / sidebar** | `/` (app shell) | Lists joined channels with unread counts + presence dots. `#route-east`, `#warehouse-mumbai` etc. seeded. |
| **Channel message view** | `/channels/[id]` | Real-time messages with sender name, timestamp. Inline shipment cards for `SHIP-\d+` refs. AI summary panel. |
| **Direct message view** | `/dm/[channelId]` | 1:1 DMs, same message/WS path as channels. |
| **Shipment surface** | inline in messages | Any `SHIP-001`…`SHIP-010` ref renders a card (origin, destination, carrier, status, ETA). |
| **Presence indicators** | sidebar + DM list | Green (online), yellow (away), gray (offline). Live via WS `presence_update` frames + 20s poll fallback. |

---

## XHR constraint

> *"Form validation using raw XMLHttpRequest (NOT fetch or axios). We want to see you handle
> HTTP request lifecycle, async, and progress/abort/timeout/error events directly."*

**Five call sites use raw XHR** — `frontend/lib/xhr.ts`:

| Call | Why XHR |
|---|---|
| `xhrLogin` | login form submit |
| `xhrRegister` | register form submit |
| `xhrSendMessage` | message composer submit |
| `xhrCreateChannel` | channel creation form submit (name + description) |
| `xhrAddMember` | member picker form submit (add people to channel) |

`xhrRequest()` (the underlying wrapper) wires every event in the XHR lifecycle:

```
xhr.onload    — transport success; inspects HTTP status; extracts FastAPI error detail
xhr.onerror   — DNS / connection refused / CORS rejection
xhr.ontimeout — server took > 15s; 15 000ms timeout set on xhr.timeout
xhr.onabort   — caller cancels (AbortSignal); navigator unmount triggers this
```

It also accepts an `AbortSignal` so the message-send can be cancelled if the user
navigates away mid-send — the same lifecycle control the assignment asks for, exercised
on a real hot path. Everything else (channels, DMs, history, shipments, presence,
summarize) uses `fetch` via `lib/api.ts`.

---

## Backend endpoints

REST + WebSocket endpoints implemented in FastAPI (async throughout):

**Auth**
- `POST /api/auth/register` — create account (bcrypt hash; returns JWT)
- `POST /api/auth/login` — validate credentials; returns JWT

**Channels**
- `GET /api/channels` — list joined channels (excludes DMs); includes unread count
- `POST /api/channels` — create channel
- `POST /api/channels/{id}/members` — add another user (caller must be a member)
- `POST /api/channels/{id}/leave` — leave channel
- `POST /api/channels/{id}/read` — advance read cursor (clears unread)

**Messages** (channel + DM both use the same path)
- `POST /api/channels/{id}/messages` — post message; `sender_id` derived from JWT
- `GET /api/channels/{id}/messages` — paginated history (`?before_id=` / `?after_id=`)

**Direct messages**
- `POST /api/dm/{peer_user_id}` — find-or-create DM channel (idempotent)
- `GET /api/dm` — list caller's DM conversations with peer info + unread count

**Shipments (mock)**
- `GET /api/shipments/{ref}` — lookup by ref (case-insensitive; 404 on miss)

**AI**
- `POST /api/channels/{id}/summarize` — trigger summary; streams over requester's WS

**Presence**
- `GET /api/presence?user_ids=1,2,3` — online / away / offline per user

**Users**
- `GET /api/users` — directory (for DM picker); returns id/email/display_name only

**WebSocket**
- `WS /api/ws?token=<JWT>` — one connection per user; receives `message`,
  `presence_update`, `ai_summary`, `channel_added` frames

**Pagination** is cursor-based by `message_id` (`?before_id=` / `?after_id=&limit=`),
never offset-based — correct under concurrent inserts and the reconnect-replay mechanism.

---

## AI feature — "Catch me up"

### Why this feature

Logistics dispatchers coming onto a shift must catch up on overnight channel activity —
reading 100+ messages across `#route-east` and `#dispatch-ops` is slow and error-prone.
A summary of *what happened, who handled what, which shipments are involved, and what's
still blocked* is concrete, real user value tied to genuine pain — not novelty for its own
sake.

### How it's implemented

- **Trigger:** "✨ Catch me up" button in the channel header →
  `POST /api/channels/{id}/summarize`.
- **Context:** the backend fetches the **last 50 messages** (hard cap — never the full
  history) and builds a logistics-flavored system prompt that asks the model to extract
  shipment refs, delays, ETAs, action items, and owners.
- **Model:** Gemini Flash via the **OpenAI-compatible endpoint** using the `openai`
  package (`AsyncOpenAI`, `base_url=.../v1beta/openai/`, `stream=True`). Fully
  provider-agnostic — swapping to Groq / OpenRouter / OpenAI is a `.env` change only.
- **Streaming delivery:** each chunk is pushed **only to the requester's WebSocket
  connection** (`manager.send_to`) as an `ai_summary` frame with a `request_id`
  correlation id and a final `done:true` frame. It is **never** published to the
  channel's Redis topic — other members never receive someone else's private summary.
- **Caching:** `summary:{channel_id}` in Redis with a 5-minute TTL. A warm cache returns
  synchronously in the HTTP body (`cached:true`). An empty channel returns a canned
  response with no LLM call.
- **Resilience:** the stream runs in a background `asyncio.create_task` (reference held
  in a module-level set to prevent GC), with a 20-second overall timeout and a graceful
  fallback chunk on any error. The task never raises and never crashes the channel.

### What would change in production

- **Grounding / anti-hallucination:** validate any shipment refs the model emits against
  the `shipments` table before surfacing them; cite the source messages.
- **Rate limiting:** 1 summary / user / 5 min (Redis counter) to cap cost and abuse.
- **Cost & observability:** monitor token spend and latency per request; tune the context
  cap and cache TTL based on real usage patterns.
- **Refusals:** detect and decline out-of-context queries rather than inventing answers.
- **User trust:** clearly label AI output; surface which messages were used as sources.

### Prompt-injection defense

Retrieved chat text is treated as **untrusted data, not instructions**. The system prompt
explicitly frames messages as data and injects them below a clear separator boundary,
following the "data vs instructions" mitigation for prompt injection.

---

## Infrastructure

| Requirement | How |
|---|---|
| **PostgreSQL as primary datastore** | All durable data (users, channels, memberships, messages, shipments) lives in Postgres. No in-memory dicts/lists. |
| **Redis for pub/sub fan-out** | Every `message` event is published to `channel:{id}`; WS tasks subscribe and relay to their user's socket. Fan-out works across multiple workers. |
| **Redis for caching** | `presence:{user_id}` (90s TTL) and `summary:{channel_id}` (5-min TTL). |
| **Required tables** | `users`, `channels` (includes DMs via `is_dm`), `memberships`, `messages`, `shipments`. Plus Alembic migration history. |
| **Docker** | `docker-compose.yml` brings up Postgres 15 on `:5432` and Redis 7 on `:6379`. |
| **`.env` for config** | `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `GEMINI_API_KEY`, `LLM_MODEL`. Frontend reads `NEXT_PUBLIC_API_URL` from `frontend/.env.local`. |
| **Public GitHub repo** | _<add link here>_ |

**Schema highlights:**

- `messages(channel_id, id)` composite index — powers cursor pagination
- `memberships` indexed on both FKs; unique `(user_id, channel_id)`
- `channels.is_dm` boolean — DMs reuse the channel table; public lists filter them out
- Alembic from the first migration — no `create_all()` in app code

---

## Architecture overview

```
Browser (Next.js 14, App Router)
  ├── XHR  (lib/xhr.ts)   → REST: login, register, post-message  [graded constraint]
  ├── fetch (lib/api.ts)  → REST: channels, history, DMs, shipments, presence, summarize
  └── WebSocket (1 per user) → /api/ws?token=<JWT>
          ↳ receives: message, presence_update, ai_summary, channel_added

FastAPI worker (async throughout)
  ├── REST handlers → validate → PostgreSQL (durable source of truth)
  ├── on new message → persist → publish to Redis channel:{id}
  ├── WS endpoint → 1 connection per user; background task subscribes to
  │                 user's channel:{id} topics + personal user:{id} topic
  │                 and relays every Redis event to that user's socket
  └── AI service → Gemini Flash (streaming) → chunks pushed to requester's
                   socket only via manager.send_to (never the channel topic)

Redis                              PostgreSQL
  ├─ pub/sub fan-out across workers  users · channels · memberships · messages · shipments
  └─ ephemeral: presence, summary      (durable; Alembic migrations; indexed)
       cache
```

**Key design decisions** (full rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):

- **One WebSocket per user**, not per channel. The server filters events by the user's
  memberships. Fan-out across workers goes through Redis pub/sub.
- **DMs are virtual channels** — a DM between users A and B is a channel named
  `dm_{min(A,B)}_{max(A,B)}` with `is_dm=true` and both memberships created atomically.
  This reuses the entire message / history / WS path with zero new code.
- **Presence = lazy `last_seen` + TTL** in Redis (`presence:{user_id}`, 90s TTL, refreshed
  by a 30s client heartbeat). Status computed on read: fresh → `online`, stale → `away`,
  expired → `offline`. No keyspace notifications needed.
- **Alembic from the first migration** — no `create_all()` in app code.
- **Cursor pagination by message id** — never offset-based, correct under concurrent inserts
  and the reconnect-replay mechanism.
- **Two Redis connection pools** (`redis_pool` for commands, `redis_pubsub_pool` with
  `socket_timeout=None` for subscribers) so a blocked `listen()` on an idle channel doesn't
  time out and crash the subscriber task.

---

## How Redis is used (two distinct roles)

| Role | Pool | What | Where |
|---|---|---|---|
| **(a) Pub/sub fan-out** | `redis_pubsub_pool` (`socket_timeout=None`) | Broadcast `message` to `channel:{id}`; `channel_added` to `user:{id}`; WS task subscribes and relays to socket | `routers/messages.py`, `routers/channels.py`, `routers/ws.py` |
| **(b) Caching / ephemeral state** | `redis_pool` | `presence:{user_id}` (90s TTL) and `summary:{channel_id}` (5-min TTL) | `routers/ws.py`, `services/ai.py` |

Postgres is the **only** durable store. Nothing that must survive a restart lives in Redis.

---

## Real-time correctness

- **Message ids are monotonic** (`BIGSERIAL`). The client tracks the last id it has seen.
- **Reconnect replay:** on every (re)connect the client calls
  `GET /api/channels/{id}/messages?after_id=<last_seen>` to replay anything missed, then
  resumes the live stream. Incoming messages are deduplicated by id and ordered by id, so
  reconnects never drop, duplicate, or reorder messages.
- **Lifecycle across navigation:** a single WS provider owns the socket; channel views
  subscribe / unsubscribe without churning the connection.
- **React Strict Mode safe:** connection state is closure-local per effect run so the
  dev-mode double-mount doesn't open two WS connections.
- **Channel membership in real time:** adding a user to a channel publishes
  `channel_added` to their personal `user:{id}` Redis topic; the client force-reconnects
  so the WS task re-queries memberships and subscribes to the new topic.
- **Exponential backoff reconnect** with capped delay; 30s ping heartbeat refreshes
  presence and keeps the socket alive.

---

## Security

- **`sender_id` from JWT only** — never trusted from the client body (`routers/messages.py`).
- **Every query scoped by membership** — no channel data returned to a non-member; no
  tenancy leaks.
- **Passwords** hashed with bcrypt; login errors are deliberately vague ("Invalid email or
  password") to prevent user enumeration.
- **WebSocket auth** via a short-lived JWT in the query param (browsers can't set WS
  headers), decoded and validated before the connection is accepted.
- **XSS:** message content is escaped on render (React default; no `dangerouslySetInnerHTML`).
- **CSRF:** token-based auth (JWT Bearer); stateless, no cookies.
- **Prompt injection:** retrieved chat text treated as untrusted data, not instructions.

---

## Testing

```bash
cd backend
pytest                         # all 82 tests
pytest tests/test_ai.py -v     # AI feature (mocked LLM)
pytest tests/test_auth.py -v   # auth
pytest tests/test_channels.py -v
pytest tests/test_messages.py -v
pytest tests/test_dms.py -v
```

- **82 backend tests** across auth, channels, messages, DMs, shipments, users, WebSocket,
  and AI.
- Cover **happy paths and failure paths**: auth enforcement (401 on every protected route),
  membership isolation, blank input, wrong password, unknown email, idempotent DM creation,
  self-DM rejection, cache hit/miss, LLM streaming fallback, reconnect replay.
- The **AI test mocks `AsyncOpenAI`** with `pytest-mock` so CI is deterministic and
  non-billable.
- Tests run on a **transaction-rollback session** — no DB truncation between tests, fast
  and isolated.

Frontend tests are encouraged but not required by the assignment; none are included.

---

## Tradeoffs & known limitations

- **No RBAC on `add_member`.** Any channel member can add another user; any user can create
  a channel. In production: admin-only actions would gate on a role claim in the JWT checked
  by a FastAPI dependency, with server-side audit logging.
- **Channel name has no DB unique constraint.** The create endpoint does a soft duplicate
  check, which is racy under concurrency. The durable fix is a partial unique index
  (`WHERE is_dm=false`).
- **Force-reconnect on `channel_added`.** Adding a user triggers a brief WS reconnect so
  the subscriber task picks up the new topic. Simple and reuses the hardened reconnect path
  (which replays missed messages), at the cost of a ~100ms blip. At Slack scale: dynamically
  subscribe on the live connection via inter-task signaling.
- **Presence has a polling fallback.** Live dots come from `presence_update` WS frames; the
  sidebar also polls `GET /api/presence` every 20s as a backstop for missed frames.
- **Context cap at 50 messages for AI.** The summary uses the last 50 messages. A longer
  window needs chunking + map-reduce summarization to avoid token-limit issues.
- **Webhooks not implemented.** Optional in the assignment; not built.
- **Rate-limiting not implemented.** Noted as a production concern in the AI section; no
  middleware added.
- **Deployment not included.** The project runs locally via Docker + uvicorn + Next.js dev.

---

## Rubric self-assessment

| Criterion | Status | Evidence |
|---|---|---|
| **Core Chat** | Strong | Register, login, channels, DMs, real-time delivery, presence all work end-to-end. Reconnect replays missed messages; message ordering guaranteed by monotonic ids + dedup-by-id. |
| **Postgres + Redis** | Strong | Postgres holds all durable data via Alembic migrations with indexes. Redis for pub/sub AND caching — dual roles explicitly separated in code and README. |
| **AI Feature** | Strong | Thread summarization works end-to-end: streams to requester WS only, 5-min Redis cache, 20s timeout + fallback, mocked in tests, README answers why/how/production. |
| **Code Quality** | Strong | FastAPI async throughout; SQLAlchemy 2.0 + Alembic; TypeScript strict (no `any`); router/service separation; Pydantic response models; consistent style. |
| **Real-Time Correctness** | Strong | Cursor-based reconnect replay; dedupe by id; single WS per user; React Strict Mode safe; `channel_added` real-time membership. |
| **Testing** | Passes | 82 backend tests; happy + failure paths; mocked LLM test; transaction-rollback isolation. No frontend tests. |
| **Documentation** | Strong | This file: setup, architecture diagram, AI write-up (why/how/production), Redis dual role, tradeoffs, rubric. Meaningful commit history. |
| **Logistics Context** | Strong | Seeded logistics channels (`#route-east`, `#dispatch-ops`); inline shipment cards; AI feature grounded in dispatcher pain; mock shipments with origin/destination/carrier/status/ETA. |

**Bonus items:**
- Docker setup for local infra ✓
- Clean migration history (Alembic) ✓
- Meaningful commit history (feature branches, `--no-ff` merges) ✓
- Webhooks: not implemented (optional)
- Second AI feature: not implemented (stretch goal)

---

## Conceptual questions

These are sample interview questions from the assignment. Answers below for preparation.

### Architecture & Real-Time

**How would you design this system to handle 10,000+ concurrent users?**

WebSockets with channel-scoped pub/sub via Redis, async FastAPI handlers, horizontal
scaling behind a sticky-session load balancer (or consistent-hash routing so a user's
socket always lands on the same worker), and client-side batching to reduce render churn.
The single-WS-per-user model already separates connection management from channel
fan-out cleanly.

**Why is Redis required alongside PostgreSQL?**

Postgres gives durable storage for messages and users. Redis solves what Postgres can't:
when WebSocket connections are spread across multiple workers, a message posted on worker A
must reach users connected to worker B. Redis pub/sub broadcasts the event so all workers
fan it out. It is also a natural fit for ephemeral state: presence TTLs and summary
caching — things that must expire automatically and don't need ACID guarantees.

**If drivers in low-connectivity areas drop off frequently, how do you handle message delivery?**

Persist messages with monotonic ids, let clients request "messages since id X" on
reconnect, and use heartbeats with exponential-backoff reconnection. This project
implements all three: `after_id` replay on every reconnect, `BIGSERIAL` message ids, 30s
ping heartbeat, capped exponential backoff in `websocket-context.tsx`.

### AI & Product Thinking

**Where in this product would AI create the most value, and why?**

Thread summarization for dispatchers. They come onto a shift needing to catch up on
overnight activity across several channels. Reading 100+ messages manually is slow and
error-prone. A two-sentence summary of what happened, which shipments moved, and what's
still blocked is concrete value tied to real pain — not novelty. Secondary value: delay
detection that surfaces flagged messages to managers so they don't have to monitor every
channel.

**What are the failure modes of LLM answers in a logistics context?**

Hallucinated tracking numbers, wrong ETAs, fabricated shipment status. Mitigations:
ground outputs in retrieved source messages, validate any shipment refs the model emits
against the `shipments` table before surfacing them, cite source messages, refuse
out-of-context queries, and clearly label AI output so users know not to act on it
without verification.

### Security & Frontend

**How would you protect admin-only actions (creating channels, removing members)?**

JWT with role-based claims; a FastAPI dependency checks the role claim before executing.
Audit-log admin actions server-side. This project currently allows any channel member to
add users — the RBAC gap is noted in Tradeoffs.

**What vulnerabilities arise in a multi-user chat product, and how do you prevent them?**

- **XSS:** escape on render (React default; no `dangerouslySetInnerHTML`)
- **CSRF:** JWT Bearer token in Authorization header; stateless, no cookies
- **Message spoofing:** server derives `sender_id` from the JWT, never trusts client body
- **Tenancy leaks:** every query is scoped by membership — non-members get no data
- **Prompt injection:** retrieved chat text framed as data in the system prompt, injected
  below a clear separator, never as instructions

**What are the hardest parts of managing real-time chat state in React?**

Avoiding stale closures in WebSocket handlers (solved with `useRef` for mutable state),
preventing memory leaks on unmount (cleanup in `useEffect` return), reconnecting cleanly
after network loss (exponential backoff + `after_id` replay), ordering interleaved
arrivals (dedup-by-id, sort by id), and keeping re-renders cheap under load (message list
only re-renders on new messages, not on every WS frame type).

---

## Project layout

```
backend/
  app/
    routers/    auth · channels · messages · dm · shipments · users · ai · ws
    services/   ai.py  (Gemini streaming + cache + fallback)
    models.py   SQLAlchemy models  (users · channels · memberships · messages · shipments)
    db.py       async engine + two Redis pools (commands vs pubsub)
    auth.py     JWT + bcrypt + get_current_user dependency
    seed.py     idempotent seed: 5 channels, 2 users, 10 shipments
  alembic/      async env.py + initial schema migration
  tests/        82 tests (LLM mocked)
frontend/
  app/          App Router pages: login, register, (app)/channels/[id], (app)/dm/[channelId]
  components/   Sidebar · ChannelView · MessageList/Item/Composer · ShipmentCard ·
                PresenceDot · SummaryPanel
  lib/          xhr.ts (graded) · api.ts (fetch) · websocket-context · workspace-context ·
                auth-context · types.ts
docs/           ARCHITECTURE.md · API_CONTRACTS.md · PROGRESS.md · GIT_RULES.md
docker-compose.yml
.env.example
```

---

## Full API reference

Full contract in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md). Summary:

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | — | Email + password (bcrypt). Returns `{access_token, user}`. **XHR** from frontend. |
| POST | `/api/auth/login` | — | Validates credentials. Returns JWT. **XHR** from frontend. |
| GET | `/api/channels` | JWT | List joined channels (excl. DMs). Includes unread count. |
| POST | `/api/channels` | JWT | Create channel. |
| POST | `/api/channels/{id}/members` | JWT | Add user by `user_id`. Caller must be a member. |
| POST | `/api/channels/{id}/leave` | JWT | Leave channel. |
| POST | `/api/channels/{id}/read` | JWT | Advance read cursor; clears unread. |
| POST | `/api/channels/{id}/messages` | JWT | Post message. `sender_id` from JWT. **XHR** from frontend. |
| GET | `/api/channels/{id}/messages` | JWT | Cursor history (`?before_id=` / `?after_id=&limit=`). |
| POST | `/api/dm/{peer_id}` | JWT | Find-or-create DM channel (idempotent). |
| GET | `/api/dm` | JWT | List DM conversations with peer info + unread. |
| GET | `/api/shipments/{ref}` | JWT | Mock lookup. Case-insensitive. 404 on miss. |
| POST | `/api/channels/{id}/summarize` | JWT | AI summary. Streams `ai_summary` frames over requester's WS only. |
| GET | `/api/presence?user_ids=1,2` | JWT | Online / away / offline per user. |
| GET | `/api/users` | JWT | Directory for DM picker. Returns id/email/display_name only. |
| WS | `/api/ws?token=<JWT>` | JWT (query param) | One connection per user. Frames: `message` · `presence_update` · `ai_summary` · `channel_added`. |
| GET | `/health` | — | Load balancer health check. |
