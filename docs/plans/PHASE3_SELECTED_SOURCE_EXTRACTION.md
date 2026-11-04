# Phase 3 selected-source extraction: M1 delivery

Date: 2026-11-03 (Asia/Kuala_Lumpur). Baseline: `ad6ca1b` (Phase 1/2
foundations committed before this turn). This delivery connects extraction and
editable candidates. Phase 4 still owns identity resolution, comparison and
accepted-version review. No production migration or deployment was performed.

## Delivered scope

The existing Add to Knowledge flow offers a general summary, or an explicit
personal PROJECT selection for summary plus meeting-date candidates. Users can
create a project themselves in the selector; the AI never creates one. Sources
can remain unassigned when saving general context. Project names do not merge
identities and Unassigned never becomes a wildcard for comparison.

One generated bundle contains a narrative summary and zero or more date
candidates. Summary approval creates a CONTEXT KnowledgeItem linked to the
selected project and leaves all facts unconfirmed. It does not create a fact
version, an Action or a Google operation. Discarding the summary does not
silently reject the independent candidates.

The M1 atomic type is MEETING_TIME with a complete date. All four existing
source adapters can supply input: Gmail, Calendar, document chunks (including
parsed PDF/DOCX/TXT) and manual/pasted sources. A source without a supported
meeting date can still produce a useful general summary. Other fact types,
time-specific dates, people/set semantics and project-status scopes remain
outside this narrow delivery. Their absence is not evidence that the source
contains no useful knowledge.

## Provider and evidence contract

`prepare_fact_bundle(content, title, context)` returns a strict schema:

- summary, key_points, summary evidence_text;
- facts: fact_type, subject, occurrence, normalized value_date, verbatim
  original_value, modality, previous_value_date, change_cue, exact evidence_text,
  and DIRECT/QUOTED/FORWARDED/UNCLEAR source context.

Nulls are explicit. Missing fields, wrong types and unsupported fact types are
invalid provider output. A valid empty facts list is a normal result. API,
parsing and grounding failures produce a safe FAILED extraction receipt and a
503 response; they never masquerade as an empty successful analysis and never
fall back to a mock when the configured LLM fails.

The service checks bounded output sizes, exact source passages, complete dates
in the **original value itself**, previous-date evidence and verbatim change
cues. A year elsewhere in a paragraph cannot complete `next Friday` or `17 Sep`.
Known negation, questions, proposed wording, forwarded/quoted content and
unidentified recurring occurrences retain clarification reasons. Timed Calendar
events cannot be silently reduced to an all-day DATE fact. These conservative
checks are not a substitute for semantic entailment or a measured detector.

The OpenAI adapter preserves the current configured model/key and its existing
structured parsing helper. It follows [OpenAI's structured-output guidance](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat),
with local validation still required. Tests use fake providers/SDK responses;
schema compliance and passing control tests do not establish live model
accuracy. The deterministic mock is labelled as a demo provider in the UI.

## Identity and editing boundary

A generated subject is an AI suggestion. Grounded date assertions become
Phase 2 pending proposals with an immutable provisional identity such as
`candidate:<run UUID>:<index>`. They carry `identity_unverified=true` and cannot
be made New/Same/Revision-ready or approved through the development review API.
Ambiguous/incomplete values stay in the extraction result with clarification
reasons, rather than acquiring a fabricated date or identity.

Users can edit a materialized candidate's date and exact supporting passage.
Phase 2 saves a new pending revision and EDIT history; a new value must occur in
the quoted source. Editing marks evidence for revalidation. It cannot certify a
different value under an unrelated old quotation. A correction based on the
user's own knowledge needs a new explicit user source/assertion in the later
resolution flow.

**Phase 4 design constraint:** after a candidate has immutable EDIT/review
history, its identity cannot simply be reassigned to an existing accepted
identity. Resolve by creating a new, linked proposal under the verified identity
and preserving the original candidate/review history. Add an explicit lineage
link and stale-revision checks in Phase 4; do not rewrite that history or reuse
the original pending proposal as though its identity had always been verified.

## Coverage, cost and cache behavior

Extraction processes at most three sequential segments, each at most 8,000
characters, with paragraph/sentence boundary preference. The total input budget
is at most 24,000 characters. Boundary choices can make the actual coverage
smaller. The UI shows exact analyzed/total character counts and ranges, with a
partial-analysis warning whenever content remains. This is a bounded first
portion, not a claim that all pages of a long document were analyzed.

The full source fingerprint still covers omitted text, so changing a late
document chunk invalidates an old summary/fact approval. Document locators
retain the existing chunk/page/span metadata. Full fingerprint construction
still reads the saved source; large-document latency/memory costs need Phase 7
measurement. Segment boundaries can lose context; source accuracy requires
separate evaluation.

Cache keys include owner, org, explicit project, canonical local source,
stable origin, full fingerprint, extractor/schema version, provider and model.
Same-source repeated clicks reuse a completed bundle. Content-free request
receipts bind even cache-hit request IDs to their original operation. Identical
retries return the same result; a reused key with a different intent returns
409. A known failed receipt requires a new request ID to retry; the UI starts
one after a confirmed failed response and keeps the old ID for an unknown
network outcome.

Reimport can reuse validated model output from the same stable origin/content,
but creates fresh local evidence and proposals after explicit user selection.
It does not reactivate a removed original, approve a candidate or add an accepted
version. Explicit regeneration is an API option, bounded to three attempts per
owner/project/origin/content/extractor revision, including failed attempts and
reimports. Each attempt can make up to three provider calls; this bound limits
repeated clicks, not overall account usage. Sync itself never invokes extraction.

PostgreSQL acquires a request advisory lock, then an origin lock, rechecks source
eligibility, and validates the project. Generation avoids holding a raw-source
row lock during provider calls. Before publishing it locks/rereads the original
and checks the complete fingerprint again. A source changed or removed while
the provider was running produces a failed receipt with no partial summary or
facts. Summary, candidates and result links publish in one savepoint.

## Storage, purge and rollout

Migration `0016_fact_extraction_runs` follows 0015. It adds:

| Store | Purpose |
| --- | --- |
| FactExtractionRun | Scoped attempt/result, coverage, bounded validated output cache, safe error code, summary and candidate links |
| FactExtractionRequest | Content-free request key/hash and scoped result reference, including cache hits |
| FactProposal.extraction_context | Candidate modality/context, unverified identity and edit-revalidation markers |

Minimal source snapshots exist even for zero-fact/failed runs, so their origin
can be found for fact-layer redaction. That internal primitive now clears run
outputs/coverage and associated proposal context. A purged cache is not reusable.
No raw provider exception, credential or source body is copied into failed-run
logs. Project deletion returns a clear conflict when extraction history exists,
including a zero-fact run, pending an explicit archive/deletion policy.

Full D3 purge remains Phase 6 work: legacy SourceProposal payloads, approved
general knowledge, operations, files, AI logs and browser copies need the full
coordinator. In particular, summary approval and purge need a shared origin-lock
order before a SourceProposal row lock to prevent later publication of a purged
general-summary copy. The current internal fact-only primitive is not a public
whole-workspace erasure operation. No public Purge claim or button is added.

All 0014/0015/0016 migrations must be present before using this checkout with a
database. The new tables/column are referenced even with the feature flag off.
Fact generation/editing is default-off and DEVELOPMENT-only via
`FACT_MEMORY_ENABLED=true`; PRODUCTION capabilities stay disabled even if the
flag is enabled. Existing general-summary/action flows continue without fact
options. A staged rollout and live browser/provider checks are still required.

Downgrading 0016 drops extraction receipts/cache and proposal extraction metadata.
It is a disposable/staged migration test, not a lossless undo after real use.
Back up and inspect the target environment before any later production migration.
No test uses the application `.env` database by default.

## Acceptance evidence

Tests exercise strict provider schemas and failure propagation; selected-email
bundle generation; independent summary approval; exact quotes/year/precision;
clarification states; privacy/owner/project checks; cache receipts and bounded
regeneration; reimport; edits; long-source coverage and stale-tail rejection;
file and all-day Calendar inputs. Existing PDF/DOCX/TXT parser tests are part of
the full regression, not a new live-document/model accuracy experiment.

Actual isolated PostgreSQL tests cover 0016 upgrade/downgrade/re-upgrade,
composite constraints, receipt uniqueness, concurrent generation with one
provider call, conflicting request reuse and source mutation from a second
session. Frontend tests cover project selection/creation, candidate edits,
separate summary approval, error retries, redaction and cache invalidation.
The main task checkpoint records final counts and unperformed live checks.
