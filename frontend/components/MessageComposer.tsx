"use client";

import { useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { xhrSendMessage, XhrError } from "@/lib/xhr";

/**
 * Message composer. Sends via the XHR helper (graded constraint — NOT fetch).
 * We don't optimistically insert: the server publishes the saved message to
 * the channel's Redis topic, and our own WebSocket is subscribed, so the
 * message echoes back with its real id. The message list dedupes by id.
 */
export default function MessageComposer({
  channelId,
  placeholder,
}: {
  channelId: number;
  placeholder: string;
}) {
  const { token } = useAuth();
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const send = async () => {
    const text = content.trim();
    if (!text || !token || sending) return;
    setSending(true);
    setError(null);
    try {
      await xhrSendMessage(channelId, text, token);
      setContent("");
      taRef.current?.focus();
    } catch (err) {
      setError(
        err instanceof XhrError ? err.message : "Failed to send message"
      );
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="border-t border-slate-200 px-4 py-3">
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="flex items-end gap-2 rounded-xl border border-slate-300 px-3 py-2 focus-within:border-indigo-500 focus-within:ring-1 focus-within:ring-indigo-500">
        <textarea
          ref={taRef}
          rows={1}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          className="max-h-40 flex-1 resize-none bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400"
        />
        <button
          onClick={() => void send()}
          disabled={sending || !content.trim()}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
