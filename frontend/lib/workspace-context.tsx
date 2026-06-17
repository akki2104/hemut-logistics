"use client";

/**
 * Workspace state — the list of channels + DM conversations the user belongs
 * to, plus unread badge counts kept live off the single WebSocket stream.
 *
 * Unread bookkeeping:
 *   - Initial counts come from the server (Channel.unread_count /
 *     DMConversation.unread_count), computed from the membership read cursor.
 *   - A WS `message` frame for a NON-active conversation bumps its badge.
 *   - Opening a conversation zeroes its badge locally and POSTs /read so the
 *     server cursor catches up (keeps the count correct across reloads).
 *   - A frame for a conversation we don't know yet (a peer opened a fresh DM)
 *     triggers a full reload so the new DM appears in the sidebar.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { listChannels, listDMs, markChannelRead } from "./api";
import { useAuth } from "./auth-context";
import { useWebSocket } from "./websocket-context";
import type { Channel, DMConversation } from "./types";

interface WorkspaceState {
  channels: Channel[];
  dms: DMConversation[];
  loading: boolean;
  activeChannelId: number | null;
  setActiveChannel: (channelId: number | null) => void;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  const { subscribe, connectionEpoch, forceReconnect } = useWebSocket();

  const [channels, setChannels] = useState<Channel[]>([]);
  const [dms, setDms] = useState<DMConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeChannelId, setActiveChannelIdState] = useState<number | null>(null);

  // Keep the active id in a ref so the WS listener (registered once) always
  // sees the current value without re-subscribing on every navigation.
  const activeRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [ch, dm] = await Promise.all([listChannels(token), listDMs(token)]);
      setChannels(ch);
      setDms(dm);
    } catch {
      /* transient — next refresh (reconnect / nav) will retry */
    } finally {
      setLoading(false);
    }
  }, [token]);

  // Load on mount and whenever the socket (re)connects — reconnect implies we
  // may have missed messages, so resync the badge counts from the source.
  useEffect(() => {
    void refresh();
  }, [refresh, connectionEpoch]);

  const setActiveChannel = useCallback(
    (channelId: number | null) => {
      activeRef.current = channelId;
      setActiveChannelIdState(channelId);
      if (channelId == null || !token) return;

      // Zero the badge locally for snappy UX...
      setChannels((prev) =>
        prev.map((c) => (c.id === channelId ? { ...c, unread_count: 0 } : c))
      );
      setDms((prev) =>
        prev.map((d) =>
          d.channel_id === channelId ? { ...d, unread_count: 0 } : d
        )
      );
      // ...and advance the server read cursor to the latest message.
      void markChannelRead(token, channelId).catch(() => {});
    },
    [token]
  );

  // Live badge updates from the message stream + channel membership events.
  useEffect(() => {
    const unsub = subscribe((event) => {
      // When we are added to a new channel, force-reconnect the WebSocket so
      // the subscriber task re-queries memberships and subscribes to the new
      // channel's Redis topic. connectionEpoch bump then reloads channels.
      if (event.type === "channel_added") {
        forceReconnect();
        return;
      }

      if (event.type !== "message") return;
      const channelId = event.data.channel_id;
      if (channelId === activeRef.current) return; // viewing it → no badge

      let known = false;
      setChannels((prev) => {
        const next = prev.map((c) => {
          if (c.id === channelId) {
            known = true;
            return { ...c, unread_count: c.unread_count + 1 };
          }
          return c;
        });
        return next;
      });
      setDms((prev) => {
        const next = prev.map((d) => {
          if (d.channel_id === channelId) {
            known = true;
            return { ...d, unread_count: d.unread_count + 1 };
          }
          return d;
        });
        return next;
      });
      // A message for a conversation we don't track yet → a new DM. Reload.
      if (!known) void refresh();
    });
    return unsub;
  }, [subscribe, refresh, forceReconnect]);

  return (
    <WorkspaceContext.Provider
      value={{
        channels,
        dms,
        loading,
        activeChannelId,
        setActiveChannel,
        refresh,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return ctx;
}
