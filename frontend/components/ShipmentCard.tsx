"use client";

import { useEffect, useState } from "react";
import { getShipment } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Shipment, ShipmentStatus } from "@/lib/types";

/**
 * Inline shipment card. Given a SHIP-xxx ref, hydrates from
 * GET /api/shipments/{ref}. On 404 it renders nothing — the message text
 * already shows the ref, so an unknown shipment degrades to plain text.
 *
 * A module-level cache dedupes lookups: the same ref mentioned across many
 * messages is fetched once per session.
 */

const cache = new Map<string, Shipment | null>();

const STATUS_STYLES: Record<ShipmentStatus, string> = {
  IN_TRANSIT: "bg-blue-100 text-blue-800",
  DELIVERED: "bg-emerald-100 text-emerald-800",
  DELAYED: "bg-red-100 text-red-800",
};

function StatusBadge({ status }: { status: ShipmentStatus }) {
  const label = status.replace("_", " ");
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLES[status]}`}
    >
      {label}
    </span>
  );
}

function formatEta(eta: string | null): string {
  if (!eta) return "No ETA";
  const d = new Date(eta);
  if (Number.isNaN(d.getTime())) return "No ETA";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ShipmentCard({ shipmentRef }: { shipmentRef: string }) {
  const { token } = useAuth();
  const [shipment, setShipment] = useState<Shipment | null>(
    cache.get(shipmentRef) ?? null
  );
  const [resolved, setResolved] = useState(cache.has(shipmentRef));

  useEffect(() => {
    if (!token || cache.has(shipmentRef)) {
      setShipment(cache.get(shipmentRef) ?? null);
      setResolved(cache.has(shipmentRef));
      return;
    }
    let cancelled = false;
    getShipment(token, shipmentRef)
      .then((s) => {
        cache.set(shipmentRef, s);
        if (!cancelled) {
          setShipment(s);
          setResolved(true);
        }
      })
      .catch((err) => {
        // 404 → cache the miss so we don't re-fetch; render nothing.
        if (err instanceof ApiError && err.status === 404) {
          cache.set(shipmentRef, null);
        }
        if (!cancelled) {
          setShipment(null);
          setResolved(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token, shipmentRef]);

  if (!resolved || !shipment) return null;

  return (
    <div className="mt-1.5 max-w-md rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm font-semibold text-slate-900">
          {shipment.shipment_ref}
        </span>
        <StatusBadge status={shipment.status} />
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm text-slate-700">
        <span className="font-medium">{shipment.origin}</span>
        <span className="text-slate-400">→</span>
        <span className="font-medium">{shipment.destination}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
        <span>Carrier: {shipment.carrier}</span>
        <span>ETA: {formatEta(shipment.eta)}</span>
      </div>
    </div>
  );
}
