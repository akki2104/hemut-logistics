"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMessages, getPresence } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useWebSocket, useWSListener } from "@/lib/websocket-context";
import { useWorkspace } from "@/lib/workspace-context";
import type { Message, PresenceStatus } from "@/lib/types";
import MessageList from "./MessageList";
import MessageComposer from "./MessageComposer";
import SummaryPanel from "./SummaryPanel";
import PresenceDot from "./PresenceDot";

const PRESENCE_POLL_MS = 20_000;

/**
 * The main chat pane, shared by channel and DM routes (DMs are virtual
 * channels, so the message plumbing is identical). Responsibilities:
 *   - Load the latest page of history when the channel changes.
 *   - Append live messages from the single WebSocket (deduped by id).
 *   - On reconnect, replay anything missed via GET ?after_id=<last id>.
 *   - Mark the channel active so the sidebar zeroes its unread badge.
 */
export default function ChannelView({ channelId }: { channelId: number }) {
  const { token, user } = useAuth();
  const { connectionEpoch, presence: wsPresence } = useWebSocket();
  const { channels, dms, setActiveChannel } = useWorkspace();

  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const lastIdRef = useRef(0);

  const dm = useMemo(
    () => dms.find((d) => d.channel_id === channelId),
    [dms, channelId]
  );
  const channel = useMemo(
    () => channels.find((c) => c.id === channelId),
    [channels, channelId]
  );

  // Merge messages into state, deduping by id and keeping ascending order.
  const merge = useCallback((incoming: Message[]) => {
    setMessages((prev) => {
      const byId = new Map<number, Message>();
      for (const m of prev) byId.set(m.id, m);
      for (const m of incoming) byId.set(m.id, m);
      const merged = Array.from(byId.values()).sort((a, b) => a.id - b.id);
      if (merged.length) lastIdRef.current = merged[merged.length - 1].id;
      return merged;
    });
  }, []);

  // Mark active (sidebar badge + server read cursor) while mounted here.
  useEffect(() => {
    setActiveChannel(channelId);
    return () => setActiveChannel(null);
  }, [channelId, setActiveChannel]);

  // Load the latest page whenever the channel changes.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    setMessages([]);
    lastIdRef.current = 0;
    getMessages(token, channelId, { limit: 50 })
      .then((res) => {
        if (cancelled) return;
        setMessages(res.messages);
        if (res.messages.length) {
          lastIdRef.current = res.messages[res.messages.length - 1].id;
        }
      })
      .catch(() => {
        /* leave empty; reconnect/replay will retry */
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, channelId]);

  // Live messages for THIS channel.
  useWSListener((event) => {
    if (event.type === "message" && event.data.channel_id === channelId) {
      merge([event.data]);
    }
  });

  // Reconnect replay: when the socket re-opens, fetch anything posted while we
  // were away. Skips the very first epoch (the initial load already covered it).
  const epochRef = useRef<number | null>(null);
  useEffect(() => {
    if (epochRef.current === null) {
      epochRef.current = connectionEpoch;
      return;
    }
    if (connectionEpoch === epochRef.current) return;
    epochRef.current = connectionEpoch;
    if (!token || lastIdRef.current === 0) return;
    getMessages(token, channelId, { afterId: lastIdRef.current, limit: 100 })
      .then((res) => merge(res.messages))
      .catch(() => {});
  }, [connectionEpoch, token, channelId, merge]);

  // Presence for a DM peer (header dot).
  const [peerStatus, setPeerStatus] = useState<PresenceStatus>("offline");
  useEffect(() => {
    if (!token || !dm) return;
    const peerId = dm.peer_id;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await getPresence(token, [peerId]);
        if (!cancelled) setPeerStatus(res.presence[String(peerId)] ?? "offline");
      } catch {
        /* keep last */
      }
    };
    void poll();
    const t = setInterval(poll, PRESENCE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token, dm]);

  const peerLiveStatus = dm ? (wsPresence[dm.peer_id] ?? peerStatus) : undefined;

  const title = dm ? dm.peer_display_name : channel ? channel.name : "…";
  const placeholder = dm
    ? `Message ${dm.peer_display_name}`
    : `Message #${channel?.name ?? ""}`;

  return (
    <div className="flex h-full min-w-0 flex-col">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          {dm ? (
            <PresenceDot status={peerLiveStatus} />
          ) : (
            <span className="text-slate-400">#</span>
          )}
          <h1 className="truncate text-base font-bold text-slate-900">{title}</h1>
          {channel?.description && (
            <span className="truncate text-sm text-slate-400">
              {channel.description}
            </span>
          )}
        </div>
        <SummaryPanel channelId={channelId} />
      </header>

      <MessageList
        messages={messages}
        currentUserId={user?.id ?? -1}
        loading={loading}
      />

      <MessageComposer channelId={channelId} placeholder={placeholder} />
    </div>
  );
}
