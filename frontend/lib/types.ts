/**
 * Shared API + domain types. These mirror the backend Pydantic schemas
 * (see backend/app/schemas.py) and the WebSocket contract in
 * docs/API_CONTRACTS.md. Keep them in sync by hand — there is no codegen.
 */

export interface User {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Channel {
  id: number;
  name: string;
  description: string | null;
  is_dm: boolean;
  created_by: number | null;
  created_at: string;
  unread_count: number;
}

export interface Message {
  id: number;
  channel_id: number;
  sender_id: number;
  sender_name: string;
  content: string;
  created_at: string;
}

export interface MessageList {
  messages: Message[];
  has_more: boolean;
}

export interface DMConversation {
  channel_id: number;
  peer_id: number;
  peer_display_name: string;
  unread_count: number;
}

export interface DMOpen {
  channel_id: number;
  peer: { id: number; display_name: string };
}

export type ShipmentStatus = "IN_TRANSIT" | "DELIVERED" | "DELAYED";

export interface Shipment {
  id: number;
  shipment_ref: string;
  origin: string;
  destination: string;
  carrier: string;
  status: ShipmentStatus;
  eta: string | null;
  created_at: string;
}

export interface SummarizeResponse {
  request_id: string;
  cached: boolean;
  summary: string | null;
}

export interface AskResponse {
  request_id: string;
}

export type PresenceStatus = "online" | "away" | "offline";

/** A directory entry used by the "start a DM" picker. */
export interface DirectoryUser {
  id: number;
  display_name: string;
  email: string;
}

// --- WebSocket frames (server -> client) ----------------------------------

export interface WSMessageEvent {
  type: "message";
  data: Message;
}

export interface WSPresenceEvent {
  type: "presence_update";
  data: {
    user_id: number;
    status: PresenceStatus;
    user_name?: string;
    updated_at?: string;
  };
}

export interface WSAiSummaryEvent {
  type: "ai_summary";
  data: { request_id: string; chunk: string; done: boolean };
}

export interface WSAiAnswerEvent {
  type: "ai_answer";
  data: {
    request_id: string;
    chunk?: string;
    tool_status?: string;
    done: boolean;
  };
}

export interface WSConnectedEvent {
  type: "connected";
  user_id: number;
}

export interface WSPongEvent {
  type: "pong";
}

export interface WSErrorEvent {
  type: "error";
  data: { message: string };
}

export interface WSChannelAddedEvent {
  type: "channel_added";
  data: Channel;
}

export type WSEvent =
  | WSMessageEvent
  | WSPresenceEvent
  | WSAiSummaryEvent
  | WSAiAnswerEvent
  | WSConnectedEvent
  | WSPongEvent
  | WSErrorEvent
  | WSChannelAddedEvent;
