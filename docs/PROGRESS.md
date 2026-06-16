# Progress

Update after every completed task. At session start, read this + `CLAUDE.md`.

## Status: Backend feature-complete — auth + channels + messages + WS + presence + shipments + DMs + AI summarization done (79 tests green). Next: frontend.

## Priority order (protect top to bottom under time pressure)
1. **Core chat loop** — auth, channels, post/receive message in real time, presence. (non-negotiable)
2. **DMs** (virtual channels) + reconnect replay (`after_id`).
3. **Shipment surface** — `SHIP-0xx` parsing + card + `/shipments/{ref}`.
4. **AI summarization** ("Catch me up") with streaming + cache + fallback.
5. **Backend tests** — auth, channels, messages, AI (mocked LLM).
6. **README + Loom + Docker polish.**
- Stretch (only if all above done): delay detection (2nd AI feature).

## Checklist
- [x] docker-compose (Postgres + Redis) + `.env`
- [x] Backend scaffold (`main.py`, `db.py`, `config.py`)
- [x] Models + async Alembic env.py configured
- [x] Initial migration (`ca9481fbf6e9`) — all 5 tables + indexes applied
- [x] Seed (channels, 2 users, 10 shipments) — `app/seed.py`
- [x] Auth module (schemas, JWT, `get_current_user`, register/login) — `app/auth.py`, `app/routers/auth.py`
- [x] Channels router (list/create/join/leave/read, exclude is_dm, unread count) — `app/routers/channels.py` + `tests/test_channels.py`
- [x] Messages router (POST + cursor history, Redis publish, sender read-cursor advance) — `app/routers/messages.py` + `tests/test_messages.py`
- [x] WebSocket `/api/ws` + ConnectionManager + Redis pub/sub fan-out — `app/routers/ws.py` + `tests/test_ws.py`
- [x] Presence (lazy last_seen+TTL, heartbeat, `GET /api/presence`) — included in ws.py
- [x] DMs (find-or-create virtual channel, both memberships) — `app/routers/dm.py` + `tests/test_dms.py`
- [x] Shipments router (`GET /api/shipments/{ref}`, case-insensitive, 404 on miss) — `app/routers/shipments.py` + `tests/test_shipments.py`
- [x] AI summarization (Gemini stream → requester WS, cache, fallback) — `app/services/ai.py` + `app/routers/ai.py` + `tests/test_ai.py`
- [x] Frontend: `lib/xhr.ts`, `lib/api.ts`, auth context, WebSocket provider
- [x] Frontend screens: login/register (XHR), channel list+unread, channel view, DM view, shipment card, presence dots, AI summary panel
- [ ] Tests: auth, channels, messages, AI (mocked) — backend done (82); frontend has no test harness yet
- [ ] README (setup, architecture, AI justification, tradeoffs, production)
- [ ] Loom 3–5 min

### Frontend build plan (feature branch: `feature/frontend-chat`) — COMPLETE
1. [x] Scaffold Next.js 14 (TS strict, Tailwind, App Router, `@/*` alias) in `frontend/`
2. [x] `lib/types.ts` + `lib/xhr.ts` (login / register / **send-message** via XHR only — explicitly graded) + `lib/api.ts` (fetch for everything else)
3. [x] Auth context (token + user, localStorage persistence, redirect guards)
4. [x] WebSocket provider — single WS per user (`ws://host/api/ws?token=`), reconnect w/ exponential backoff, `after_id` replay + dedupe-by-id, 30s ping heartbeat
5. [x] Login + register screens (XHR)
6. [x] App shell: channel list + DM list + unread badges + presence dots (green/yellow/gray via `GET /api/presence`)
7. [x] Channel/DM view: message list + send box + inline `SHIP-\d+` shipment cards (`GET /api/shipments/{ref}`)
8. [x] AI summary panel — streaming "Catch me up" (POST `/api/channels/{id}/summarize`, consume `ai_summary` WS frames keyed by `request_id`)
9. [ ] Verify `npm run build` (DONE — green, 7 routes), live smoke test, then merge `feature/frontend-chat` into `main` (`--no-ff`)
- Backend addition this branch: `GET /api/users` directory endpoint for the DM picker (+3 tests, 82 total).

## Current task
Frontend (Next.js 14 App Router) — `lib/xhr.ts` (login/register/message-send via XHR only), `lib/api.ts` (fetch), auth context, `useWebSocket` hook, then screens: login/register, channel list+unread, channel view, DM view, shipment card, presence dots, AI summary panel.

## Notes (AI summarization — for interview defense)
- Model id: `.env` LLM_MODEL set to `gemini-2.5-flash` (1.5 + 2.0 Flash retired; 2.0 shut down 2026-06-01). Bump to `gemini-3.5-flash` for newest GA. One-line swap — provider-agnostic via OpenAI-compatible client.
- Summary streams to requester's WS only (`manager.send_to`), never the channel topic. Correlation id = `request_id`.
- Cache `summary:{channel_id}` in Redis, 5-min TTL. Cache hit + empty channel return synchronously in HTTP body; cache miss streams over WS.
- Background `asyncio.create_task` (ref held in module set vs GC); 20s overall timeout; fallback chunk on error; never raises out of the task.
- **Not live-tested against Gemini yet** — needs docker (PG+Redis) up + a real WS client. Validate during frontend wiring / Loom.

## Notes / blockers
- **Node upgraded to v24.16.0** (was 18.12.1, below Next 14's 18.17 floor) via `winget install OpenJS.NodeJS.LTS`. README "Prerequisites" must state Node ≥ 18.17 (we run 24 LTS).
- Verify current Gemini Flash model id before wiring AI.
- **passlib dropped** — bcrypt 5.x broke passlib's backend probe; auth.py now calls `bcrypt` directly.
- **Channel name has no DB unique constraint** — create endpoint does a soft duplicate check (racy under concurrency). If it matters later: add a partial unique index `WHERE is_dm=false`.
- **No channel discovery endpoint** — GET lists only *joined* channels. Fine for demo (seed joins both users to all). Revisit if "browse channels" is needed.
