"use client";

/**
 * Single WebSocket connection per user (architecture decision #6). The server
 * filters events by the user's memberships and fans out across workers via
 * Redis pub/sub, so the client needs exactly one socket — not one per channel.
 *
 * Responsibilities:
 *   - Connect to ws(s)://<api>/api/ws?token=<JWT> and keep it alive.
 *   - 30s ping heartbeat (refreshes presence TTL server-side; expects pong).
 *   - Exponential-backoff reconnect when the socket drops unexpectedly.
 *   - Centralize presence_update frames into a map for the whole UI.
 *   - Expose a pub/sub so views subscribe to the frames they care about
 *     (message frames per channel, ai_summary frames per request_id).
 *   - Bump `connectionEpoch` on every (re)open so views can replay missed
 *     messages via GET ?after_id=.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "./auth-context";
import type { PresenceStatus, WSEvent } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const HEARTBEAT_MS = 30_000;
const MAX_BACKOFF_MS = 15_000;

type WSStatus = "connecting" | "open" | "closed";
type Listener = (event: WSEvent) => void;

interface WebSocketState {
  status: WSStatus;
  /** Increments each time a fresh connection opens — drives replay. */
  connectionEpoch: number;
  presence: Record<number, PresenceStatus>;
  /** Subscribe to every inbound frame. Returns an unsubscribe fn. */
  subscribe: (listener: Listener) => () => void;
}

const WebSocketContext = createContext<WebSocketState | null>(null);

function buildWsUrl(token: string): string {
  // http -> ws, https -> wss; everything else stays as-is.
  const wsBase = API_BASE.replace(/^http/, "ws");
  return `${wsBase}/api/ws?token=${encodeURIComponent(token)}`;
}

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();

  const [status, setStatus] = useState<WSStatus>("closed");
  const [connectionEpoch, setConnectionEpoch] = useState(0);
  const [presence, setPresence] = useState<Record<number, PresenceStatus>>({});

  // Refs hold mutable connection machinery that must survive re-renders
  // without retriggering effects.
  const socketRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(1_000);
  const intentionalCloseRef = useRef(false);

  const subscribe = useCallback((listener: Listener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  const emit = useCallback((event: WSEvent) => {
    listenersRef.current.forEach((l) => {
      try {
        l(event);
      } catch {
        /* a misbehaving listener must not break fan-out to the others */
      }
    });
  }, []);

  useEffect(() => {
    // No token → ensure we're fully torn down and idle.
    if (!token) {
      setStatus("closed");
      return;
    }

    intentionalCloseRef.current = false;

    const clearTimers = () => {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      heartbeatRef.current = null;
      reconnectRef.current = null;
    };

    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(buildWsUrl(token));
      socketRef.current = ws;

      ws.onopen = () => {
        setStatus("open");
        backoffRef.current = 1_000; // reset backoff on a clean open
        setConnectionEpoch((e) => e + 1);
        // Start the heartbeat. The server refreshes presence TTL on ping.
        heartbeatRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, HEARTBEAT_MS);
      };

      ws.onmessage = (raw) => {
        let event: WSEvent;
        try {
          event = JSON.parse(raw.data) as WSEvent;
        } catch {
          return;
        }
        // Centralize presence so any component can read the live map.
        if (event.type === "presence_update") {
          setPresence((prev) => ({
            ...prev,
            [event.data.user_id]: event.data.status,
          }));
        }
        emit(event);
      };

      ws.onclose = () => {
        clearTimers();
        socketRef.current = null;
        if (intentionalCloseRef.current) {
          setStatus("closed");
          return;
        }
        // Unexpected drop → reconnect with capped exponential backoff.
        setStatus("connecting");
        const delay = backoffRef.current;
        backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
        reconnectRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onerror is always followed by onclose; let onclose handle retry.
        ws.close();
      };
    };

    connect();

    return () => {
      intentionalCloseRef.current = true;
      clearTimers();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [token, emit]);

  const value = useMemo<WebSocketState>(
    () => ({ status, connectionEpoch, presence, subscribe }),
    [status, connectionEpoch, presence, subscribe]
  );

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocket(): WebSocketState {
  const ctx = useContext(WebSocketContext);
  if (!ctx) {
    throw new Error("useWebSocket must be used within a WebSocketProvider");
  }
  return ctx;
}

/**
 * Subscribe to inbound frames with a stable callback. Handles unsubscribe on
 * unmount and when the handler identity changes. Use this in views instead of
 * calling subscribe() by hand.
 */
export function useWSListener(handler: Listener): void {
  const { subscribe } = useWebSocket();
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const stable: Listener = (event) => handlerRef.current(event);
    return subscribe(stable);
  }, [subscribe]);
}
