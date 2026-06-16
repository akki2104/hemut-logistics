"use client";

import { Fragment } from "react";
import type { Message } from "@/lib/types";
import { extractShipmentRefs } from "@/lib/ship";
import ShipmentCard from "./ShipmentCard";

const SHIP_SPLIT = /(\bSHIP-\d+\b)/gi;
const IS_SHIP = /^SHIP-\d+$/i;

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return (parts[0]?.[0] ?? "?").concat(parts[1]?.[0] ?? "").toUpperCase();
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Render message text, wrapping SHIP-xxx tokens in a subtle mono chip. */
function renderContent(content: string) {
  const parts = content.split(SHIP_SPLIT);
  return parts.map((part, i) =>
    IS_SHIP.test(part) ? (
      <span
        key={i}
        className="rounded bg-indigo-50 px-1 font-mono text-[0.85em] font-medium text-indigo-700"
      >
        {part.toUpperCase()}
      </span>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    )
  );
}

export default function MessageItem({
  message,
  showHeader,
  isOwn,
}: {
  message: Message;
  /** False when this message is grouped under the previous sender's header. */
  showHeader: boolean;
  isOwn: boolean;
}) {
  const refs = extractShipmentRefs(message.content);

  return (
    <div className={`flex gap-3 px-4 ${showHeader ? "mt-3" : "mt-0.5"}`}>
      {/* Avatar column (only on the first message of a group) */}
      <div className="w-9 shrink-0">
        {showHeader && (
          <div
            className={`flex h-9 w-9 items-center justify-center rounded-md text-xs font-semibold text-white ${
              isOwn ? "bg-indigo-500" : "bg-slate-400"
            }`}
          >
            {initials(message.sender_name)}
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        {showHeader && (
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-slate-900">
              {message.sender_name}
            </span>
            <span className="text-xs text-slate-400">
              {formatTime(message.created_at)}
            </span>
          </div>
        )}
        <div className="whitespace-pre-wrap break-words text-sm text-slate-800">
          {renderContent(message.content)}
        </div>
        {refs.map((ref) => (
          <ShipmentCard key={ref} shipmentRef={ref} />
        ))}
      </div>
    </div>
  );
}
