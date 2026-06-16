# Progress

Update after every completed task. At session start, read this + `CLAUDE.md`.

## Status: Backend scaffold + initial migration done (ca9481fbf6e9), merged to main. Next: auth module.

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
- [ ] Seed (channels, 2 users, 10 shipments)
- [ ] Auth module (model, schemas, JWT, `get_current_user`, register/login)
- [ ] Channels router (list/create/join/leave/read, exclude is_dm from list)
- [ ] Messages router (POST + cursor history)
- [ ] WebSocket `/ws/{user_id}` + ConnectionManager + Redis pub/sub fan-out
- [ ] Presence (lazy last_seen+TTL, heartbeat, sweep broadcast)
- [ ] DMs (find-or-create virtual channel, both memberships)
- [ ] Shipments router + mock lookup
- [ ] AI summarization (Gemini stream → requester WS, cache, fallback)
- [ ] Frontend: `lib/xhr.ts`, `lib/api.ts`, auth context, useWebSocket hook
- [ ] Frontend screens: login/register (XHR), channel list+unread, channel view, DM view, shipment card, presence dots, AI summary panel
- [ ] Tests: auth, channels, messages, AI (mocked)
- [ ] README (setup, architecture, AI justification, tradeoffs, production)
- [ ] Loom 3–5 min

## Current task
Auth module — `app/models.py` (User already defined), schemas, JWT utils, `get_current_user` dependency, register/login router.

## Notes / blockers
- Verify current Gemini Flash model id before wiring AI.
