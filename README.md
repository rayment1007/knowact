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
