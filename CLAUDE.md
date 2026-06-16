# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Source of truth.** This file plus `docs/ARCHITECTURE.md`, `docs/API_CONTRACTS.md`, and `docs/PROGRESS.md` are authoritative. At the start of every session: read this file and `docs/PROGRESS.md`, confirm understanding, then take the next task. Do not invent architecture that contradicts these docs — flag the conflict instead.

## Project Overview

Slack-style real-time collaboration platform for a logistics company. 72-hour solo take-home. Must deliver: channels, 1:1 DMs, real-time messaging, presence, a logistics surface (shipment cards), one well-executed AI feature, Postgres + Redis, WebSockets, backend tests, README + Loom.

Realistic build budget is ~12–14h over 72h, and the developer is new to Next.js/React/Docker. **Protect the core chat loop + one AI feature + happy-path tests above all else.** Everything else is bonus.

## Tech Stack (do not deviate)

- **Backend:** FastAPI (async throughout), SQLAlchemy 2.0 + asyncpg, Alembic, JWT (python-jose), passlib+bcrypt, FastAPI WebSockets.
- **DB / cache:** PostgreSQL 15 (Docker, :5432), Redis 7 (Docker, :6379).
- **Frontend:** Next.js 14 **App Router**, TypeScript strict (no `any`), Tailwind (no inline styles).
- **AI:** Gemini Flash via the **OpenAI-compatible** endpoint using the `openai` package (`AsyncOpenAI`), `base_url=https://generativelanguage.googleapis.com/v1beta/openai/`. Streaming on. Provider-agnostic — swapping to Groq/OpenRouter is a `.env` change only.
- **Testing:** pytest, pytest-asyncio, httpx AsyncClient, pytest-mock (LLM mocked).
- **Infra:** docker-compose for Postgres + Redis locally.

## Finalized Architecture Decisions

These were debated and locked. See `docs/ARCHITECTURE.md` for full rationale.

1. **Migrations: Alembic from the start.** First migration = initial schema (autogenerate). No `create_all()` in app code. Async `env.py` pattern is documented in ARCHITECTURE.md — copy it, don't reinvent.
2. **DMs are virtual channels**, not a separate table. Channel name `dm_{minId}_{maxId}` (lower user id first), `is_dm=true`. Reuses all channel/message/WS infrastructure. Both memberships created atomically. **`is_dm=true` channels are excluded from the public channel list.**
3. **Presence = lazy `last_seen` + TTL** (NOT keyspace notifications). Store `presence:{user_id}` in Redis with a `last_seen` timestamp and TTL. Compute online/away/offline on read. A periodic sweep broadcasts changes for live dots. Heartbeat every 30s refreshes it.
4. **XHR scope = forms + message-send.** `lib/xhr.ts` wraps XMLHttpRequest with full `timeout`/`onabort`/`ontimeout`/`onerror`/`onload` handling. Login, register, AND posting a message go through it. Everything else may use `fetch` via `lib/api.ts`. This is the only tooling constraint and is explicitly graded — never use fetch/axios for these three.
5. **Pagination = cursor-based by `message_id`** (`?before_id=` / `?after_id=&limit=`). Never offset-based.
6. **One WebSocket connection per user** (not per channel). Server filters events by the user's memberships. Fan-out across workers via Redis pub/sub.
7. **AI = thread summarization ("Catch me up")** as the single primary feature. Delay detection is documented as a **stretch goal only** — do not build it until core + summarization + tests are done.

## Critical Constraints (never violate)

- **Postgres is the durable store.** Users, channels, memberships, messages, shipments live in Postgres. In-memory dicts/lists as primary storage is an automatic rubric FAIL. Only ephemeral state (presence, unread counts, rate-limit counters, summary cache) lives in Redis.
- **Redis serves two distinct purposes** and the README must keep them separate: (a) pub/sub fan-out across workers, (b) caching/ephemeral state.
- **AI summary streams ONLY to the requester's WS connection** — never publish it to the channel's Redis topic, or every member receives it. Include a correlation id.
- **Server derives `sender_id` from the JWT**, never trusts a client-supplied sender. Scope every query by membership (no tenancy leaks). Escape message content on render (XSS). Treat retrieved chat text as untrusted data in AI prompts (prompt injection).
- **Never commit** `.env`, `__pycache__`, `node_modules`, `.next`.

## Commands

```bash
# Infra
docker compose up -d          # start Postgres + Redis
docker compose ps             # verify
docker compose down           # stop

# Backend (from backend/)
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head          # apply migrations
alembic revision --autogenerate -m "msg"          # new migration after model change
python -m app.seed            # seed channels, test users, shipments
uvicorn app.main:app --reload # http://localhost:8000

# Backend tests
pytest                        # all
pytest tests/test_auth.py -v  # one file
pytest tests/test_ai.py::test_summarize -v   # one test

# Frontend (from frontend/)
npm install
npm run dev                   # http://localhost:3000
npm run build
```

## Environment Variables

Single root `.env` (see `docs/ARCHITECTURE.md` for the template). Backend reads `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_DAYS`, `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `LLM_MODEL`. Frontend reads `NEXT_PUBLIC_API_URL` from `frontend/.env.local`. **Verify the current Gemini Flash model id when wiring** — model ids change; keep it in `.env` so it's a one-line swap.

## Working With This Repo (for Claude Code sessions)

- Ask for **complete features** in one prompt (e.g. "the whole auth module: model + schemas + JWT + dependency + router"), not isolated fragments.
- When fixing a bug, fix it without breaking working code, and **explain what was wrong** (the developer needs to defend every line in interview).
- Prefer explaining *why* a pattern was chosen over alternatives, not just *how*.
- Update `docs/PROGRESS.md` after each completed task.

## Code Style

- **Python:** type hints + docstrings on all public functions; everything DB/external is `async`; `logging` not `print`; return Pydantic models not raw dicts; specific exceptions → correct HTTP status; constants at module top.
- **TypeScript:** strict, no `any`; components PascalCase, hooks camelCase; Tailwind only; `@/` absolute imports; props as interfaces.
- **Commits:** `[scope] description` (e.g. `[backend] add auth endpoints`), frequently, no cluade code as co-author, see `docs/GIT_RULES.md` to follow the commit and push rules.

## References

- `docs/ARCHITECTURE.md` — system design, schema, presence, DM model, WS fan-out, AI feature, rationale, async Alembic env.py.
- `docs/API_CONTRACTS.md` — REST endpoints + WebSocket event types.
- `docs/PROGRESS.md` — task checklist / current status.
- `Hemut_Logistics_TakeHome_Round2.pdf` — original assignment, rubric, conceptual questions.
