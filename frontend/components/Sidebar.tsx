"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useWebSocket } from "@/lib/websocket-context";
import { useWorkspace } from "@/lib/workspace-context";
import { createChannel, getPresence, listUsers, openDM } from "@/lib/api";
import type { DirectoryUser, PresenceStatus } from "@/lib/types";
import PresenceDot from "./PresenceDot";

const PRESENCE_POLL_MS = 20_000;

export default function Sidebar() {
  const { user, token, logout } = useAuth();
  const { status, presence: wsPresence } = useWebSocket();
  const { channels, dms, refresh } = useWorkspace();
  const pathname = usePathname();
  const router = useRouter();

  const [polled, setPolled] = useState<Record<number, PresenceStatus>>({});
  const [showNewChannel, setShowNewChannel] = useState(false);
  const [showNewDM, setShowNewDM] = useState(false);

  // Poll presence for the people we have DMs with. WS presence_update frames
  // (when the server sweep emits them) take precedence over the poll.
  const peerIds = useMemo(() => dms.map((d) => d.peer_id), [dms]);

  useEffect(() => {
    if (!token || peerIds.length === 0) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await getPresence(token, peerIds);
        if (cancelled) return;
        const next: Record<number, PresenceStatus> = {};
        for (const [id, st] of Object.entries(res.presence)) {
          next[Number(id)] = st;
        }
        setPolled(next);
      } catch {
        /* ignore — keep last known */
      }
    };
    void poll();
    const t = setInterval(poll, PRESENCE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token, peerIds]);

  const presenceFor = useCallback(
    (peerId: number): PresenceStatus => wsPresence[peerId] ?? polled[peerId] ?? "offline",
    [wsPresence, polled]
  );

  return (
    <aside className="flex h-full flex-col border-r border-slate-200 bg-slate-50">
      {/* Workspace header */}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-bold text-slate-900">Hemut Logistics</h2>
          <ConnectionPill status={status} />
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3 scrollbar-thin">
        {/* Channels */}
        <SectionHeader
          label="Channels"
          onAdd={() => setShowNewChannel(true)}
          addLabel="Create channel"
        />
        <ul className="mb-4 space-y-0.5">
          {channels.map((c) => {
            const href = `/channels/${c.id}`;
            const active = pathname === href;
            return (
              <li key={c.id}>
                <Link
                  href={href}
                  className={`flex items-center justify-between rounded-md px-2 py-1.5 text-sm ${
                    active
                      ? "bg-indigo-100 font-semibold text-indigo-900"
                      : "text-slate-700 hover:bg-slate-200"
                  }`}
                >
                  <span className="truncate">
                    <span className="text-slate-400">#</span> {c.name}
                  </span>
                  {c.unread_count > 0 && <UnreadBadge count={c.unread_count} />}
                </Link>
              </li>
            );
          })}
          {channels.length === 0 && (
            <li className="px-2 py-1 text-xs text-slate-400">No channels yet</li>
          )}
        </ul>

        {/* Direct messages */}
        <SectionHeader
          label="Direct messages"
          onAdd={() => setShowNewDM(true)}
          addLabel="New direct message"
        />
        <ul className="space-y-0.5">
          {dms.map((d) => {
            const href = `/dm/${d.channel_id}`;
            const active = pathname === href;
            return (
              <li key={d.channel_id}>
                <Link
                  href={href}
                  className={`flex items-center justify-between rounded-md px-2 py-1.5 text-sm ${
                    active
                      ? "bg-indigo-100 font-semibold text-indigo-900"
                      : "text-slate-700 hover:bg-slate-200"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <PresenceDot status={presenceFor(d.peer_id)} />
                    <span className="truncate">{d.peer_display_name}</span>
                  </span>
                  {d.unread_count > 0 && <UnreadBadge count={d.unread_count} />}
                </Link>
              </li>
            );
          })}
          {dms.length === 0 && (
            <li className="px-2 py-1 text-xs text-slate-400">
              No direct messages yet
            </li>
          )}
        </ul>
      </nav>

      {/* Current user + logout */}
      <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-900">
            {user?.display_name}
          </p>
          <p className="truncate text-xs text-slate-400">{user?.email}</p>
        </div>
        <button
          onClick={() => {
            logout();
            router.replace("/login");
          }}
          className="rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-200 hover:text-slate-700"
        >
          Sign out
        </button>
      </div>

      {showNewChannel && (
        <NewChannelDialog
          onClose={() => setShowNewChannel(false)}
          onCreated={async (id) => {
            setShowNewChannel(false);
            await refresh();
            router.push(`/channels/${id}`);
          }}
        />
      )}
      {showNewDM && (
        <NewDMDialog
          onClose={() => setShowNewDM(false)}
          onOpened={async (channelId) => {
            setShowNewDM(false);
            await refresh();
            router.push(`/dm/${channelId}`);
          }}
        />
      )}
    </aside>
  );
}

// --- Small presentational pieces ------------------------------------------

function ConnectionPill({ status }: { status: "connecting" | "open" | "closed" }) {
  const map = {
    open: { dot: "bg-emerald-500", text: "Connected" },
    connecting: { dot: "bg-amber-400 animate-pulse", text: "Connecting…" },
    closed: { dot: "bg-slate-300", text: "Offline" },
  } as const;
  const { dot, text } = map[status];
  return (
    <span className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {text}
    </span>
  );
}

function SectionHeader({
  label,
  onAdd,
  addLabel,
}: {
  label: string;
  onAdd: () => void;
  addLabel: string;
}) {
  return (
    <div className="flex items-center justify-between px-2 py-1">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </h3>
      <button
        onClick={onAdd}
        aria-label={addLabel}
        title={addLabel}
        className="flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-200 hover:text-slate-700"
      >
        +
      </button>
    </div>
  );
}

function UnreadBadge({ count }: { count: number }) {
  return (
    <span className="ml-2 inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-indigo-600 px-1.5 text-xs font-semibold text-white">
      {count > 99 ? "99+" : count}
    </span>
  );
}

// --- Dialogs ---------------------------------------------------------------

function DialogShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-4 text-base font-semibold text-slate-900">{title}</h3>
        {children}
      </div>
    </div>
  );
}

function NewChannelDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (channelId: number) => void;
}) {
  const { token } = useAuth();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      const channel = await createChannel(
        token,
        name.trim(),
        description.trim() || undefined
      );
      onCreated(channel.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create channel");
      setSubmitting(false);
    }
  };

  return (
    <DialogShell title="Create a channel" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. route-west"
          required
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-60"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}

function NewDMDialog({
  onClose,
  onOpened,
}: {
  onClose: () => void;
  onOpened: (channelId: number) => void;
}) {
  const { token } = useAuth();
  const [users, setUsers] = useState<DirectoryUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    if (!token) return;
    listUsers(token)
      .then(setUsers)
      .catch(() => setError("Could not load the team directory"));
  }, [token]);

  const start = async (peerId: number) => {
    if (!token) return;
    setBusyId(peerId);
    setError(null);
    try {
      const res = await openDM(token, peerId);
      onOpened(res.channel_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open DM");
      setBusyId(null);
    }
  };

  return (
    <DialogShell title="Start a direct message" onClose={onClose}>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {users === null && !error && (
        <p className="text-sm text-slate-400">Loading team…</p>
      )}
      {users && users.length === 0 && (
        <p className="text-sm text-slate-400">No one else is here yet.</p>
      )}
      <ul className="max-h-72 space-y-1 overflow-y-auto scrollbar-thin">
        {users?.map((u) => (
          <li key={u.id}>
            <button
              disabled={busyId !== null}
              onClick={() => start(u.id)}
              className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 disabled:opacity-60"
            >
              <span>
                <span className="font-medium text-slate-900">
                  {u.display_name}
                </span>
                <span className="block text-xs text-slate-400">{u.email}</span>
              </span>
              {busyId === u.id && (
                <span className="text-xs text-slate-400">Opening…</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </DialogShell>
  );
}
