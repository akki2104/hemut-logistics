"use client";

import { useEffect, useRef } from "react";
import type { Message } from "@/lib/types";
import MessageItem from "./MessageItem";

const GROUP_GAP_MS = 5 * 60 * 1000; // start a new header block after 5 min

export default function MessageList({
  messages,
  currentUserId,
  loading,
  onReply,
}: {
  messages: Message[];
  currentUserId: number;
  loading: boolean;
  onReply?: (message: Message) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Autoscroll to the newest message. Keyed on the last id so it fires on
  // append, not on every render.
  const lastId = messages.length ? messages[messages.length - 1].id : 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lastId]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
        Loading messages…
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
        No messages yet — say hello 👋
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-3 scrollbar-thin">
      {messages.map((m, i) => {
        const prev = messages[i - 1];
        const sameSender = prev && prev.sender_id === m.sender_id;
        const closeInTime =
          prev &&
          new Date(m.created_at).getTime() -
            new Date(prev.created_at).getTime() <
            GROUP_GAP_MS;
        const showHeader = !(sameSender && closeInTime);
        return (
          <MessageItem
            key={m.id}
            message={m}
            showHeader={Boolean(showHeader)}
            isOwn={m.sender_id === currentUserId}
            onReply={onReply}
          />
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
