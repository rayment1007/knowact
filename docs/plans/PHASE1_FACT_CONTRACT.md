# Phase 1 project fact contract

Date: 2026-11-02. Status: design contract for the upcoming fact-memory implementation; the tables and comparison engine below are not implemented by this document.

## First vertical milestone

M1 uses one owner, one explicitly selected project, one UAT meeting occurrence, one date-only fact type and a selected email. An existing date is first approved with its own source evidence. A new email explicitly reschedules that same meeting. The system proposes Revision; the user sees both dates and evidence; approval appends V2 and supersedes V1. Edit, Reject and unresolved review preserve V1. Approval does not create an Action, send email or change Calendar.

The M1 working set is intentionally small. Unsupported ambiguity fails closed; the broader fact/source matrix follows the demonstration. Exact identities come before semantic alias matching. No source synchronization starts this extraction automatically.

## General knowledge and structured facts

General knowledge is reviewed narrative context, such as concerns discussed by a project team. Existing KnowledgeItem can continue to hold it. Its confirmation says the user accepted that summary; it does not certify an unseen structured fact or provide a canonical current deadline/status.

Add to Knowledge may return a General Summary plus zero or more Structured Fact proposals. Each fact has its own identity, value, evidence and review state. A source with no supported atomic fact can still produce useful narrative context. Saving/approving one part does not approve the others.

Legacy confirmed summaries stay legacy/general context unless separately extracted and reviewed. Do not invent earlier fact versions or review events from their timestamps.

The legacy string `KnowledgeItem.knowledge_type = "FACT"` does not establish a FactIdentity or an atomic accepted version. SourceProposal summary approvals now write `CONTEXT`; existing FACT/MEMORY records remain narrative context. Phase 2 must route atomic proposals through its separate version-review service and must not expose them to the legacy summary-confirm endpoint.

## Ownership and project boundary

Fact identity is `(organization_id, workspace_owner_id, project_id, fact_type, canonical_fact_key)` with an occurrence discriminator when the subject repeats. A project belongs to that owner. Client-provided UUIDs must be checked, not trusted as authorization.

Phase 1 reuses BusinessEntity as the project container with explicit personal ownership. Its other entity types can remain general context; a fact project's type must be PROJECT. Its display name is not its immutable identity. Unassigned has no wildcard meaning. Sources can stay unassigned for raw search and general summary; fact comparison requires an explicit project or an unresolved project choice.

Project selection uses the authenticated owner's entity list. Client-supplied entity IDs are revalidated when used for Actions, Decisions, email drafts, Calendar context or Copilot. A future rename preserves the UUID and must never merge projects by name. When fact history is introduced, removing a referenced project must archive/protect it; the current general-context detach/delete route cannot be reused to erase fact identity. New project rename/archive UI is not part of Phase 1.

Canonical keys describe a stable subject, for example `uat_review` within an identified occurrence. Dates and values that may change are not the identity key. AI-produced aliases are suggestions, not proof of equality. Repeated weekly reviews and UAT sign-off cannot automatically merge into UAT review.

Cross-owner/project comparisons are forbidden even when text and values are identical. A service failure or an incomplete lookup is not evidence that a fact is New.

## Value contracts

The canonical internal fact types proposed for Phase 2 are below. These names are implementation vocabulary; the report's five conceptual types stay the same.

| Type | Value representation | Constraints |
|---|---|---|
| DEADLINE | Date or aware date-time with explicit precision | Preserve whether date-only, time zone or year is known. No invented midnight, year or zone. |
| MEETING_TIME | Date-only or aware start/end time; all-day and event occurrence metadata | M1 uses date-only. Event date is a value, not evidence valid-time. Do not merge recurring occurrences. |
| PROJECT_STATUS | Normalized state plus subject and original wording | Completed for API integration does not mean the whole project is completed. Keep negation and scope. |
| DECISION | Decision subject/key plus accepted choice or explicit text | A proposal/question is not an enacted decision. Unrelated decisions have different identities. |
| RESPONSIBLE_PERSON | Owner-reviewed person identity/label or explicitly defined set | Same names do not prove same people; multiple people need set semantics rather than competing singleton heads. No new contacts connector is required. |

M1 supports only MEETING_TIME with a complete unambiguous date, explicit identity and assertion/replacement evidence. Unsupported types/precision produce a visible pending/unsupported outcome, not an invented confident fact.

Normalization is deterministic and versioned. Keep original values alongside normalized representations. Different precision is not automatically equal. Date-only and time-specific assertions may be compatible but require a defined comparison rule. Case/spacing changes may normalize; meaning-changing transformations require evidence.

## Time and modality

- `source_time`: timestamp of the original statement/provider revision where available.
- `captured_at`: when KnowAct stored the source; it never substitutes for source/effective time.
- `effective_from/to`: source-supported applicability, optional. An event's scheduled date is not automatically the validity interval of the scheduling statement.
- `reviewed_at`: user decision time, separate from all of the above.
- Modality: ASSERTION, PROPOSED_CHANGE, QUESTION, UNCERTAIN. Model extraction of ASSERTION does not mean a user has confirmed it.

The current head identifies the latest accepted knowledge version, not a guarantee that a future-effective operational value applies today. Current-state retrieval checks applicability. M1 leaves future-effective operational changes unresolved until the fuller temporal rules exist. A past meeting date remains valid historical information; the clock alone does not reject or supersede it.

## Independent states and review outcomes

| Dimension | Meaning |
|---|---|
| Relationship | NEW, SAME, REVISION, CONFLICT, UNCLEAR; the proposed comparison result |
| Review | UNCONFIRMED, APPROVED, REJECTED; historical human acceptance |
| Version | CURRENT or SUPERSEDED for accepted versions; pending material stays a proposal |
| Processing | Pending/running/succeeded/failed for extraction/comparison infrastructure |

Database enum names may map existing SUGGESTED/CONFIRMED to these meanings. Do not confuse a mutable draft revision number with an immutable accepted fact version.

| Result/decision | Required behavior |
|---|---|
| NEW | No relevant accepted fact after a successful scoped lookup; save an unconfirmed candidate. |
| SAME | Reliable identity and compatible equal normalized value; link validated evidence idempotently; no replacement version or value change. |
| REVISION | Reliable identity, changed value and supported replacement of the relevant earlier value; current stays unchanged until approval. |
| CONFLICT | Same comparable subject/time with incompatible claims and no supported replacement; show both, never choose newest silently. |
| UNCLEAR | Identity, precision, time, scope or meaning cannot be established; retain uncertainty or ask the user to resolve it. |
| Edit | Save a new unconfirmed proposal revision, invalidate/recompute its comparison; preserve earlier edits in review history. |
| Approve | Approve the exact displayed resolved fact/value against expected proposal revision, source revision and current head. |
| Reject | Close the candidate without changing the accepted current value. |
| Keep unresolved | Preserve the last accepted value and show the unresolved warning. |

`rescheduled` alone is insufficient for Revision. A delayed old message that moves 17→18 cannot overwrite a subsequently accepted 20. A suggestion to move a date cannot establish that the date moved. Where the model cannot substantiate the claimed replacement, abstain.

All fact writers use the same review service. At most one current head exists for a single-valued identity. The value of an approved version is immutable. A single database transaction records predecessor/successor, head, evidence, review and audit. Stale or concurrent requests fail with a recoverable conflict; idempotent retries return the original result.

## Evidence and removal policy

Each fact links identifiable source passages, locators, stable provider identity, local revision and fingerprints. Quoted text must exist in the captured source, and the passage must support the claim; substring containment alone does not prove that support. User edits require rechecking the association. A user's independent correction needs an explicit user assertion/source rather than a false quote attribution.

User confirmed on 2026-11-02:

1. Remove from KnowAct removes the local raw source. Before removal, explain that minimal evidence excerpts already used by approved facts remain for their history. Mark the original source unavailable. This is not merely hiding the raw record.
2. Permanently Delete / Purge removes related source-content copies, evidence text, embeddings and AI logs. Affected history states evidence removed; it must not silently reproduce purged text from another context/cache.
3. Neither local action deletes Google email/events. Later Gmail/Calendar Sync may import a fresh local record. Reimport must not automatically rerun fact extraction or create another version.

Before Phase 2 schema work, enumerate all content-bearing stores and their lineage, including values/summaries and operation payload snapshots that copy the source. Define redaction/revalidation for solely source-derived material and preservation rules for genuinely independent evidence/user-authored content. Retain only permitted content-free history metadata after purge; never retain the original in a hidden audit JSON field. The Phase 2 UI must show these consequences before purge. The purge UI and engine are not implemented in Phase 1.

## Baselines and implementation inventory

Golden scenarios are in `backend/tests/fixtures/conflict_mvp_cases.json`. They define intended behavior; no conflict algorithm test is claimed to pass until an implementation runs against them.

For evaluation, compare rule-only with hybrid, plus LLM-only if budget allows. A controlled comparison-stage experiment uses identical frozen facts, candidate identities and source context for all methods; end-to-end extraction/matching quality is a separate result. Freeze holdout before tuning, record calls/cost/latency and report actual results even when hybrid does not improve them.

Existing write paths to integrate: core source extract/knowledge confirm, SourceProposal approval, Gmail action suggestion confirm, Copilot confirm and user-created Actions. Existing consumers to scope: workspace lists/counts/search/origins, legacy Core detail routes, active Dashboard counts, Copilot context, Email draft context, Calendar action/brief lookup and legacy AI Brief routes. Decisions remain a legacy protected endpoint, not a new navigation module.

| Path | Phase 1 behavior | M1 / later integration |
|---|---|---|
| Core source classify/extract | Owner scoped; extraction produces legacy suggested summary | Keep classification separate from atomic fact review; do not infer approval from reclassification |
| Core knowledge confirm/reject | Owner scoped, pending-only transition, terminal retries idempotent; empty evidence cannot be approved | General context only; atomic versions must use the fact review service |
| Workspace SourceProposal | Owner scoped draft/result, source fingerprint and draft-version checks; summary saved as CONTEXT | Return summary + separately reviewed atomic proposals |
| Gmail task confirm / Copilot confirm / manual action | Owner scoped action writer with validated project references | Remain independent from fact approval; stored artifact/revision binding and full action lifecycle hardening are Phase 5/6 |
| Workspace list/search/counts/origins/activity | Owner scoped Core records plus existing owner scoped connected sources | Add current/history/unresolved distinctions in Phase 5 |
| Legacy Brief, Decision and entity endpoints | Retained with personal access checks | No new Decision navigation; fact-version-aware context is Phase 5 |
| Copilot / Email draft context / Calendar action lookup | Only personal records can be selected | Bind exact fact versions and detect stale dependent previews in Phase 5/6 |

See `PHASE1_MIGRATION_RUNBOOK.md` for legacy ownership recovery, synthetic data counts and deployment boundaries. This design contract does not retrospectively certify the semantic accuracy of pre-existing AI summaries or historical answer text.
