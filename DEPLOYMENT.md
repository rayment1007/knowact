# Deploying KnowAct (Neon + GitHub + Render + Vercel)

The plan: **Neon** hosts PostgreSQL, **Render** runs the FastAPI backend,
**Vercel** serves the React SPA and proxies `/api/*` to Render.

## Updating an existing deployment

Keep the local backend and frontend running while reviewing changes. Before
releasing this Workspace version:

1. Run the frontend build and the affected backend/frontend tests. Review
   `git diff` and stage the application files deliberately; keep `.env`, local
   uploads, generated code dumps, and unrelated coursework out of the commit.
2. Include migrations through `0013_sync_preferences` in the
   release. Render's configured start command runs `alembic upgrade head`; do not
   rerun the initial seed against an existing database. A local migration against
   the same Neon database already changes that shared database, but does not
   deploy any backend code.
3. Decide how uploaded originals will persist before relying on the hosted app.
   The checked-in Render configuration uses the free plan and local storage.
   [Render free services lose local files on restart, redeploy, and spin-down](https://render.com/docs/free).
   Neon retains extracted text and database records, not the uploaded binaries.
   Local uploads are not transferred by a Git push. Durable originals require
   persistent storage (for example, a paid Render disk with `STORAGE_ROOT` set to
   its mount path), or an object-storage adapter, which is not implemented yet.
4. Deploy the matching backend and frontend revisions together. A Vercel preview
   still proxies to the Render host in `frontend/vercel.json`; a frontend preview
   alone does not provide a matching preview API. Confirm the connected branches
   and auto-deploy settings in both dashboards before pushing the release branch.
5. Verify both the Render and Vercel `/api/health` endpoints, then test real Google
   sign-in at the Vercel origin. Confirm production OAuth callback URLs and secure
   cookies; do not copy localhost OAuth settings into production.
6. Check sync timestamps, open an original source, generate/edit/approve one
   source draft, follow its evidence link, and ask Copilot about today's tasks.
   A build or health check alone does not verify those authenticated workflows.

### Sync and review release

- Settings stores the user's sync window: Gmail defaults to the past 7 days;
  Calendar defaults to the past 7 and next 90 days. Login and Sync Now use it.
  Narrowing the window does not delete previously saved records.
- Removing imported email or calendar sources only removes the KnowAct copy.
  The original stays in Google and may return on a later sync within the saved
  range. Legacy email exclusion markers are no longer applied.
- Manual sources and project groups have deletion controls. Removing a group
  unlinks its records rather than deleting knowledge or actions.
- Approved Calendar writes and explicitly sent Gmail drafts are matched by
  provider ID, so later imports do not create a second review requirement.
- Keep `TOKEN_ENCRYPTION_KEY` consistent across environments sharing the same
  database. A different key cannot decrypt existing Google connection tokens;
  reconnecting from that environment would replace tokens used by the other one.
- The existing owner email allowlist remains enabled. This release does not
  open public registration or change Google consent permissions.

## Why the Vercel proxy matters

The browser only ever talks to your Vercel domain. Vercel forwards `/api/*` to
Render server-side, so from the browser's point of view the API is same-origin.
That means:

- the HTTP-only auth cookie stays same-site, so `SameSite=lax` keeps working and
  no third-party-cookie blocking applies;
- there is no CORS preflight to configure;
- Google OAuth callbacks point at one domain (Vercel), not two.

The alternative — calling Render directly from the browser — needs
`SameSite=none`, full CORS, and breaks in browsers that block third-party
cookies. Do not do that.

---

## Step 1 — Neon (database)

1. Create a project at [neon.tech](https://neon.tech). Pick a region near you.
2. Copy the connection string. It looks like
   `postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`.
3. **Rewrite the scheme** to `postgresql+psycopg://`. Everything else stays.
   Keep `?sslmode=require`.

You now have your production `DATABASE_URL`. Keep it out of Git.

### Apply migrations and seed from your own machine

Simplest path: point your local backend at Neon once.

```powershell
cd backend
# Temporarily set DATABASE_URL to the Neon URL for this shell only
$env:DATABASE_URL = "postgresql+psycopg://USER:PASS@HOST/DB?sslmode=require"
$env:SEED_USER_PASSWORD = "<the password you want for the demo users>"
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m app.seed
```

Migration `0007` runs `CREATE EXTENSION IF NOT EXISTS vector`. Neon supports
pgvector, so this succeeds. Verify:

```powershell
.venv\Scripts\python -c "import os,sqlalchemy as sa; e=sa.create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(sa.text('select extversion from pg_extension where extname=''vector''')).scalar())"
```

Close that shell afterwards so the variables do not linger.

---

## Step 2 — GitHub (repository)

Decide **private or public** first. If public, double-check that no `.env` is
tracked — `.gitignore` already excludes `backend/.env`, `frontend/.env`, and
`.env`, but verify before the first push.

```powershell
cd <path-to>\KnowAct
git init
git add -A
git status                      # confirm no .env file is staged
git commit -m "Initial commit: KnowAct"
git branch -M main
git remote add origin https://github.com/<you>/knowact.git
git push -u origin main
```

If `git status` lists any `.env`, stop and remove it from the index
(`git rm --cached backend/.env`) before committing.

---

## Step 3 — Render (backend)

1. New → **Web Service** → connect the GitHub repo.
2. Render reads `render.yaml` from the repo root. If you create the service
   manually instead, use:
   - Root directory: `backend`
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command:
     `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/api/health`
3. In **Environment**, set the secrets that `render.yaml` marks `sync: false`:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Neon URL with the `postgresql+psycopg://` scheme |
   | `AUTH_ALLOWED_EMAILS` | a JSON array containing exactly your owner email, e.g. `["you@example.com"]` |
   | `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
   | `LLM_API_KEY` | your OpenAI key |
   | `OPENAI_API_KEY` | the same OpenAI key (used for embeddings) |
   | `GOOGLE_OAUTH_CLIENT_ID` | from Google Cloud |
   | `GOOGLE_OAUTH_CLIENT_SECRET` | from Google Cloud |
   | `GOOGLE_OAUTH_REDIRECT_BASE` | your Vercel domain (fill in after step 4) |
   | `FRONTEND_BASE_URL` | your Vercel domain (fill in after step 4) |

   Set `AUTH_ALLOWED_EMAILS` before the first production boot; KnowAct fails
   closed when it is missing, empty, or contains more than one address. Use a
   **fresh** `JWT_SECRET` and `TOKEN_ENCRYPTION_KEY` for production — do not
   reuse the ones in your local `.env`.

4. Deploy. Note the service URL, e.g. `https://knowact-api.onrender.com`.
   Check `https://<render-url>/api/health` returns `{"status":"ok"}`.

---

## Step 4 — Vercel (frontend)

1. New Project → import the same repo.
2. Set **Root Directory** to `frontend`. Framework preset: Vite.
   Build command `npm run build`, output directory `dist`.
3. Leave `VITE_API_BASE_URL` unset. The client defaults to `/api`, which is what
   the rewrite expects.
4. Open `frontend/vercel.json` and replace the placeholder Render host with your
   real one:

   ```json
   { "source": "/api/(.*)", "destination": "https://<your-render-host>/api/$1" }
   ```

   Commit and push — Vercel redeploys automatically.
5. Note your Vercel domain, e.g. `https://knowact.vercel.app`.

### Close the loop

Go back to Render and set both `GOOGLE_OAUTH_REDIRECT_BASE` and
`FRONTEND_BASE_URL` to the Vercel domain, then redeploy.

---

## Step 5 — Google Cloud OAuth

In **APIs & Services → Credentials → your OAuth 2.0 Client**, add these
Authorized redirect URIs (in addition to the localhost ones you already have):

```
https://<your-vercel-domain>/api/auth/google/callback
https://<your-vercel-domain>/api/integrations/callback
```

Note the second path is `/api/integrations/callback` — not
`/api/integrations/google/callback`.

Confirm on the OAuth consent screen:

- Publishing status **Testing** is fine; add the same Gmail address configured
  in `AUTH_ALLOWED_EMAILS` under **Test users**.
- Enabled APIs: **Gmail API**, **Google Calendar API**.
- Scopes: `openid`, `email`, `profile`, `gmail.readonly`, `gmail.compose`,
  `calendar.events`, `calendar.calendarlist.readonly`.

---

## Step 6 — Verify

1. Open the Vercel URL and sign in with the owner account configured in
   `AUTH_ALLOWED_EMAILS`.
2. Integrations → connect Gmail, then connect Calendar. Both should return to the
   SPA with a success banner.
3. Gmail Sync → run a sync. Messages appear.
4. Copilot → ask a question. The answer should carry citations.
5. Actions → Gmail Drafts → create and review a draft. Sending email is not needed
   to verify the phase-one draft workflow.

---

## Known limitations

**Render free tier sleeps.** After ~15 minutes idle the service spins down and
the next request takes 30–60 seconds. The first page load after a pause will feel
slow. This is expected on the free plan.

**Uploaded files are ephemeral on Render.** `STORAGE_BACKEND=local` writes to the
container filesystem, which is wiped on restart. The *original* uploaded binaries
are lost, but the extracted text chunks and their embeddings live in Neon, so
Copilot document Q&A keeps working. Re-upload a file if you need the original
back. Moving to S3-compatible object storage would fix this properly.

**Set an OpenAI spending cap.** A public URL means anyone who can sign in can burn
tokens. Add a hard monthly limit in your OpenAI billing settings.

**Rotate your keys.** If an API key or client secret has ever been pasted into a
chat, an email, or a screenshot, rotate it: OpenAI dashboard for the API key,
Google Cloud Credentials for the client secret.

**Google OAuth in Testing mode** only works for accounts listed under Test users,
and refresh tokens expire after 7 days. Publishing the app requires Google
verification.
