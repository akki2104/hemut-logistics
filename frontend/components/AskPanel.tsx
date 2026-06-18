"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { requestAnswer } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useWSListener } from "@/lib/websocket-context";

/**
 * "Ask Hemut" — conversational AI copilot with tool-calling.
 *
 * Unlike SummaryPanel (single-shot summary), this lets the dispatcher ask a
 * natural-language question. The backend gives the model tools to query the
 * shipments table and search this channel's history; it decides which to call,
 * then streams a grounded answer.
 *
 * Flow (see backend/app/services/ai.py):
 *   1. POST /api/channels/{id}/ask {question} returns a request_id immediately.
 *   2. The answer streams over THIS user's WebSocket as `ai_answer` frames
 *      carrying the same request_id. Three frame kinds:
 *        - tool_status → a live progress line ("Queried shipments (3 found)")
 *        - chunk       → a piece of the answer text
 *        - done:true   → stream complete
 *   3. The stream is private — sent only to the requester's socket.
 */
interface AskPanelProps {
  channelId: number;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}

export default function AskPanel({ channelId, open, onOpen, onClose }: AskPanelProps) {
  const { token } = useAuth();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState("");
  const [steps, setSteps] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimited, setRateLimited] = useState(false);

  // The request we're currently listening for. Frames for any other id are
  // ignored (e.g. a stale request after asking a new question).
  const requestIdRef = useRef<string | null>(null);

  useWSListener((event) => {
    if (event.type !== "ai_answer") return;
    if (event.data.request_id !== requestIdRef.current) return;
    if (event.data.tool_status) {
      setSteps((prev) => [...prev, event.data.tool_status as string]);
    }
    if (event.data.chunk) {
      setAnswer((prev) => prev + event.data.chunk);
    }
    if (event.data.done) {
      setStreaming(false);
      requestIdRef.current = null;
    }
  });

  const ask = async () => {
    const q = question.trim();
    if (!token || !q || loading || streaming) return;
    onOpen();
    setLoading(true);
    setError(null);
    setRateLimited(false);
    setAsked(q);
    setAnswer("");
    setSteps([]);
    try {
      const res = await requestAnswer(token, channelId, q);
      requestIdRef.current = res.request_id;
      setStreaming(true);
      setQuestion("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to ask";
      setError(msg);
      setRateLimited(msg.toLowerCase().includes("rate limit"));
      setStreaming(false);
    } finally {
      setLoading(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void ask();
    }
  };

  const busy = loading || streaming;

  return (
    <div className="relative">
      <button
        onClick={() => (open ? onClose() : onOpen())}
        className="flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100"
      >
        <span>💬</span> Ask Hemut
      </button>

      {open && (
        <div className="absolute right-0 top-full z-40 mt-2 w-[26rem] rounded-xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">
                Ask Hemut
              </span>
              {rateLimited && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[0.7rem] font-medium text-amber-700">
                  rate limited
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600"
              aria-label="Close Ask Hemut"
            >
              ✕
            </button>
          </div>

          {/* Input row */}
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5">
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
              placeholder="Ask about this channel or your shipments…"
              className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:bg-slate-50"
            />
            <button
              onClick={() => void ask()}
              disabled={busy || !question.trim()}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy ? "…" : "Ask"}
            </button>
          </div>

          <div className="max-h-96 overflow-y-auto px-4 py-3 scrollbar-thin">
            {asked && (
              <p className="mb-2 text-sm font-medium text-slate-500">
                <span className="text-slate-400">Q:</span> {asked}
              </p>
            )}

            {error && <p className="text-sm text-red-600">{error}</p>}

            {/* Live tool-call progress — the model reasoning over real data */}
            {!error && steps.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {steps.map((s, i) => (
                  <span
                    key={i}
                    className="rounded-full bg-emerald-50 px-2 py-0.5 text-[0.7rem] font-medium text-emerald-700"
                  >
                    🔍 {s}
                  </span>
                ))}
              </div>
            )}

            {!error && loading && (
              <p className="text-sm text-slate-400">Thinking…</p>
            )}

            {!error && (answer || streaming) && (
              <div className="prose prose-sm prose-slate max-w-none text-slate-800">
                <ReactMarkdown>{answer}</ReactMarkdown>
                {streaming && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-emerald-400 align-middle" />
                )}
              </div>
            )}

            {!error && !loading && !answer && !streaming && !asked && (
              <p className="text-sm text-slate-400">
                Try: “Which shipments are delayed and who’s handling them?”
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
