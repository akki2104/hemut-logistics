"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { requestSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useWSListener } from "@/lib/websocket-context";

/**
 * "Catch me up" — AI thread summarization.
 *
 * Flow (see backend/app/services/ai.py):
 *   1. POST /api/channels/{id}/summarize returns immediately with a
 *      request_id. If the result is available synchronously (warm cache or an
 *      empty channel) the summary text is in the response body.
 *   2. Otherwise the summary streams token-by-token over THIS user's
 *      WebSocket as `ai_summary` frames carrying the same request_id. We match
 *      on request_id (the correlation id) and append each chunk.
 *   3. A frame with done=true closes the stream.
 *
 * The stream is private — the backend sends it only to the requester's socket,
 * never the channel topic, so other members never see your summary.
 */
export default function SummaryPanel({ channelId }: { channelId: number }) {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [cached, setCached] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimited, setRateLimited] = useState(false);

  // The request we're currently listening for. Frames for any other id are
  // ignored (e.g. a stale request after switching channels).
  const requestIdRef = useRef<string | null>(null);

  useWSListener((event) => {
    if (event.type !== "ai_summary") return;
    if (event.data.request_id !== requestIdRef.current) return;
    if (event.data.chunk) {
      setText((prev) => prev + event.data.chunk);
    }
    if (event.data.done) {
      setStreaming(false);
      requestIdRef.current = null;
    }
  });

  const run = async () => {
    if (!token || loading || streaming) return;
    setOpen(true);
    setLoading(true);
    setError(null);
    setRateLimited(false);
    setText("");
    setCached(false);
    try {
      const res = await requestSummary(token, channelId);
      if (res.summary != null) {
        // Synchronous result: warm cache or empty channel.
        setText(res.summary);
        setCached(res.cached);
        setStreaming(false);
        requestIdRef.current = null;
      } else {
        // Asynchronous: chunks will arrive over the WebSocket.
        requestIdRef.current = res.request_id;
        setStreaming(true);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to summarize";
      setError(msg);
      setRateLimited(msg.toLowerCase().includes("rate limit"));
      setStreaming(false);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => (open ? setOpen(false) : void run())}
        className="flex items-center gap-1.5 rounded-lg bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100"
      >
        <span>✨</span> Catch me up
      </button>

      {open && (
        <div className="absolute right-0 top-full z-40 mt-2 w-96 rounded-xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">
                Channel summary
              </span>
              {cached && (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[0.7rem] font-medium text-slate-500">
                  cached
                </span>
              )}
              {rateLimited && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[0.7rem] font-medium text-amber-700">
                  rate limited
                </span>
              )}
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-slate-400 hover:text-slate-600"
              aria-label="Close summary"
            >
              ✕
            </button>
          </div>

          <div className="max-h-80 overflow-y-auto px-4 py-3 scrollbar-thin">
            {error && <p className="text-sm text-red-600">{error}</p>}

            {!error && loading && !text && (
              <p className="text-sm text-slate-400">Reading the channel…</p>
            )}

            {!error && (text || streaming) && (
              <div className="prose prose-sm prose-slate max-w-none text-slate-800">
                <ReactMarkdown>{text}</ReactMarkdown>
                {streaming && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-indigo-400 align-middle" />
                )}
              </div>
            )}

            {!error && !loading && !text && !streaming && (
              <p className="text-sm text-slate-400">No summary yet.</p>
            )}
          </div>

          <div className="border-t border-slate-100 px-4 py-2 text-right">
            <button
              onClick={() => void run()}
              disabled={loading || streaming || rateLimited}
              className="text-xs font-medium text-indigo-600 hover:underline disabled:opacity-50"
            >
              {streaming ? "Summarizing…" : rateLimited ? "Try again later" : "Regenerate"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
