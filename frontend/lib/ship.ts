/**
 * Shipment-reference detection for inline card unfurls (Slack-style).
 *
 * Messages may mention shipments like "SHIP-001 is delayed". We detect the
 * pattern client-side and hydrate a card from GET /api/shipments/{ref}.
 */

// Word-bounded, case-insensitive. Captures the digits so we can normalize.
const SHIP_PATTERN = /\bSHIP-\d+\b/gi;

/** Return the unique, uppercased shipment refs mentioned in a string. */
export function extractShipmentRefs(text: string): string[] {
  const matches = text.match(SHIP_PATTERN);
  if (!matches) return [];
  const seen = new Set<string>();
  for (const m of matches) seen.add(m.toUpperCase());
  return Array.from(seen);
}
