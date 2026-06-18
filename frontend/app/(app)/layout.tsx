"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { WebSocketProvider } from "@/lib/websocket-context";
import { WorkspaceProvider } from "@/lib/workspace-context";
import Sidebar from "@/components/Sidebar";

/**
 * Authenticated shell. Guards every page under (app): unauthenticated users
 * are redirected to /login. Once authenticated, it opens the single
 * WebSocket and provides workspace state to the whole subtree.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { token, hydrated } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (hydrated && !token) router.replace("/login");
  }, [hydrated, token, router]);

  // Avoid flashing the app shell before we know the auth state.
  if (!hydrated || !token) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">
        Loading…
      </div>
    );
  }

  return (
    <WebSocketProvider>
      <WorkspaceProvider>
        <div className="flex h-screen overflow-hidden">
          <Sidebar />
          <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white">{children}</main>
        </div>
      </WorkspaceProvider>
    </WebSocketProvider>
  );
}
