// Shared human-readable evidence resolution for brief lines.
//
// Brief lines (`BriefLine`) carry two evidence fields: an opaque
// `evidence_ref` (often a bare knowledge/action/decision id, kept for
// traceability) and a resolved, human-readable `evidence_text` snippet that
// the backend populates from the same confirmed context that grounds the
// brief. The UI should prefer `evidence_text`; when it is absent it may fall
// back to `evidence_ref`, but only when that ref is human-readable free text —
// a bare id (UUID) must never be surfaced as "evidence" (Requirement 10.4).
//
// This helper is shared by every surface that renders `BriefLine` evidence
// (the Dashboard daily brief and the business-entity
// brief) so the logic stays consistent in one place.

import type { BriefLine } from "./types";

/** Matches a bare UUID so a raw id is never surfaced as "evidence". */
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Resolve the human-readable evidence for a brief line. Prefer the backend's
 * resolved `evidence_text`; otherwise fall back to `evidence_ref` only when it
 * is human-readable free text — a bare id (UUID) is never shown, in which case
 * `null` is returned and the caller should render no evidence (Req 10.4).
 */
export function readableEvidence(line: BriefLine): string | null {
  const text = line.evidence_text?.trim();
  if (text) return text;
  const ref = line.evidence_ref?.trim();
  if (ref && !UUID_PATTERN.test(ref)) return ref;
  return null;
}
