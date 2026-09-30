# KnowAct

KnowAct turns the noise in a work inbox into a small set of **confirmed** facts,
tasks, and decisions — and then lets you ask questions against only that
confirmed material.

The core idea is a hard line between *suggested* and *confirmed*. Every AI output
starts as a suggestion that a human must accept, edit, or reject. Nothing the AI
produces is treated as fact, used as grounding for a later answer, or sent
anywhere until a person confirms it. That single rule is what makes the answers
auditable: every claim can be traced back to a specific confirmed record.

## What it does

**Capture and triage.** Emails, notes, and documents land in a Source Inbox. The
AI classifies each item on three independent axes — relevance, business category,
and sensitivity — with a confidence score and the text spans it used as evidence.
You confirm or override.

**Confirmed knowledge.** Accepted items become knowledge records with a summary,
key points, and the evidence text they came from, optionally linked to a business
entity (a project, process, client, vendor). Rejected suggestions are kept as
negative signal, not deleted.

**Actions and decisions.** Confirmed knowledge can be promoted into action items
(which can be pushed to Google Calendar) and decision records that capture what
was decided, why, and on what evidence.

**Grounded Copilot.** Ask a question and get an answer built only from confirmed
knowledge, open actions, decisions, synced email, client entities, calendar
events, and uploaded documents. Every sentence carries a citation the backend
validates before the answer is returned. If the evidence is not there, it says so
instead of guessing.

**Connected workspace.** Google sign-in, Gmail sync with content-hash
deduplication, Calendar event creation from confirmed actions, and document
upload with vector retrieval over extracted chunks.

**AI-assisted Gmail drafts.** Draft a reply grounded in confirmed facts, edit it,
then send. Sending is idempotent under an atomic compare-and-set, so a retry can
never send twice.

**Privacy controls.** Configurable raw-email retention, scoped deletion, and a
provenance log recording exactly which records backed each answer.

Every AI-facing surface is framed as operational support only and refuses to give
financial, legal, tax, insurance, medical, or investment advice.

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.13, FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 + pgvector |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Auth | JWT in an HTTP-only cookie; Google OIDC sign-in |
| AI | OpenAI chat models with strict structured outputs; `text-embedding-3-small` |
| Secrets | OAuth tokens encrypted at rest with Fernet |
| Tests | pytest + Hypothesis (property-based) |

## Architecture

```
frontend/          React SPA. Calls /api, which Vite proxies locally and Vercel
                   rewrites in production, so the browser sees one origin.
backend/
  app/core/        Core Engine: models, schemas, routers, services
                   (ingestion, classification, knowledge, actions, decisions,
                   briefs, audit, AI provider).
  app/modules/cwi/ Connected Workspace Intelligence: Google OAuth, Gmail,
                   Calendar, documents + retrieval, Copilot, email drafts,
                   privacy.
  alembic/         Migrations 0001 -> 0009.
  tests/           307 tests, including the property-based correctness suite.
```

Every domain table carries `organization_id`, and every query goes through a
scoping helper. A cross-tenant read or write returns `404`, never `403`, so one
tenant can never even confirm that another's record exists.

## Run it locally

You need Python 3.13, Node 20+, and Docker Desktop.

```powershell
# 1. Database (PostgreSQL 16 with pgvector, on host port 5434)
docker compose up -d

# 2. Backend
cd backend
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # then fill in the values it asks for
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m app.seed
.venv\Scripts\python -m uvicorn app.main:app --reload

# 3. Frontend (separate terminal)
cd frontend
npm install
npm run dev                 # http://localhost:5173
```

Start order matters: database, then backend, then frontend. `--reload` watches
code but **not** `.env`, so restart uvicorn after changing environment values.

`SEED_USER_PASSWORD` has no default. The seeder refuses to run until you set it,
which is what keeps a shared password out of this repository. The seed creates
`alex@knowact.app` (ADMIN) and `sarah@knowact.app` (MEMBER) with that password.
For a deployed phase-one instance, set `AUTH_ALLOWED_EMAILS` to a JSON array
containing exactly one owner address. That same exact allowlist protects both
password login and Google Sign-In; an unlisted Google identity is rejected
before any user or organization is created.

Full first-time setup, including Google Cloud and OpenAI configuration, is in
[`SETUP_NEW_MACHINE.txt`](SETUP_NEW_MACHINE.txt). Deployment to Neon, Render, and
Vercel is in [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Tests

### Workspace interface refresh

The main navigation is Dashboard and Workspace. Workspace contains Sources,
Knowledge and Actions tabs. Source types (Emails, Calendar, Files, Notes) are
filters in one source list. All three tabs have visible sidebar status filters
with counts. The compact header contains workspace search, a sync-status popover
(Gmail and Calendar only), and a Settings icon. Account details and Log out sit
at the bottom of the main sidebar. Settings combines
Google connections and privacy controls. Copilot opens as a persistent floating
panel; Gmail drafts are available under Actions. Old Settings URLs redirect to
the combined page; Decision and Verification URLs redirect to Knowledge.

Workspace lists use server-side pagination (20 records per page), keyword
search, status filters and stable date ordering. Opening a row loads its detail
panel; on small screens the detail replaces the list until closed. `Add source`
opens a note/upload dialog. Existing source, email, file, knowledge and action
bookmarks redirect to the matching Workspace tab and selected record.

Red badges count pending classification, knowledge or action-suggestion reviews;
they are not unread-message indicators. Raw sources remain usable without
approval. Source details retain the original text and actual linked knowledge,
pending email action suggestions and accepted actions. Confirmed email action
links are resolved from the recorded confirmation transaction, never inferred
from similar titles. File text passages are also paginated.

`Add to Knowledge` and `Add to Actions` let the user choose a destination from
any readable note, email, processed file or captured Calendar event. This is a
separate entry point from existing automatic classification/suggestions. The AI
prepares a persisted, editable draft, not a formal knowledge/action record.
`Save draft` leaves it pending; `Approve & create` atomically saves the displayed
edits and creates confirmed knowledge or an open action with original evidence.
Pending drafts appear in the corresponding tab and review count. Discard closes
the draft without creating a record. Repeated approval does not create duplicates;
concurrent stale edits and changed original sources cannot be silently approved.
Source/result links preserve the actual origin, including files and Calendar;
deleted originals show an unavailable message while approved records remain.

Drafting uses the existing configured AI provider and fails visibly if it fails;
there is no silent mock fallback. An explicitly configured mock provider is
labelled as a demo in the UI. Highly sensitive sources require acknowledgement;
previously user-confirmed exclusions must be reclassified first. Files must be
processed before drafting. Inputs are limited to the first 24,000 characters of
readable source text, with a visible partial-analysis warning for longer sources.
AI-suggested dates require source support; the reviewer can edit the due date.
Approval only creates a local record and never sends email or changes Calendar.

Conflict comparison and persistent read/unread notifications are not included.

Connected Gmail and Google Calendar accounts sync on authenticated workspace
entry. Navigation within the workspace does not start another sync. `Sync Now`
retries manually. Google consent is still required once for each service; a
disconnected or expired account is not imported. Sync never sends an email or
creates, updates or deletes a provider Calendar event.

Workspace reads are cached in memory for the current browser session. Switching
pages reuses loaded data; successful local creates, edits, approvals and deletions
invalidate related queries and refresh affected dashboard panels. Unrelated
modules keep their data. Sync Now refreshes connected providers and captured
workspace data, including changes from another tab/device. Reloading the browser
starts a new cache; logout and authentication expiry clear it. Authentication,
OAuth nonces and generated AI briefs are never cached. Private source content is
not written to localStorage by this cache.

The dashboard includes a local date, a brief calculated from saved actions,
pending AI suggestions and captured event dates, and links to exact review
filters. Sources to review counts pending AI classifications, not raw uploads;
knowledge to review counts suggested knowledge. Actions are ordered by due date
(overdue first, undated last); no priority score is invented. The brief does not
call an AI provider. Recent activity displays the current user's recorded source,
action, knowledge-confirmation and document operations, not semantic changes or
conflict detection. Activity is paginated, and unavailable targets have no link.
New source additions and actual action edits are recorded from this version
onward; old edits are not backfilled.

Sync status displays the last successful timestamp to the second in the device's
time zone. A failed sync keeps that timestamp and explicitly shows the failure.
Upcoming events use captured primary-calendar dates; recurring instances are
not expanded. An unchanged calendar read no longer advances an event's update
time.

Copilot uses the browser's current UTC offset and the server clock. The suggested
"What needs my attention today?" and "Show my overdue tasks" questions query
saved task deadlines directly: overdue, due today, and undated tasks remain
separate. Calendar answers use captured event start/end times, not record creation
dates. Other questions receive the same clock and date metadata in their bounded
AI context. Historical email phrases such as "this Sunday" are not evidence that
a task is due today. Citations appear as numbered links instead of internal IDs;
general answers with no valid citations are refused. Calendar answers remain
limited to captured events; sync before asking about recent provider changes.

The Gmail collector retains its existing latest-100-message limit. Calendar
capture reads the primary calendar, follows all result pages and stores recurring
series without expanding every recurrence. Search covers captured email text,
parsed file chunks, raw notes, knowledge, actions, drafts and captured Calendar
events. It is case-insensitive keyword search with category filters and pagination;
it does not search unimported provider content or run an AI model.

Before running this version against an existing database, apply the migration:

```powershell
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
```

Migration `0011_calendar_sources` adds the table for captured Calendar events.
Migration `0012_source_proposals` adds editable drafts and approved provenance.
Restart the backend after applying migrations (or let the local reloader reload). Existing Google write/approval flows are
unchanged. Calendar reads use the documented
[Google Calendar events.list endpoint](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).

### Running checks

```powershell
cd backend
.venv\Scripts\python -m pytest -q     # 307 tests
```

```powershell
cd frontend
npm run typecheck
npm run build
```

The suite never makes a paid or real external call. Fake Gmail, Calendar, Google
OAuth, and OpenAI transports are injected, embeddings are deterministic, and
`conftest.py` forces the mock AI and embedding providers before the app is
imported. No test can send a real email.

### Property-based tests

Behaviour that must hold for *all* inputs is tested with Hypothesis rather than
hand-picked examples. Each property runs a minimum of 100 generated cases:

- confirmed-only grounding — unconfirmed records never reach an AI prompt
- human-in-the-loop — no AI output is applied without an explicit confirmation
- confidence bounds and evidence presence on every suggestion
- sensitivity and permission gates on retrieval
- organization isolation across services and over HTTP
- Gmail deduplication, calendar and email-send idempotency
- Copilot citation grounding — every citation resolves to a real confirmed record
- OAuth tokens are never serialized into any response

## Security notes

This repository is public, so nothing secret lives in it. Every credential is
read from the environment at runtime; ackend/.env is gitignored and there is
no committed key, token, or password anywhere in the tree.

A few things worth knowing if you deploy your own copy:

- **There is no public sign-up.** A production instance refuses to start unless
  `AUTH_ALLOWED_EMAILS` contains exactly one owner address. Both password login
  and Google Sign-In enforce it, and an unlisted Google account cannot create a
  user or workspace.
- **The auth token is an HTTP-only cookie.** Client JavaScript never reads it, so
  an XSS bug cannot exfiltrate a session. Set `AUTH_COOKIE_SECURE=true` in any
  deployed environment.
- **OAuth tokens are encrypted at rest** with Fernet under
  `TOKEN_ENCRYPTION_KEY`, and no response schema serializes a token column.
- **Set an OpenAI spending cap.** A public URL means anyone who obtains an
  account can spend tokens. A hard monthly limit in your OpenAI billing settings
  is the only real backstop.
- **Rotate anything that has leaked.** If a key or client secret has ever been in
  a chat, an email, or a screenshot, replace it before going public.
- Cross-tenant access returns `404`, never `403`, so one organization can
  never confirm that another's record exists.

## License

Coursework project. Not licensed for production use.
