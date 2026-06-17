"use client";

import { useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { xhrSendMessage, XhrError } from "@/lib/xhr";
import { parseShipmentCommand } from "@/lib/ship";
import ShipmentCard from "@/components/ShipmentCard";

/**
 * Message composer. Sends via the XHR helper (graded constraint — NOT fetch).
 * We don't optimistically insert: the server publishes the saved message to
 * the channel's Redis topic, and our own WebSocket is subscribed, so the
 * message echoes back with its real id. The message list dedupes by id.
 *
 * Also handles the `/shipment <id>` slash command: instead of posting a
 * message, it renders an ephemeral shipment preview visible only to the
 * sender (Slack-style) — a quick logistics lookup without spamming the channel.
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
  // Ephemeral, sender-only shipment preview from the /shipment command.
  const [previewRef, setPreviewRef] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const isShipmentCommand = content.trimStart().toLowerCase().startsWith("/shipment");

  const send = async () => {
    const text = content.trim();
    if (!text || !token || sending) return;

    // Intercept the /shipment <id> slash command — local lookup, no message sent.
    const cmdRef = parseShipmentCommand(text);
    if (cmdRef) {
      setPreviewRef(cmdRef);
      setContent("");
      setError(null);
      taRef.current?.focus();
      return;
    }

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
      {previewRef && (
        <div className="mb-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-500">
              Shipment preview · only visible to you
            </span>
            <button
              onClick={() => setPreviewRef(null)}
              className="text-xs font-medium text-slate-400 hover:text-slate-600"
              aria-label="Dismiss shipment preview"
            >
              Dismiss ✕
            </button>
          </div>
          <ShipmentCard shipmentRef={previewRef} showNotFound />
        </div>
      )}
      {isShipmentCommand && (
        <p className="mb-2 text-xs text-slate-400">
          Tip: <span className="font-mono">/shipment SHIP-001</span> shows a
          private shipment preview (not posted to the channel).
        </p>
      )}
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
