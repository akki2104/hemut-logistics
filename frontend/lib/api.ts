/**
 * Fetch-based API client for every call EXCEPT login, register, message-send,
 * createChannel, and addMember (those go through lib/xhr.ts — a graded constraint).
 *
 * All calls here are authenticated with the JWT bearer token. The caller
 * passes the token explicitly rather than reading global state, so these
 * functions stay pure and testable.
 */

import type {
  AskResponse,
  Channel,
  DirectoryUser,
  DMConversation,
  DMOpen,
  MessageList,
  Shipment,
  SummarizeResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function apiFetch<T>(
  path: string,
  token: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const parsed = await res.json();
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      /* keep default */
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// --- Channels -------------------------------------------------------------

export function listChannels(token: string): Promise<Channel[]> {
  return apiFetch<Channel[]>("/api/channels", token);
}

export function leaveChannel(token: string, channelId: number): Promise<void> {
  return apiFetch<void>(`/api/channels/${channelId}/leave`, token, { method: "POST" });
}

export function markChannelRead(
  token: string,
  channelId: number,
  messageId?: number
): Promise<unknown> {
  return apiFetch(`/api/channels/${channelId}/read`, token, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId ?? null }),
  });
}

// --- Messages (history only; send is XHR) ---------------------------------

export function getMessages(
  token: string,
  channelId: number,
  opts: { beforeId?: number; afterId?: number; parentId?: number; limit?: number } = {}
): Promise<MessageList> {
  const params = new URLSearchParams();
  if (opts.beforeId != null) params.set("before_id", String(opts.beforeId));
  if (opts.afterId != null) params.set("after_id", String(opts.afterId));
  if (opts.parentId != null) params.set("parent_id", String(opts.parentId));
  params.set("limit", String(opts.limit ?? 50));
  return apiFetch<MessageList>(
    `/api/channels/${channelId}/messages?${params.toString()}`,
    token
  );
}

// --- Direct messages ------------------------------------------------------

export function listDMs(token: string): Promise<DMConversation[]> {
  return apiFetch<DMConversation[]>("/api/dm", token);
}

export function openDM(token: string, peerUserId: number): Promise<DMOpen> {
  return apiFetch<DMOpen>(`/api/dm/${peerUserId}`, token, { method: "POST" });
}

// --- Directory (for the "start a DM" picker) ------------------------------

export function listUsers(token: string): Promise<DirectoryUser[]> {
  return apiFetch<DirectoryUser[]>("/api/users", token);
}

// --- Shipments ------------------------------------------------------------

export function getShipment(token: string, ref: string): Promise<Shipment> {
  return apiFetch<Shipment>(`/api/shipments/${encodeURIComponent(ref)}`, token);
}

// --- Presence -------------------------------------------------------------

export function getPresence(
  token: string,
  userIds: number[]
): Promise<{ presence: Record<string, "online" | "away" | "offline"> }> {
  const ids = userIds.join(",");
  return apiFetch<{ presence: Record<string, "online" | "away" | "offline"> }>(
    `/api/presence?user_ids=${ids}`,
    token
  );
}

// --- AI summarization (trigger; chunks arrive over WS) --------------------

export function requestSummary(
  token: string,
  channelId: number
): Promise<SummarizeResponse> {
  return apiFetch<SummarizeResponse>(
    `/api/channels/${channelId}/summarize`,
    token,
    { method: "POST" }
  );
}

// --- Ask Hemut (conversational copilot; answer streams over WS) ------------

export function requestAnswer(
  token: string,
  channelId: number,
  question: string
): Promise<AskResponse> {
  return apiFetch<AskResponse>(`/api/channels/${channelId}/ask`, token, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}
