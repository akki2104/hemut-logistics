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
| POST | `/api/channels/{id}/join` | join |
| POST | `/api/channels/{id}/leave` | leave |
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
| Method | Path | Notes |
|---|---|---|
| POST | `/api/channels/{id}/summarize` | triggers summarization; chunks stream back over the requester's WS as `ai_summary`; returns cached summary if `summary:{id}` is warm |

## WebSocket

Connect: `GET /ws/{user_id}` (auth via token query param or header). All frames are JSON `{type, data}`.

```ts
type WSEvent =
  | { type: "message"; data: { id, channel_id, sender_id, sender_name, content, created_at } }
  | { type: "presence_update"; data: { user_id, status: "online"|"away"|"offline", user_name, updated_at } }
  | { type: "ai_summary"; data: { request_id, chunk: string, done: boolean } }  // requester-only
  | { type: "pong" }
  | { type: "error"; data: { message: string } }

// client → server
type ClientMsg = { type: "ping" }   // heartbeat every 30s, refreshes presence TTL
```

Notes:
- `ai_summary` carries a `request_id` correlation id and is sent only to the requesting connection — never published to a channel topic.
- Client tracks last `message.id`; on reconnect calls `?after_id=` to replay, dedupes by id, orders by id.
