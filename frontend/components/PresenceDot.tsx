import type { PresenceStatus } from "@/lib/types";

const STYLES: Record<PresenceStatus, { color: string; label: string }> = {
  online: { color: "bg-emerald-500", label: "Online" },
  away: { color: "bg-amber-400", label: "Away" },
  offline: { color: "bg-slate-300", label: "Offline" },
};

/** A small status dot. Defaults to offline when status is unknown. */
export default function PresenceDot({
  status,
  className = "",
}: {
  status: PresenceStatus | undefined;
  className?: string;
}) {
  const { color, label } = STYLES[status ?? "offline"];
  return (
    <span
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-white ${color} ${className}`}
      title={label}
      aria-label={label}
    />
  );
}
