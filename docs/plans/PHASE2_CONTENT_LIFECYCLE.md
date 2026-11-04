# Phase 2 content provenance and deletion contract

Status: implementation contract and repository audit, 2026-11-03. This document
describes the additive structured-fact foundation. It does **not** assert that a
whole-workspace permanent-purge endpoint, production migration, browser flow, or
provider-side deletion has been completed. The implementation checklist remains
in `KNOWACT_IMPLEMENTATION_TASKS.md`.

## Scope and accepted decisions

- M1 handles a selected source, one owner/project, and an explicit UAT meeting
  date replacement. Sync never starts extraction or approves facts.
- General Knowledge remains independent: saving a summary approves no atomic
  fact. Existing summaries are not backfilled as human-approved fact versions.
- D3 ordinary **Remove from KnowAct** deletes the local original and removes it
  from ordinary browsing. The UI must disclose retention of the minimum evidence
  needed for already approved facts. Those facts can show a retained excerpt and
  an unavailable-original marker; they cannot pretend the original still opens.
- D3 **Permanently delete / Purge** also removes associated content copies,
  excerpts, embeddings and AI context/log text. It preserves only content-free
  history sufficient to explain that evidence was removed. It cannot mean only
  hiding a row or clearing one foreign key.
- Both are local operations. Neither removes Gmail messages, drafts or Google
  Calendar events. Later explicit sync may import a Google original again.

## Current content inventory

All paths below are relative to the repository root. This is a code inspection,
not evidence of the contents of Neon, Render disks, provider logs or backups.

| Store | Content and ownership/provenance | Required treatment / current limitation |
| --- | --- | --- |
| `SourceItem` | Raw/extracted pasted, note or imported-email text, title; `organization_id`, `created_by`; local UUID | Ordinary removal deletes the row and classification children. Gmail has a separate provider identity. UUID alone cannot join a later reimport to old evidence. |
| `ClassificationResult` | Reasons and evidence spans; ownership inherited through `source_item_id` | Delete with source. Do not retain source quotes in rejected classification JSON after purge. |
| `EmailMessageRecord` | Subject, sender/recipients, labels, message/thread IDs, content hash; owner through `IntegrationConnection`; raw/extracted body through `source_item_id` | Current email removal deletes record/body/task suggestions. Ordinary raw-SourceItem removal instead detaches the email record. Purge must cover email metadata as well as body. |
| `CalendarSource` | Title, description, location, date/time strings, link; owner through connection; calendar/event IDs | Local source deletion is separate from external event deletion. Sync can update, replace or remove these snapshots. All paths require a fact-source availability hook. |
| `DocumentAsset` and storage object | Filename, checksum, processing failure, storage key; owner `uploaded_by`; binary under `{storage_root}/{org_id}/{key}` | Current delete removes asset, chunks and binary. Storage deletion is not a PostgreSQL rollback: purge needs explicit retry/receipt handling if either store fails. Do not treat a DB commit alone as successful file erasure. |
| `DocumentChunk` | Parsed text, chunk/page coordinates, flexible metadata and embedding; owner through asset | Delete text, metadata copies and vectors together. Metadata source references are optional, so they cannot be the only purge lineage mechanism. |
| `SourceProposal` | Full editable summary/action payload, source title, evidence quote, source fingerprint and result ID; `organization_id`, `user_id`, polymorphic source kind/local UUID | Existing deletion discards unapproved proposals but retains approved proposals. Their stored content is an additional copy; ordinary removal is not already a minimal-evidence implementation. Purge must reach these after the raw source is gone. |
| `EmailTaskSuggestion` | Title, description, entity-name suggestion and evidence; email/source FKs | Email deletion removes these. Raw-source deletion can detach source FK and leave suggestion linked to email. Preserve durable lineage before detaching. |
| `KnowledgeItem` | Summary, key points, evidence text; `workspace_owner_id`, source/entity links | Existing source deletion detaches source FK and retains confirmed memory. These are narrative records, not atomic versions. Permanent purge requires per-record provenance and treatment of copied text, not automatic retention of all confirmed content. |
| `ActionItem` | Title, description, evidence and due date; `workspace_owner_id`, optional knowledge/entity link | Actions can derive directly from SourceProposal or indirectly from knowledge; both routes must be traced. A nullable knowledge link alone is incomplete lineage. |
| `DecisionRecord` | Decision, title, rationale and evidence; `decided_by`, entity link | Existing record has no complete source manifest. Future purge must conservatively handle content derived from removed sources; do not infer an independent source from human approval. |
| `BusinessEntity` | Name, description, attributes; `workspace_owner_id` | Project identity must remain stable while referenced by facts. Treat user-authored project labels separately from source-derived descriptions/attributes; UUID is not the human-readable name. |
| `Brief` | Aggregated `content` JSON; `generated_for`, scope/scope reference | A brief may repeat facts from many origins without a full source manifest. Invalidate/regenerate or redact affected content on purge; leaving cached text violates purge. |
| `EmailDraft` | Subject/body, recipients, purpose, referenced-facts JSON, warnings; `user_id`, source/entity/connection links | Raw-source deletion detaches FK but keeps draft content. Purge requires source manifests for referenced facts and local draft snapshots. External Gmail draft/sent-message removal needs separate explicit authorization. |
| `CalendarEventLink` | Last-synced/pending payload snapshots, operation IDs; `user_id`, action and provider links | Contains repeated event/action content even if CalendarSource is deleted. Purge must handle snapshots without interpreting local erasure as cancellation of a Google event. |
| `CopilotAnswerLog` | Question, answer, citations JSON and serialized evidence JSON; `user_id`, `organization_id` | Stores content-bearing context and excerpts. Purge must redact/delete affected entries or reconstruct safe portions, not just hide citations. Future fact citations need version/evidence IDs in the manifest. |
| `AuditLog` | Action/type/target/time plus flexible detail JSON; `actor_id` | Some existing entries contain filenames, sender patterns or other human-readable content. New fact audit events use IDs/revision/count/status only; purge must inspect older content-bearing details too. |
| `IntegrationConnection`, sender signals, retention/sync preferences | Account identity, scopes, encrypted credentials; owner `user_id`; sender rules may contain addresses | Connection credentials are not evidence and must never enter fingerprints, snapshots or logs. Source purge does not revoke the account. A full account deletion policy is broader than source purge. |
| Browser API cache and component state | `frontend/src/api/cache.ts` uses an in-memory Map; source content is not intentionally persisted there to browser storage | Remove/purge must invalidate matching queries, open detail views and Copilot messages. Session-generation protection exists for sign-out; a successful server purge must also remove stale content still visible in the current session. |
| Provider request/response memory, diagnostics, backups | AI receives source excerpts; Google keeps originals; runtime and backup behavior is deployment dependent | Do not promise erasure outside controlled stores without verifying provider retention and backup policies. App database purge is not deletion of Google originals or historical backups. No live deployment inspection was performed for this audit. |

### Existing raw-retention terminology

`RawEmailRetentionMode.EXTRACTED_ONLY` explicitly allows extracted text. Gmail
ingestion still saves the extracted message body in `SourceItem`; changing the
policy currently flips `EmailMessageRecord.stored_raw` to false. This flag is
neither proof that all message-derived text was erased nor proof that readable
extracted text is unavailable. The fact adapter reads that owner-authorized
extracted text only; it reads no raw MIME or attachments. The user-facing policy
and future purge tests must distinguish these concepts.

## Source adapter contract implemented in Phase 2

`backend/app/core/services/fact_sources.py` exposes:

```python
read_fact_source(db, user, kind, source_id,
                 lock=True, acknowledge_sensitive=False) -> FactSourceRead
```

The result contains canonical `kind` and current local `source_id`, `origin_key`,
`fingerprint`, `title`, full extracted `text`, `source_time`, `locator`, and the
underlying `SourceItem` reference when one exists. It performs no AI request and
no write. The caller must not serialize/persist the whole dataclass as an audit
payload: full text is transient. Persist only deliberately selected excerpts and
the minimal locator/version metadata required for their provenance.

| Input | Stable origin within owner + organization | Revision / time / locator |
| --- | --- | --- |
| Gmail email | Normalized connected account email + Gmail message ID; not connection UUID, thread ID or new local record UUID | Content fingerprint covers full extracted body, subject, sender/recipients and received timestamp. Locator contains current email/source UUIDs and provider message ID. Received time is source-receipt time, not effective fact time. |
| Raw source linked to Gmail | Canonicalizes to that owner's Gmail record before locking | Avoids a second identity when the same email is accessed through the raw-source route. Re-read uses returned canonical kind/ID. |
| Calendar | Normalized connected account email + normalized calendar ID + event ID | Includes complete saved title/description/location/start/end. Primary calendar's account-address alias normalizes to `primary`. `source_time=None`: local ORM `updated_at` and event start are not provider-authored revision timestamps. |
| File | Asset UUID, scoped to owner and organization | Fingerprint includes checksum, filename, MIME type and **all** ordered chunk text/page metadata. Chunk UUIDs locate current content but do not cause a content revision by themselves. Upload time is not publication time; `source_time=None`. Re-uploading identical bytes is not silently assigned the same independent provenance. |
| Note/pasted source | SourceItem UUID, scoped to owner and organization | Complete title/body, source type and local received timestamp form a revision. `source_time=None`: the server-default receipt time is not a source-authored timestamp. No provider identity is invented. The `note` alias normalizes to the existing `source` route. |

Gmail/Calendar account email is the current compatibility key across reconnects.
Google OIDC `external_account_id` would also be suitable as a durable account key,
but silently switching key formats later would split existing origin history.
Version origin-key formats and migrate them explicitly if account-rename support
is introduced. File checksum is a content-integrity value, not proof that two
separate uploads are independent corroborating sources.

### Fingerprint and evidence invariants

1. Origin identifies the resource; fingerprint identifies its content revision.
   A new local UUID on reimport does not create new source evidence by itself.
2. Hash canonical UTF-8 JSON with deterministic key order. Preserve actual text
   whitespace; an exact excerpt uses character `[start, end)` in that snapshot.
   A normalized display quote is not an exact original-text locator.
3. Hash the complete selected source, not only the first 24,000 characters. A
   future AI input bound must report analyzed ranges separately. A change beyond
   an AI prefix still invalidates stale approval.
4. Ignore sync/update timestamps and classification status in content hashes.
   Recheck current classification, exclusion and sensitivity at approval;
   unchanged text does not override a new privacy restriction.
5. Keep source time, local capture time, fact-effective time and review time
   separate. Unknown source-authored time stays unknown. A later sync alone is
   never replacement evidence.
6. Quote existence proves textual provenance, not the truth of a proposed date
   or explicit supersession. Phase 3 comparison/Phase 4 review must still verify
   semantic support. No arbitrary edited value may inherit old evidence merely
   because its quote is a substring.
7. Identical reimports can be recognized without automatic extraction, review,
   version creation or evidence reactivation. Purged evidence stays purged until
   a new explicit user-reviewed workflow creates authorized evidence again.

## Transaction and deletion integration requirements

The adapter uses the owner-scoped `_source` helper. Its existing email lock order
is EmailMessageRecord then SourceItem. Other types lock their original row.
Approval must re-read inside its transaction, compare origin/fingerprint and the
expected proposal revision/current fact head, and reject missing/changed sources.
An historical excerpt is never a substitute for this live eligibility check.

Every local deletion path must mark the corresponding fact source snapshot
unavailable before deleting/detaching the local source. This includes raw source
deletion, Gmail privacy deletion, document deletion, Calendar source deletion,
Calendar sync removal and an approved external Calendar delete's local cleanup.
The hook must use explicit owner/org scope and both canonical email and raw-source
aliases where needed. Do not let a generic ORM cascade erase fact history.

The removal hook must not lock fact identities or proposals in the opposite order
to approval. A snapshot-availability update can participate in the same database
transaction without traversing back into identity locks. PostgreSQL tests must
cover deletion racing with proposal creation/approval, not just SQLite tests.
Some existing deletions select rows without `FOR UPDATE`; the new hook alone is
not proof that every legacy race is solved.

For ordinary removal, retain only approved evidence needed by retained versions.
Pending/unresolved/rejected draft excerpts and value snapshots are not approved
memory; cancel/invalidate and clear them unless a separately disclosed policy
justifies retention. A minimal Phase 2 availability hook may initially only block
further approval; that must be reported as a lifecycle foundation rather than
complete D3 minimization. Do not silently re-enable an old proposal after sync.

## Permanent-purge implementation boundary

Phase 2 may provide a tested, internal fact-layer redaction helper. It is not a
whole-workspace purge route. Before the Phase 6 UI can claim permanent erasure:

- Persist explicit origin/version/evidence manifests for each new derived
  content copy. Polymorphic local UUIDs and nullable source FKs alone are
  insufficient after removal. Include proposal payloads, review snapshots,
  summaries, Copilot context, drafts and operation payloads.
- Clear relevant excerpt, source title, value and payload text from proposal,
  evidence and review copies. Preserve only IDs, ordering, timestamps and
  redaction/status markers required for a content-free audit trail.
- A fact supported **only** by purged evidence cannot remain an active usable
  value just because it was approved earlier. Mark/redact it and omit it from
  current-memory retrieval. Do not silently roll the head back to an older fact.
- Independently supported facts can remain only where another approved,
  nonpurged evidence item supports the same value. An identical reimport, two
  chunks from one document, or AI paraphrase is not independent corroboration.
- Invalidate embeddings, retrieval indexes, saved brief/answer content and open
  browser caches. Block concurrent generation from publishing a pre-purge copy
  afterward; retries use purge generation/version checks and idempotency.
- Coordinate file deletion and database redaction with recoverable receipts;
  document backup/provider limits. Return an honest incomplete result when an
  owned store could not be cleared. Do not use a successful metadata update as
  evidence that every content copy disappeared.

## Focused verification checklist

- Owner/org mismatch returns 404 before reading text; highly sensitive content
  requires explicit acknowledgment; confirmed excluded sources cannot be used.
- Gmail source/email aliases have one origin. Reimport/reconnection preserves
  origin while local UUID changes. Different owners never share an origin key.
- Fingerprint detects title/content/time and late-document-chunk changes;
  ordinary local sync timestamps do not change it.
- Exact locator and excerpt match; unavailable source or stale revision rejects
  approval. A source change cannot be bypassed with an unchanged proposed date.
- Calendar/file unknown source-authored time remains None; all-day DATE values
  never acquire a timezone or timestamp through the adapter.
- Removal marks all affected fact snapshots unavailable and cannot resurrect
  pending evidence on reimport. Purge redaction reaches all fact-layer copies and
  preserves only allowed independent facts/content-free history.
- Full legacy-store purge coverage and production/runtime validation remain
  separate acceptance work. Do not label this contract itself a passing test.
