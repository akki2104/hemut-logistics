# Submission Evaluation & Action Tracker

> Living document. Analysis of the codebase against the Hemut take-home rubric + a
> checkbox action plan. **Update the checkboxes as work lands** so we don't re-analyze.
> Last full analysis: 2026-06-17.

**Verdict:** **"Strong" on 7 of 8 rubric criteria.** Remaining gap is only Loom (required deliverable, no code change needed).
- **AI feature elevated:** summarizer → copilot. "Ask Hemut" adds tool-calling (query_shipments, get_shipment, get_channel_history), two-phase LLM loop, live tool-chip UI, and a separate rate-limit budget. Defensible at every layer.
- **Biggest risk → resolved:** AI grounding live, rate limit shipped, provider-agnostic design validated (Gemini → Groq was a pure `.env` change).
- **Logistics Context → resolved:** 2/3 surfaces done (inline card + slash command); grounding footer ties AI directly to shipment data.

---

## ⚡ Action Plan (check off as completed)

Ordered by ROI. Non-negotiable subset = #1, #6, #7.

- [x] **1. Live-test AI against real Gemini** — confirmed by user: "Catch me up" was tested with a
      real Gemini key and works end-to-end. ✅ (no further action needed)
- [x] **2. `/shipment <id>` slash command** ✅ — `parseShipmentCommand`/`normalizeShipmentRef` in
      `lib/ship.ts`; intercepted in `MessageComposer.tsx` → ephemeral sender-only `ShipmentCard`
      preview (with dismiss + typing hint). `ShipmentCard` gained a `showNotFound` variant. Build green.
- [x] **3. AI shipment-ref grounding/citation** ✅ — `build_grounding_footer` in `services/ai.py`
      extracts `SHIP-\d+` from the summary, validates against `shipments` table, streams+caches a
      "Referenced shipments" footer citing real ones and **flagging hallucinated refs**. 3 new mocked
      tests pass. README updated (grounding moved to "how it's implemented").
- [x] **4. Rate limit on `/summarize`** ✅ — **shipped.** Redis `INCR`/`EXPIRE` counter
      (`summary_rate:{user_id}`, 5 calls/5 min), enforced **cache-miss path only** so cache hits and
      empty channels never consume budget. 429 raises `HTTPException` with detail string.
      Frontend (`SummaryPanel.tsx`): `rateLimited` state catches the 429, shows amber "rate limited"
      badge + "Try again later" disabled button. 2 new backend tests (unit + endpoint). README has
      "Design note: caching vs. rate limiting" explaining why both levers are needed.
- [x] **5. XHR `onprogress` handler** ✅ — `xhr.onprogress` (download) and `xhr.upload.onprogress`
      (upload) wired in `frontend/lib/xhr.ts` via optional `onProgress` callback on `XhrOptions`.
      Build green. Full XHR lifecycle now demonstrably covered.
- [x] **6. Pre-submit hygiene** ✅ — `pytest` confirmed **98 green**, `npm run build` clean.
      README updated (Ask Hemut section, 98 tests, port 5433 / hemut_test DB notes, API table).
      `docs/API_CONTRACTS.md` updated (`/ask` endpoint + `ai_answer` WS frame).
      Update README Loom link (README.md:8) after recording.
- [ ] **7. Record Loom 3–5 min** (~1.5–2h) — register → real-time msg (two browsers) → DM →
      presence dot → shipment card + `/shipment` cmd → "Catch me up" streaming →
      **kill+reopen tab to show reconnect replay** → narrate architecture/Redis dual-role.
      *Required deliverable.*

**Total ~8.5–10h.** Tight-on-time minimum: #1, #6, #7 (protects all Passes + existing Strongs).

---

## Rubric Status (target: 5+ Strong, none "Does Not Pass")

| Criterion | Current | Gap to Strong | Lifted by |
|---|---|---|---|
| Core Chat | **Strong** ✅ | none (reconnect replay, id dedup+ordering, error states) | — |
| Postgres + Redis | **Strong** ✅ | none (indexed schema, Alembic, two Redis pools) | — |
| AI Feature | **Strong** ✅ | Elevated: copilot with tool-calling (query_shipments, get_shipment, get_channel_history), two-phase loop, streaming answer, rate limit, live tool chips | #1, #3, #4 done + Ask Hemut |
| Code Quality | **Strong** ✅ | none (zero `any`, Tailwind-only, clean contexts) | — |
| Real-Time Correctness | **Strong** ✅ | none (disconnect/replay/lifecycle) | — |
| Testing | Passes (≈Strong) | failure paths + fast rollback already present | (optional FE tests) |
| Documentation | **Strong** ✅ | none (setup, diagram, AI write-up, tradeoffs) | — |
| Logistics Context | **Strong** ✅ | 2 of 3 surfaces done (inline card + slash command); grounding footer ties AI to shipment data | #2 done |

---

## Highlighted / High-Signal Requirements

### Logistics Context — ⚠️ 1 of 3 surfaces (requirement met; Strong needs more)
| Surface | Status | Evidence |
|---|---|---|
| Shipment preview card on a message | ✅ Full | `lib/ship.ts` regex `/\bSHIP-\d+\b/gi`; `ShipmentCard.tsx`; `MessageItem.tsx`; hydrates `GET /api/shipments/{ref}`; status badges; 404→nothing |
| `/shipment <id>` slash command | ✅ Full | `lib/ship.ts` `parseShipmentCommand`/`normalizeShipmentRef`; intercepted in `MessageComposer.tsx`; ephemeral sender-only `ShipmentCard` with `showNotFound` feedback |
| Shipments sidebar | ❌ Missing | `Sidebar.tsx` only Channels + DMs |

### XHR Requirement — ✅ Fully satisfied (5 call sites, raw XHR, no fetch/axios)
login, register, message-send, `xhrCreateChannel`, `xhrAddMember` — all in `frontend/lib/xhr.ts`.
Wired: lifecycle, async (Promise), **abort** (`onabort`+AbortSignal), **timeout** (`xhr.timeout`+`ontimeout`),
**error** (`onerror`), typed `XhrError`. GETs correctly use fetch (`api.ts`).
- ✅ All lifecycle events wired: `onload`, `onerror`, `ontimeout`, `onabort`, `onprogress`, `upload.onprogress`.

### Backend Functional — ✅ Both full
- **Pagination:** cursor-based (`before_id`/`after_id`, `limit` cap 100, `has_more`), indexed `(channel_id, id)` — `routers/messages.py:134–205`. Not offset-based.
- **Shipment lookup:** `GET /api/shipments/{ref}`, case-insensitive, **Postgres-durable** — `routers/shipments.py:38–61`, `models.py:93–104`, `seed.py` (10 shipments).

### Documentation — ✅ All 3 AI questions answered (`README.md:147–185`)
Why (dispatcher catch-up pain), How (last-50 context, Gemini streaming, requester-only WS, 5-min cache,
20s timeout+fallback), Production changes (grounding, rate-limit, observability, refusals). + tradeoffs + ASCII diagram.

### Testing — ✅ Fully satisfied
`backend/tests/test_ai.py` mocks `AsyncOpenAI` (`_client`) + Redis with hardcoded `_FakeStream`.
Deterministic, CI-safe, non-billable. AI tests cover auth/membership/cache hit+miss/streaming/empty/fallback/injection-framing + tool dispatch (query_shipments, get_shipment, get_channel_history) + Ask Hemut rate limit + channel-scope isolation.
**98 tests total**, transaction-rollback isolation (`conftest.py`). Separate `hemut_test` DB prevents seed-data collisions.

---

## Gap Analysis Summary

- **Fully implemented:** auth, channels, messages, DMs, presence, WS lifecycle, pagination, shipment
  lookup, AI service code, Redis dual-role, Postgres schema+indexes, XHR, README, backend tests.
- **Partial:** Logistics surface (1/3); presence idle-detection lag (~35s, acceptable).
- **Missing:** Loom (⚠️ required), shipments sidebar (optional 3rd surface), webhooks (optional),
  RBAC on create/add-member, frontend tests.
- **Incorrect:** none. Latent: `create_channel` soft duplicate check has no DB unique constraint
  (`channels.py:112–121`) — documented tradeoff.

---

## Architecture Notes / Interview Prep

- **Backend:** clean router/service/model split, async throughout, Pydantic responses. One-WS-per-user
  with membership-filtered fan-out. Risks: channel-name race (documented), no rate limit, unread count
  is O(n·m) correlated subquery per `list_channels` (denormalize at scale), pubsub pool capped at 100.
- **Frontend:** exemplary WS lifecycle — effect-local state defeats stale closures + Strict Mode
  double-mount; backoff reconnect; `connectionEpoch` replay; id-keyed dedup+sort. Debt: unbounded
  session shipment cache (use LRU); presence polling redundant once WS flows (intentional backstop).
- **Security:** `sender_id` from JWT ✓, membership-scoped queries ✓, bcrypt + vague login ✓,
  WS JWT validated pre-accept ✓, React auto-escape ✓, prompt-injection framing ✓.
  Gaps: no RBAC on admin actions (PDF conceptual Q), no CSRF token (low risk, bearer-in-header).
- **PDF conceptual Qs map to your code:** 10k users → Redis channel pub/sub + sticky LB; why Redis →
  cross-worker fan-out; low-connectivity drivers → monotonic ids + `after_id` replay + backoff (built!);
  LLM failure modes → grounding/citations (Action #3); admin actions → JWT role claims (documented gap).

---

## Skip List (not worth the time window)
Webhooks, threaded replies, file attachments, delay detection (stretch goal), frontend test
harness. None affect pass/fail; all cost more than they return now.
(Ask Hemut — the 2nd AI feature — was delivered and is live.)
