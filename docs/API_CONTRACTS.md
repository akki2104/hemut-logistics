# API Contracts

Evolving spec for REST endpoints and WebSocket events. Keep in sync as endpoints land.

## Auth (XHR from frontend)
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/register` | `{email, password, display_name}` | `{access_token, user}` |
| POST | `/api/auth/login` | `{email, password}` | `{access_token, user}` |

JWT in `Authorization: Bearer <token>`. Server validates credentials against Postgres.

## Channels
| Method | Path | Notes |
|---|---|---|
| GET | `/api/channels` | user's joined channels; **excludes `is_dm=true`**; includes unread count |
| POST | `/api/channels` | `{name, description}` create |
| POST | `/api/channels/{id}/members` | `{user_id}` add a user; caller must be a member |
| POST | `/api/channels/{id}/leave` | caller leaves the channel |
| POST | `/api/channels/{id}/read` | set `last_read_message_id` |

## Messages
| Method | Path | Notes |
|---|---|---|
| POST | `/api/channels/{id}/messages` | **XHR**; `{content}`; `sender_id` derived from JWT; persists then publishes to Redis |
| GET | `/api/channels/{id}/messages?before_id=&after_id=&limit=50` | cursor pagination; `after_id` used for reconnect replay |

## Direct messages
| Method | Path | Notes |
|---|---|---|
| POST | `/api/dm/{peer_user_id}` | find-or-create `dm_{min}_{max}` channel + both memberships; returns channel id |
| GET | `/api/dm` | user's DM list (derived from `is_dm` memberships) |

DM messages reuse the channel message endpoints once the dm channel id is known.

## Shipments
| Method | Path | Notes |
|---|---|---|
| GET | `/api/shipments/{shipment_ref}` | mock lookup by `SHIP-0xx`; powers the inline shipment card |

## AI
| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| POST | `/api/channels/{id}/summarize` | — | `{request_id, summary?, cached?}` | Triggers summarization. If warm cache, returns `summary` synchronously. Otherwise streams `ai_summary` WS frames. |
| POST | `/api/channels/{id}/ask` | `{question: string}` | `{request_id}` | Starts tool-calling copilot. Answer streams as `ai_answer` WS frames to the requester only. `question` max 500 chars. |

## WebSocket

Connect: `GET /ws/{user_id}` (auth via token query param or header). All frames are JSON `{type, data}`.

```ts
type WSEvent =
  | { type: "message"; data: { id, channel_id, sender_id, sender_name, content, created_at } }
  | { type: "presence_update"; data: { user_id, status: "online"|"away"|"offline", user_name, updated_at } }
  | { type: "ai_summary"; data: { request_id: string, chunk?: string, done: boolean } }   // requester-only
  | { type: "ai_answer"; data: { request_id: string, chunk?: string, tool_status?: string, done: boolean } }  // requester-only
  | { type: "channel_added"; data: { channel_id: number } }
  | { type: "pong" }
  | { type: "error"; data: { message: string } }

// client → server
type ClientMsg = { type: "ping" }   // heartbeat every 30s, refreshes presence TTL
```

### `ai_answer` frame details

Three frame shapes arrive over the lifetime of one `/ask` request, all carrying the same `request_id`:

| Shape | When | Client action |
|---|---|---|
| `{ tool_status: "Queried shipments (2 found)" }` | Each tool the model calls | Append to live tool-chip list |
| `{ chunk: "SHIP-003 is…" }` | Each streamed token of the answer | Append to answer text |
| `{ done: true }` | Stream complete | Stop spinner |

Notes:
- Both `ai_summary` and `ai_answer` carry a `request_id` correlation id and are sent to the requesting connection only — never published to a Redis channel topic.
- `tool_status` and `chunk` are mutually exclusive within a single frame.
- Client tracks last `message.id`; on reconnect calls `?after_id=` to replay, dedupes by id, orders by id.
