# Phase 2 development boundary

Date: 2026-11-03. This is a storage/review foundation, not the completed Conflict MVP.

The API prefix is `/api/dev/fact-memory`. It requires an authenticated personal workspace, `MODE=DEVELOPMENT` and `FACT_MEMORY_ENABLED=true`. The flag defaults to false. Production returns 404 even when the flag is enabled. The schema migrations must be applied to an isolated test database before enabling it.

Read endpoints provide paginated proposals, proposal details and minimal evidence, current versions, paginated version history, approved evidence links and review history. Reads are scoped by organization and workspace owner. A missing or another user's record returns the same 404.

`PATCH /proposals/{id}` accepts `expected_revision`, `value_date`, `excerpt`, and `request_key`. `POST /proposals/{id}/review` accepts `expected_revision`, explicitly supplied `expected_head_id` (null for New), `expected_source_fingerprint`, `decision`, and `request_key`. Decisions are APPROVE, REJECT or KEEP_UNRESOLVED. Unknown fields are rejected. Services validate the stored proposal and source again; a client cannot supply a comparison relationship. Phase 2 has no public proposal-generation or comparison-write endpoint. Controlled service fixtures exercise candidate creation until selected-source extraction is implemented in Phase 3.

A project with fact identities cannot be deleted through the existing project route; it returns 409 and leaves history intact. Project archival/reassignment requires a later explicit lifecycle design.

## Legacy inventory

`python -m app.core.fact_inventory` runs only when `KNOWACT_INVENTORY_DATABASE_URL` explicitly selects a database. The CLI does not load a URL from `.env` or fall back to the application's default. Use an isolated database or an approved read-only connection, after migration 0015. It starts a PostgreSQL read-only transaction and prints only aggregate counts. It does not print credentials, record identifiers, titles, source content or personal email addresses.

Counts cover total/owned/unresolved BusinessEntity, KnowledgeItem and ActionItem rows, plus owned records with direct same-owner provenance, an approved SourceProposal, or neither link. The two provenance counts can overlap; neither is a proof of semantic correctness. An action's assignee is not its workspace owner. Missing provenance does not mean a manually created record is invalid.

Migration 0014 remains responsible for deterministic ownership backfill. This inventory never assigns ownership, converts legacy summaries to atomic facts or fabricates approvals. Legacy general knowledge remains general knowledge. Every newly extracted atomic fact still requires its own review.

Synthetic inventory tests are local evidence only. No production inventory has been collected in this phase.
