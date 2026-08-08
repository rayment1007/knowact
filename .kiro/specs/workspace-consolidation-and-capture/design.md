# Design Document

## 1. Goal

This feature turns the current collection of separate pages into one clear workspace while adding
safe image, audio, and microphone capture. The design keeps the existing human-verification rule:
AI-produced text is never searchable knowledge until the user confirms it.

The implementation should remain suitable for one individual knowledge worker. Existing
`organization_id` checks are retained as data-isolation infrastructure; they do not change the
product into a multi-user enterprise system.

## 2. Design Principles

1. Show one obvious purpose at a time.
2. Keep all existing routes and citation links working.
3. Reject unsafe uploads before writing a database row or binary.
4. Keep extracted text unverified until an explicit human decision.
5. Do not make downstream knowledge depend on an original file that may disappear.
6. Use deterministic mock providers by default.
7. Make each implementation phase independently testable and reversible.

## 3. Consolidated Workspace Interface

The sidebar contains exactly six entries. It does not list all eleven pages at once.

| Sidebar entry | Default route | Context tabs shown in the content area |
| --- | --- | --- |
| Dashboard | `/` | Dashboard |
| Sources | `/source-inbox` | Inbox, Gmail, Files |
| Knowledge | `/knowledge` | Hub, Decisions, Verification |
| Actions | `/actions` | Action Center |
| Copilot | `/copilot` | Assistant, Email Drafts |
| Settings | `/integrations` | Connections, Privacy & Data |

Activating a sidebar entry navigates to its default route. When a route belongs to a group, the
group is highlighted and its context tabs are rendered above the page content. This makes related
functions feel like one workspace without deleting or renaming the existing routes.

On viewports below 768 px, the six entries move into a drawer opened from the top bar. Context tabs
remain visible as a horizontally scrollable row. The main content must not be squeezed beside a
fixed 256 px sidebar.

The navigation definition has one source of truth shared by sidebar state, context tabs, route
matching, and tests.

## 4. Route and Detail Design

The existing eleven routes remain unchanged. The router also adds these detail routes:

- `/knowledge/:knowledgeId`
- `/actions/:actionId`
- `/decisions/:decisionId`
- `/emails/:emailId`
- `/calendar/:calendarLinkId`
- `/documents/:documentId`
- `/verification`

Each detail route reuses its existing group page and opens the selected record in a detail panel or
drawer. A missing or cross-organization identifier displays a local not-found message inside the
normal App Shell. It does not replace the entire application with a blank error page.

`/knowledge?business_entity_id=:id` initializes the Knowledge Hub filter from the URL. Internal
Copilot citations use React Router links so navigation does not reload the application.

Single-record API endpoints are added where they are currently absent. Detail routes must not load
one paginated list and search that page for the requested identifier.

## 5. Upload Safety Design

`UploadValidator` runs before storage and before a `DocumentAsset` row is created. It determines an
effective type from the declared MIME type, extension, and leading bytes.

The validator uses a closed allow-list and the limits defined in Requirement 3:

- text and documents: 20 MB
- images: 10 MB
- audio: 25 MB
- empty payloads: rejected

Unsupported types return `415`, oversized payloads return `413`, and empty payloads return `400`.
A rejected upload leaves no asset, chunk, audit row, or stored binary.

Text parsing uses an exact validated-type dispatch table. It accepts strict UTF-8 and UTF-16 only
when valid, rejects replacement characters, and removes the current `latin-1` and replacement
fallbacks that can create mojibake.

Processing failures must persist `FAILED` plus a human-readable `failure_reason`. The route must
not raise in a way that causes the request transaction to roll back the failed status.

## 6. Media Data Model

The first capture migration adds:

### `DocumentAsset` additions

- `failure_reason: TEXT NULL`
- `original_binary_available: BOOLEAN NOT NULL DEFAULT TRUE`
- processing state `AWAITING_REVIEW`

The existing `mime_type` stores the validated effective MIME type.

### `ExtractedTextSuggestion`

- `id`
- `organization_id`
- `media_asset_id` with a unique cascading foreign key to `document_assets`
- `status` using `SuggestionStatus`
- `extraction_method` (`OCR` or `ASR`)
- `machine_text`
- `text` containing the current reviewable version
- `human_edited`
- optional confidence constrained to `0.0..1.0`
- optional detected language
- optional `reviewed_by` and `reviewed_at`
- optional unique `source_item_id`
- timestamps

### `MediaRetentionPolicy`

- one row per organization and user
- `RETAIN_ORIGINAL` by default
- optional `DISCARD_AFTER_EXTRACTION`
- timestamps

## 7. Extraction Lifecycle

Image and audio assets follow this state machine:

```text
UPLOADED
   |
   v
PARSING ----failure----> FAILED ----retry----> PARSING
   |
   v
AWAITING_REVIEW
   | confirm                         | reject
   v                                 v
CHUNKING -> EMBEDDING -> INDEXED     remains unindexed
```

Upload and processing remain separate backend operations. The frontend automatically calls the
processing endpoint after an upload succeeds.

One asset can have at most one provider invocation in flight and at most one extracted-text
suggestion. A repeated process or confirm request returns existing state without duplicate provider
calls, source items, chunks, or audit rows.

The original machine text is immutable. A human may edit the reviewable text before confirmation.
Confirmation creates the Source Item and searchable chunks. Rejection creates no chunks.

## 8. OCR and ASR Providers

`OCRProvider` and `ASRProvider` follow the existing provider pattern:

- protocol interface
- deterministic mock implementation
- real implementation selected by configuration
- mock selected by default
- explicit construction error when a real provider lacks credentials
- configured per-call timeout
- production failure returns a retriable `503`
- development may fall back to the mock and records the fallback

The document service depends on the protocols, not on a specific vendor. Provider credentials are
never serialized.

## 9. Media Review Interface

The Files tab accepts text documents, images, and audio. Each asset card shows:

- filename and media family
- processing status and visible progress
- failure reason and Retry when allowed
- extraction method and confidence
- original-file availability
- extracted text editor while awaiting review
- Confirm and Reject actions

When the original binary is unavailable, the card keeps derived text visible and disables download
and re-process controls.

The Source Inbox uses one Capture Composer with two modes: **Write a note** and **Record voice**.
The voice mode manages browser capability, permission denial, missing device, recording duration,
stop, discard, the 300-second limit, short-recording rejection, upload progress, and retry while
retaining the Blob in the browser.

## 10. Verification Queue

The Knowledge group contains a Verification page with two tabs:

- **Needs review**: normalized `SUGGESTED` artifacts, oldest first
- **History**: normalized `CONFIRMED` and `REJECTED` artifacts

The queue returns a common entry shape: artifact type, identifier, normalized verification status,
creation time, summary, and application path. Clicking Review navigates to the existing review
surface; the queue does not create a second confirm/reject implementation.

The current domain models use different status concepts. The queue normalizes them without
changing their meaning:

- `ClassificationResult` and `KnowledgeItem`: use `SuggestionStatus` directly
- `ExtractedTextSuggestion`: use `SuggestionStatus` directly
- `EmailDraft`: map AI-suggested, approved/materialized, and rejected states
- `ActionItem`: expose persisted actions as confirmed; keep `ActionStatus` for work progress
- `DecisionRecord`: expose the immutable, already-recorded decision as confirmed
- `EmailTaskSuggestion`: include as `ACTION_SUGGESTION` so pending Gmail-derived actions are visible

This avoids adding a misleading verification column to action progress or immutable decisions.

## 11. Pagination

All organization-scoped list endpoints return:

```json
{
  "items": [],
  "limit": 25,
  "offset": 0,
  "total_count": 0,
  "has_more": false
}
```

The backend uses shared validated pagination parameters and a shared page builder. Filtering,
organization scoping, permission checks, and sensitivity predicates are applied before count and
offset/limit. Every query has a declared stable sort and identifier tie-break.

The frontend uses shared `PageEnvelope<T>`, `PaginationControls`, and `usePaginatedList`. A mutation
reloads the current page; an empty non-first page steps back once. Changing a filter resets the
offset to zero.

## 12. Privacy and Binary Loss

The Privacy & Data tab shows the media-retention choice beside raw-email retention. Changing the
policy affects subsequent successful extractions only.

Derived Postgres rows never depend on reading the original binary. If storage is ephemeral and the
binary disappears, metadata, extracted text, confirmed chunks, embeddings, provenance, and queue
entries continue to work.

## 13. Security and Audit

Every new route requires a session and uses the existing organization-scoped query helper.
Cross-organization identifiers return `404`. Original bytes and provider credentials are never
returned in JSON.

Each successful mutation that requires auditing writes exactly one AuditLog row in the same
transaction. Idempotent repeated calls write no duplicate audit row.

## 14. Test Strategy

Each phase has a focused gate before the next phase starts:

- frontend typecheck and navigation/unit tests
- targeted backend route/service tests
- property tests with at least 100 examples for P21-P31
- existing backend regression tests
- final production frontend build
- manual mobile, deep-link, and failure-state checks

No feature test uses a real paid OCR, ASR, embedding, or LLM call.

