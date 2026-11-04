# Phase 2 fact-memory foundation

Date: 2026-11-03 (Asia/Kuala_Lumpur). This is a local development foundation,
not a deployed Conflict Detection feature. The M1 browser workflow still needs
selected-email extraction in Phase 3 and comparison/review UI in Phase 4.

## Implemented storage and review boundary

Migration `0015_structured_fact_memory` follows `0014_personal_workspace_owners`.
It adds seven initially empty tables:

| Table | Responsibility |
| --- | --- |
| FactIdentity | Immutable owner/project/type/key/occurrence identity |
| FactSourceSnapshot | Canonical local and stable origin identity, full-content fingerprint, source time and availability; no full source body |
| FactEvidence | Exact selected excerpt, span/chunk locator, redaction marker |
| FactProposal | Editable date candidate, revision, comparison state, expected accepted head |
| FactVersion | Immutable accepted value, version number, predecessor, Current/Superseded state |
| FactEvidenceLink | Approved evidence support with stable deduplication key |
| FactReview | Append-only human decision, exact reviewed payload revision and replay key |

Composite foreign keys enforce owner/org/project and same-identity version
links. A partial unique index prevents two Current versions. Database triggers
prevent rewriting accepted values, identity keys and review history. Explicit
one-way content redaction is separate from normal versioning.

Legacy KnowledgeItem remains narrative/general context, including older rows
named FACT. No migration converts a summary into a reviewed atomic fact.
Shared project/source provenance connects the two concepts; a Phase 3 extraction
bundle will present summary plus individually reviewed facts. There is no
cross-approval or second atomic current-value writer in the legacy routes.

`FactMemoryService` owns all fact writes. It never commits independently:

1. `propose` checks ownership, explicit PROJECT identity, complete date and exact
   quoted passage. It creates a pending candidate, not an accepted version.
2. `record_comparison` is an internal Python operation. It binds comparison to
   the current head and increments the proposal revision. HTTP clients cannot
   submit a comparison label or generate proposals through the development API.
3. `edit` saves a new proposal revision and an EDIT record, preserves prior
   payload values in review history, and invalidates the comparison.
4. `review` checks the displayed proposal revision, accepted head and source
   fingerprint. A changed/deleted/ineligible source blocks approval. Reject or
   Keep unresolved leaves the accepted value alone; deferring also invalidates
   an older tab's approval request.
5. Approval writes version, predecessor/supersede, evidence link, review and
   content-free audit metadata in one transaction. A savepoint rolls all of
   these back if any stage fails. Identical request keys replay the original
   result; reusing a key with a different payload returns 409.

PostgreSQL uses a scoped source-origin advisory lock, then identity/proposal
row locks before reading the locked original. The no-existing-head case is
serialized too. Database constraints remain the final protection.

## What the controlled comparison does and does not establish

This phase supports complete date-only MEETING_TIME candidates. NEW requires no
accepted head; SAME requires the exact normalized accepted value. REVISION's
narrow development gate requires explicit `rescheduled/moved/changed from OLD
to NEW`, complete dates and a strictly later known source timestamp than every
supporting timestamp of the current head. Obvious proposed/negated/question
wording is refused. CONFLICT and UNCLEAR cannot be approved without further
resolution. A redacted head cannot silently be replaced or rolled back.

These are controlled transaction tests, **not** an automatic semantic detector
or an evaluation score. Date presence in a quote does not prove that it refers
to the selected fact. Identity matching, assertion modality, quoted old text,
effective intervals, multiple facts and semantic ambiguity still need Phase
3–4 extraction/comparison. The development API is disabled by default and
unconditionally unavailable in PRODUCTION.

An event date is its value, not the validity interval of the scheduling claim.
No missing year or timezone is invented. Calendar/file source-authored times
remain unknown where existing storage cannot establish them. The fuller five
fact-type/source/relationship matrix is not implemented by these tables alone.

## Evidence and deletion

See [content lifecycle](PHASE2_CONTENT_LIFECYCLE.md) for the audited content
stores and remaining D3 work. Source readers cover email, calendar, document
chunks and manual sources without an AI/provider call. Full-content fingerprints
detect changes beyond the legacy 24,000-character generation limit. Gmail raw
source aliases and reconnect/reimport records resolve to stable owner/account/
message origins; local IDs are not the evidence deduplication identity.

Ordinary local removal marks fact snapshots unavailable in the removal
transaction; approval cannot use the missing original. Existing approved
excerpts remain. Reimport creates a fresh local source and requires a fresh,
explicit user-selected proposal/review. SAME support is deduplicated without
creating another fact version or reviving an old proposal.

`purge_fact_origin` is an internal fact-layer primitive only. It redacts all
same-origin snapshot titles, excerpts/locators, proposal values and review
payload copies. A fact value supported only by that origin is redacted while
its version chain remains. Another approved, unpurged origin may preserve the
same value; reimported copies of the same message do not count as independent.

**D3 is not yet end-to-end complete.** Ordinary removal still retains some
unapproved fact snapshots and legacy approved payload copies. Phase 6 must
coordinate minimization, raw/file erasure, summaries/actions/AI logs, embeddings,
browser caches, content-bearing identity metadata and recovery receipts. No
public Purge button or claim of whole-workspace erasure is introduced here.
The internal primitive must never be exposed as that complete operation.

## Migration and verification procedure

No Neon or deployment database was migrated in this phase. Before any later
rollout: verify the selected environment and current Alembic revision, retain a
recoverable backup, apply 0014 then 0015 to an isolated staging copy, inspect
owner/provenance counts, and verify the application with the flag off first.
The new code references these tables even when development routes are disabled;
the flag is not a substitute for schema migration.

The command for that later, explicitly selected database is the existing
`python -m alembic upgrade head` from backend. Do not run it against an implicit
production `.env` just to execute tests. The inventory tool is documented in
[development API and inventory](PHASE2_DEVELOPMENT_API.md); it requires an
explicit URL and uses a read-only PostgreSQL transaction, not automatic backfill.

Downgrading 0015 drops the new fact tables and their history. It is therefore
appropriate only for empty/disposable deployments or a deliberate backed-up
rollback, not as a routine undo after real approvals. It leaves legacy records
in place. Tests cover isolated 0015 upgrade/downgrade/re-upgrade, not the entire
historical migration chain or the production schema.

Tests use SQLite for fast behavior checks and a separate PostgreSQL 18 cluster
on `127.0.0.1:55439` with synthetic, randomly named schemas. PostgreSQL verifies
migration constraints/triggers, competing NEW approvals, competing REVISION
approvals, identical concurrent retries and injected audit-failure rollback.
Source adapters, cross-owner denial, stale source/head/revision, edit/reject/
defer, same evidence/reimport, deletion hooks and fact-layer purge are covered.
No live OpenAI calls or Google writes are part of these tests. Exact final test
counts and the next task are recorded in the main implementation checkpoint.
