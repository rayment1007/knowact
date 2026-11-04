# Phase 1 owner migration and verification

Date: 2026-11-02. This is a local implementation record and a later release runbook. No production database, Google account or deployed service was changed.

## Schema and legacy ownership

Migration `0014_personal_workspace_owners` follows `0013_sync_preferences`. It adds nullable indexed `workspace_owner_id` foreign keys to BusinessEntity, KnowledgeItem and ActionItem. ActionItem.owner_id remains the task assignee; it is not proof of workspace ownership. SourceItem.created_by, DecisionRecord.decided_by, Brief.generated_for and AuditLog.actor_id already provide personal ownership.

Runtime Core services require an explicit authenticated owner. NULL workspace owners match no user. There is no fallback to the current user, organization administrator, sole organization member or task assignee. Requests check both organization and owner, including old Core routes and consumers that generate AI context.

Backfill considers these independent provenance signals:

| Record | Signals |
|---|---|
| Knowledge | Source creator, approved SourceProposal user, CONFIRM_KNOWLEDGE audit actor |
| Action | CREATE_ACTION audit actor, approved SourceProposal user, owned linked knowledge, CONFIRM_EMAIL_TASK audit actor through its recorded action_item_id |
| Project/entity | CREATE_BUSINESS_ENTITY audit actor and owners of linked knowledge, actions, decisions, email drafts and email task suggestions |

Every available signal must identify the same user in the record's organization. NULL, missing referenced provenance or conflicting users prevent automatic assignment. Records without evidence stay unassigned. The migration does not delete content, manufacture review events or convert summaries into structured facts.

Project ownership inferred from related rows is deliberately conservative. A historically shared project is quarantined instead of being silently assigned or duplicated. Recovery needs a reviewed owner mapping and a decision about conflicting related rows. Renaming must preserve a project's UUID; once Phase 2 facts reference a project, its removal must archive/protect that identity and preserve version history rather than reuse the existing detach-and-delete behavior.

## Verification performed

- The migration was executed using Alembic Operations on an isolated PostgreSQL 18 cluster bound to `127.0.0.1:55439`, with synthetic data in a transaction-local test schema. Upgrade, backfill, downgrade and re-upgrade passed. The schema/data transaction was rolled back afterwards.
- Synthetic pre/post counts remain **6 knowledge, 5 actions, 2 entities and 1 source**. Assigned: 1 knowledge, 3 actions, 1 entity. Quarantined: 5 knowledge, 2 actions, 1 entity. Cases include conflicting owner signals, foreign-organization users, missing source, draft-only proposals, unknown linked knowledge and assignee-only records.
- Offline SQL generation from 0013 to head succeeded, and Alembic reports one head: `0014_personal_workspace_owners`.
- This test covers the actual 0014 PostgreSQL DDL/backfill using the legacy columns it reads. It does **not** prove the full historical migration chain, production data quality, pgvector installation or concurrent fact approval. Those remain release/Phase 2 checks.
- Backend and UI regression results are recorded in the main task checkpoint. They use fake AI/Google transports, not live sends or model evaluation.

## Before a later production release

1. Take a recoverable database backup and record the current application revision and Alembic head. Stop writes during migration/backfill and rollback decisions.
2. On a separate restored test database, collect content-free counts before/after: total/owned/unassigned per organization for the three tables; count owner-to-user organization mismatches; inventory conflicting provenance IDs for a reviewed recovery list. Do not export source text, tokens or passwords into this report.
3. Run `alembic upgrade head` on that test copy using its own environment. Compare counts and test two users in the same organization, plus unassigned legacy rows. Expected: no total row loss, no owner mismatch, no cross-user result/context, unresolved rows hidden.
4. Review quarantined records explicitly. Do not add a blanket `UPDATE ... SET workspace_owner_id = current_user` or infer ownership from assignee. Any approved recovery should be a separately auditable mapping, tested before production.
5. Deploy schema and application as a coordinated release. The new application requires the new columns. The previous application must not continue serving traffic because its organization-only queries do not enforce this access boundary.
6. After release, verify login, selected-source drafts, owner-scoped search/brief/Copilot, local remove/sync and Gmail review with authorized test accounts. A GitHub push is not evidence of these checks.

## Rollback

Keep traffic stopped. Prefer restoring the backed-up schema/data and compatible application together. `alembic downgrade 0013_sync_preferences` removes the owner columns and their access boundary; it does not make the earlier organization-shared application suitable for multiple personal users. Never downgrade while leaving the new application running. Recovery of a quarantined record is a data-review task, not a reason to remove isolation.

## Reproducing the isolated migration test

Use a disposable local cluster with database `postgres`, user `knowact_test`, host `127.0.0.1`, port `55439`. The test refuses other destinations, and does not read DATABASE_URL or the project .env for its connection:

```powershell
cd backend
$env:KNOWACT_TEST_POSTGRES_URL = 'postgresql+psycopg://knowact_test@127.0.0.1:55439/postgres'
.venv\Scripts\python.exe -m pytest tests/test_personal_owner_migration.py -q --basetemp ..\.codex-build\migration-test
```

Without that opt-in variable, the PostgreSQL-specific test skips. Standard backend tests continue to use ephemeral SQLite, mock AI and fake providers. Never point this test variable at Neon.

## Phase boundary

Phase 1 adds ownership and legacy Knowledge terminal-state guards, preserves Summary/Action drafting, and fixes materialized Gmail edit/send consistency. It does not add FactIdentity/FactVersion, the comparison engine, conflict review UI, purge implementation or LLM-only evaluation. Exact proposal/source/head/version binding for structured facts belongs to M1. Full provider-side drift detection, revision-bound approvals and outbox reconciliation remain Phase 6; the local tests do not claim those problems are solved.
