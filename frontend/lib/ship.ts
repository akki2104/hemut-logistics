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

/**
 * Normalize a user-typed shipment id into a canonical ref. Accepts
 * "SHIP-001", "ship-1", or bare "1"/"001". Bare digits are zero-padded to 3
 * to match the seeded refs (SHIP-001 … SHIP-010).
 */
export function normalizeShipmentRef(raw: string): string {
  const t = raw.trim().toUpperCase();
  if (/^\d+$/.test(t)) return `SHIP-${t.padStart(3, "0")}`;
  return t;
}

// `/shipment <id>` — the logistics slash command. Captures the id token.
const SLASH_SHIPMENT_PATTERN = /^\/shipment\s+(\S+)\s*$/i;

/**
 * If `text` is a `/shipment <id>` command, return the normalized ref.
 * Otherwise return null (treat as a normal message).
 */
export function parseShipmentCommand(text: string): string | null {
  const m = text.trim().match(SLASH_SHIPMENT_PATTERN);
  return m ? normalizeShipmentRef(m[1]) : null;
}
