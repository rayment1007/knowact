# Phase 4 M1: meeting-date conflict review

Updated: 2026-11-04 (Asia/Kuala_Lumpur).

Status: the narrow M1 workflow is implemented and locally tested. Full Phase 4, browser end-to-end verification, live AI quality evaluation and production rollout remain open. The feature is off by default and development-only.

## What the user can do

1. Open a selected source and choose **Add to Knowledge**.
2. Explicitly select/create a personal project and prepare summary + facts.
3. Save the general summary independently. Each complete meeting-date candidate has its own **Review fact** entry.
4. Select an existing meeting identity or explicitly name a new meeting and occurrence. A rescheduled date is a value, not the identity key.
5. Compare current/proposed dates, source passages, source times and the server's explanation.
6. Edit and compare again, approve the reviewed result, reject it, or keep it unresolved.
7. Inspect **Knowledge → Project facts → Current / History**. An approved replacement keeps V1 as Superseded and creates V2 as Current. Same adds reviewed supporting evidence without creating V2.

Sources, summaries and fact approvals remain distinct. Fact approval does not create an action, send email or change a Google Calendar event. Navigation uses the existing cache; it does not sync Google.

## Implemented comparison boundary

Supported values are complete `YYYY-MM-DD` dates for `MEETING_TIME`, with directly supported source passages and an explicitly resolved project/meeting identity. These are deterministic, conservative rules, not a general semantic contradiction detector.

| Result | M1 condition | Review behavior |
|---|---|---|
| NEW | Supported value; no accepted head or competing different pending value | Explicit approval creates the first Current |
| SAME | Same normalized date as the accepted head | Explicit approval links evidence; no new version |
| REVISION | Supported newer source explicitly replaces the current from-date with the proposed to-date | Explicit approval supersedes the old version |
| CONFLICT | Same resolved identity, incompatible date and reliable newer source, without a valid explicit replacement | User must give a resolution reason to accept the reviewed replacement, or reject/defer |
| UNCLEAR | Unknown/older/simultaneous source time, wrong replacement base or competing initial values | Cases with valid evidence may be manually resolved with a reason; unsupported evidence stays blocked |

Question/proposal/uncertain/negated/cancelled statements, quoted or forwarded content without trustworthy context, an unsupported date, a missing year, unsupported precision/effective interval and unresolved recurring occurrence cannot be promoted by entering a resolution reason. Evidence edits are revalidated against the enclosing source statement, so selecting only the date does not hide a cancellation or question.

Delayed `17 → 18` mail cannot silently replace an already accepted `20`. Date equality also does not give a new source automatic approval. Same-source contradictory candidates remain visible; rejecting/correcting the mistaken candidate permits re-comparison of the other.

Human matching remains a source of error: different names can create separate identities, and users can select the wrong meeting. Exact normalized keys prevent exact duplicates; reviewed aliases and semantic candidate suggestions are future work. English rule coverage is deliberately bounded and requires broader evaluation before rollout.

## Data, transactions and review history

- `0017_fact_resolution` follows `0016_fact_extraction_runs`. It adds `FactProposal.comparison_json`, `FactResolution` and `FactComparisonRequest`.
- A resolution links the original extraction candidate/revision to a new proposal. It never reparents the original identity or rewrites previous EDIT reviews. One active resolution per candidate is enforced in PostgreSQL.
- Rematching retires the previous association. An inactive child cannot be approved; editing the original invalidates the association. Once approved, that candidate cannot be rematched to reinterpret its history.
- Owner/project checks, request and source locks, row locks, unique constraints and immutable lineage protect the workflow. Double-click/retry receipts bind a request key to its intent.
- Approval recomputes the assessment and checks proposal revision, current head, complete source fingerprint and active lineage. Version/evidence/review/audit changes commit together.
- Versions and their evidence/reviews are paginated. Source removal marks the original unavailable; accepted evidence stays traceable under the agreed policy. The internal fact-layer purge clears comparison/extraction text and deactivates lineage. **This is not the full workspace D3 purge engine or UI.**
- Source candidate cards derive display value, passage, review status and revalidation warning from their active resolution. The original proposal id/revision/history remain intact.
- Existing extraction/confirm routes create general `CONTEXT`, not approved atomic versions. Existing historical KnowledgeItems are not silently migrated into factual truth.

Main code: `fact_resolution_service.py`, `fact_memory_service.py`, `fact_memory_views.py`, `fact_memory.py`, `FactReview.tsx`, `FactWorkspace.tsx`, `FactCandidates.tsx`.

## Development entry points

Requirements: authenticated user, `MODE=DEVELOPMENT`, `FACT_MEMORY_ENABLED=true`, and a verified database at migration head `0017_fact_resolution`. Production responds 404 even if the flag is set. Do not simply enable the flag against an old schema.

Schema changes 0014–0017 must be reviewed/applied to the intended database. Tests used only a dedicated local PostgreSQL cluster and disposable synthetic test schemas; no application `.env` URL was used for them. This phase has not applied migrations to Neon. A downgrade of 0017 removes its new resolution/comparison records; use the documented staging round-trip, not an unreviewed production downgrade.

Authenticated routes under `/api/dev/fact-memory`:

- `GET /counts`, `/memory`, `/identities`: scoped counts and filtered, paginated discovery.
- `GET /proposals/{id}`: comparison, current/proposed evidence, lineage and approval eligibility.
- `POST /proposals/{candidate}/resolve`: explicit identity choice or new canonical key.
- `PATCH /proposals/{id}` and `POST /proposals/{id}/compare`: edit then recompute; clients cannot submit a relationship classification.
- `POST /proposals/{id}/review`: revision/head/fingerprint-bound decision, optional required resolution reason.
- Identity history and version evidence/review routes: paginated read-only history.

UI: `/workspace/knowledge?memory=needs_review`, `memory=current`, `memory=history`; no additional global navigation tab. Dashboard/Reviews add actual fact counts without counting them again as general knowledge reviews. Stale responses preserve unsaved edits and offer reload/recompare.

## Repeatable M1 walkthrough

Use synthetic sources in an isolated development workspace. The HTTP workflow test supplies controlled email timestamps; a manually entered note has unknown authored time and may correctly need manual resolution instead of receiving REVISION.

1. Project: `M1 UAT Demo`; meeting: `UAT review`; occurrence: `release 1`.
2. Email authored/received 1 September 2026: `UAT review is on 17 September 2026.` Extract, resolve the new identity, inspect NEW and approve.
3. New email dated 2 September 2026: `UAT review was rescheduled from 17 September 2026 to 18 September 2026.` Match the same identity, inspect REVISION and both passages, approve.
4. Inspect history: 17 September is Superseded; 18 September is Current. Summary approval remains independent.
5. New email dated 3 September 2026: `UAT review is on 20 September 2026.` Match the identity. Inspect CONFLICT, choose Keep unresolved; 18 September stays Current.
6. Inspect Dashboard/Reviews and reopen the source: status/value/evidence match the independent review. Reloading an older approval page must be refused until compared against the changed state.

Do not change real Gmail/Calendar data for this demonstration. These dates are deliberate fixture dates, not dates to infer from the user's current clock.

## Verification record

- Full backend suite: **625 passed**, including local PostgreSQL migrations/constraints and concurrency tests; 14 existing library/test-key warnings, no failures.
- Phase 4 PostgreSQL-specific suite: **9 passed** (also included in the full run): 0017 upgrade/downgrade/re-upgrade, scope FKs, immutable/unique lineage, concurrent resolve/rematch/NEW approval, duplicate approval and request-key collisions.
- After the final Source display correction, extraction + HTTP workflow suite: **33 passed**. This overlaps the full suite and contains a subsequently added regression; do not add totals together.
- Full frontend suite: **113 passed**. Two subsequent display/count tests were added; the affected extraction/dashboard suites then passed **20 tests**, and `npm.cmd run build` passed again. These results overlap the full suite.
- Browser tooling returned no connected browser/native host, so no actual screenshot, visual layout acceptance or logged-in browser end-to-end claim is made.
- No live OpenAI, Google write, Neon migration, push, Render or Vercel deploy was performed. Fake-provider/synthetic tests establish behavior, not extraction accuracy, production latency or cost.

## Remaining work

1. Verify the real M1 UI in an isolated development workspace and record live selected-source extraction quality.
2. Continue P4-01–04: reviewed aliases, bounded semantic identity candidates, constrained LLM assistance, remaining fact types and richer temporal/set semantics with evaluation cases.
3. Phase 5 will connect Copilot/Search/Brief and derived artifacts to the accepted versions and unresolved warnings. **They do not yet consume this new FactVersion memory**, so answering a question is not a valid acceptance check for M1 today.
4. Phase 6 completes D3 and other report gaps. Phase 7 evaluates rule-only/optional LLM-only/hybrid fairly; Phase 8 handles production migration, deployment and report/diagram updates.
