// Shared helpers for turning an ApiError into user-facing messages.
//
// The backend surfaces two shapes of error body:
//
//   1. FastAPI/Pydantic *request validation* failures (422): the body is
//      `{ "detail": [ { "loc": ["body", "field", ...], "msg": "...",
//      "type": "..." }, ... ] }`. Each entry points at the offending field via
//      its `loc` path, so we can render the message inline next to that field.
//
//   2. Service-raised `HTTPException`s (401/404/409/422/…): the body is
//      `{ "detail": "a human-readable string" }`. There is no per-field
//      information, so the message is surfaced at the form/summary level.
//
// These helpers normalize both shapes so pages can consistently surface
// field-level validation errors (Requirement) and fall back to a friendly
// message otherwise, without each page re-implementing the parsing.

import { ApiError } from "./client";

/** A map of field name → first validation message for that field. */
export type FieldErrors = Record<string, string>;

interface ValidationDetailItem {
  loc?: unknown;
  msg?: unknown;
}

/**
 * The field name a validation item refers to: the last string segment of its
 * `loc` path (e.g. `["body", "full_name"]` → `"full_name"`). Numeric segments
 * (list indices) are skipped so `["body", "action_items", 0, "title"]` maps to
 * `"title"`. Returns null when no usable field segment is present (e.g. a
 * whole-body error like `["body"]`).
 */
function fieldFromLoc(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  // Drop the leading "body"/"query"/"path" origin marker, then take the last
  // string segment.
  const segments = loc.filter((s): s is string => typeof s === "string");
  const meaningful = segments.filter(
    (s) => s !== "body" && s !== "query" && s !== "path",
  );
  if (meaningful.length === 0) return null;
  return meaningful[meaningful.length - 1];
}

/**
 * Extract per-field validation messages from a 422 `ApiError`.
 *
 * Returns an empty map for non-`ApiError`s, non-422 statuses, or bodies that
 * are not the Pydantic list shape (e.g. a service `{ detail: "string" }`),
 * letting callers fall back to {@link getErrorMessage}. When multiple messages
 * target the same field, the first is kept.
 */
export function parseFieldErrors(error: unknown): FieldErrors {
  if (!(error instanceof ApiError) || error.status !== 422) return {};
  const body = error.body as { detail?: unknown } | undefined;
  const detail = body?.detail;
  if (!Array.isArray(detail)) return {};

  const result: FieldErrors = {};
  for (const raw of detail as ValidationDetailItem[]) {
    const field = fieldFromLoc(raw?.loc);
    const message = typeof raw?.msg === "string" ? raw.msg : null;
    if (field && message && !(field in result)) {
      result[field] = message;
    }
  }
  return result;
}

/**
 * A human-readable, form-level message for an error. Prefers a string
 * `detail` from the backend; otherwise falls back to the provided default.
 * For a Pydantic validation list, returns a generic prompt (callers should
 * pair this with {@link parseFieldErrors} to show the per-field detail).
 */
export function getErrorMessage(
  error: unknown,
  fallback = "Something went wrong. Please retry.",
): string {
  if (error instanceof ApiError) {
    const body = error.body as { detail?: unknown } | undefined;
    const detail = body?.detail;
    if (typeof detail === "string" && detail.trim() !== "") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return "Please correct the highlighted fields and try again.";
    }
  }
  return fallback;
}
