"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getMessages } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useWSListener } from "@/lib/websocket-context";
import { xhrSendMessage, XhrError } from "@/lib/xhr";
import type { Message } from "@/lib/types";
import MessageItem from "./MessageItem";

/**
 * Thread panel — shows replies to a single root message and lets the user
 * post replies. Slides in as a right-rail panel in ChannelView.
 *
 * Architecture note:
 *   Replies are stored in the same `messages` table as root messages, with
 *   `parent_id` pointing to their root. The backend's GET /messages endpoint
 *   accepts `?parent_id=N` to return thread replies, and POST /messages accepts
 *   an optional `parent_id` body field to create a reply.
 *
 *   Live replies arrive over the shared channel WebSocket as normal `message`
 *   frames with `parent_id` set — the panel listens for frames where
 *   `parent_id === rootMessage.id` and merges them in.
 */
interface ThreadPanelProps {
  channelId: number;
  rootMessage: Message | null;
  onClose: () => void;
}

export default function ThreadPanel({
  channelId,
  rootMessage,
  onClose,
}: ThreadPanelProps) {
  const { token, user } = useAuth();
  const [replies, setReplies] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const merge = useCallback((incoming: Message[]) => {
    setReplies((prev) => {
      const byId = new Map<number, Message>();
      for (const m of prev) byId.set(m.id, m);
      for (const m of incoming) byId.set(m.id, m);
      return Array.from(byId.values()).sort((a, b) => a.id - b.id);
    });
  }, []);

  // Load existing replies whenever the root message changes
  useEffect(() => {
    if (!token || !rootMessage) {
      setReplies([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setReplies([]);
    getMessages(token, channelId, { parentId: rootMessage.id, limit: 100 })
      .then((res) => {
        if (!cancelled) merge(res.messages);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, channelId, rootMessage?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live replies arriving over the channel WebSocket
  useWSListener((event) => {
    if (
      event.type === "message" &&
      event.data.channel_id === channelId &&
      event.data.parent_id === rootMessage?.id
    ) {
      merge([event.data]);
    }
  });

  // Auto-scroll when a new reply arrives
  const replyCount = replies.length;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [replyCount]);

  const send = async () => {
    const text = content.trim();
    if (!text || !token || !rootMessage || sending) return;
    setSending(true);
    setError(null);
    try {
      await xhrSendMessage(channelId, text, token, undefined, undefined, rootMessage.id);
      setContent("");
      taRef.current?.focus();
    } catch (err) {
      setError(err instanceof XhrError ? err.message : "Failed to send reply");
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  if (!rootMessage) return null;

  const currentUserId = user?.id ?? -1;

  return (
    <div className="flex w-80 shrink-0 flex-col border-l border-slate-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <span className="text-sm font-semibold text-slate-900">Thread</span>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-600"
          aria-label="Close thread"
        >
          ✕
        </button>
      </div>

      {/* Root message (read-only) */}
      <div className="border-b border-slate-100 bg-slate-50 py-2">
        <MessageItem
          message={rootMessage}
          showHeader
          isOwn={rootMessage.sender_id === currentUserId}
        />
      </div>

      {/* Replies */}
      <div className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {loading && (
          <p className="px-4 text-sm text-slate-400">Loading replies…</p>
        )}
        {!loading && replies.length === 0 && (
          <p className="px-4 text-sm text-slate-400">
            No replies yet — start the thread.
          </p>
        )}
        {replies.map((m, i) => {
          const prev = replies[i - 1];
          const sameSender = prev?.sender_id === m.sender_id;
          return (
            <MessageItem
              key={m.id}
              message={m}
              showHeader={!sameSender}
              isOwn={m.sender_id === currentUserId}
            />
          );
        })}
        <div ref={bottomRef} />
      </div>

      {/* Reply composer */}
      <div className="border-t border-slate-200 px-4 py-3">
        {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
        <div className="flex items-end gap-2 rounded-xl border border-slate-300 px-3 py-2 focus-within:border-indigo-500 focus-within:ring-1 focus-within:ring-indigo-500">
          <textarea
            ref={taRef}
            rows={1}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Reply in thread…"
            className="max-h-32 flex-1 resize-none bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400"
          />
          <button
            onClick={() => void send()}
            disabled={sending || !content.trim()}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {sending ? "…" : "Reply"}
          </button>
        </div>
      </div>
    </div>
  );
}
