# Job Application Tracker — Phase 8 Design: Production Deployment & CI/CD

**Date:** 2026-09-21
**Status:** Draft — awaiting approval
**Scope:** Takes the application from "runs on localhost only" to a genuinely deployed,
publicly reachable portfolio application, plus a GitHub Actions CI pipeline that gates
merges to `main` on backend tests, classifier regression checks, and frontend
type/lint/build. This is an **infrastructure and configuration phase**, not an
application-behavior phase: `app/`, `frontend/src/`, the classifier, the trust model,
and the sync/worker logic are unchanged except for one small, explicitly-scoped
additive change (§3.3, running the existing worker loop in-process). No paid AI/LLM
API is introduced. Existing local-dev behavior (documented in CLAUDE.md's "Local dev
environment" section) is fully preserved.

## 0. Why

Phases 1–7 built a complete, well-tested application — 281 backend tests, a
deterministic local classifier, a Postgres-backed sync job queue, a background worker,
and a review-queue UI — but it has only ever run on one developer's machine. There is
no GitHub remote, no CI, no Dockerfile, no production configuration of any kind, and
the `docker-compose.yml` at the repo root only runs a local Postgres for dev. For this
to function as a real portfolio piece (a reviewer visits a live URL, signs in with
Google, connects Gmail, and watches the pipeline work end-to-end) it needs to be
deployed, and the deployment needs the same engineering discipline the rest of the
project already has: real health checks, safe migrations, secrets that are never
committed, and a CI gate that a bad change can't quietly slip past.

## 1. Goal & Non-Goals

### Goal

Deploy the frontend, backend, worker, and database to genuinely free (or near-free)
hosting, with automated migrations, health checks, minimal logging/observability, a
GitHub Actions CI pipeline gating merges to `main`, and documented, verified
end-to-end behavior — while changing as little of the existing application as
possible.

### Non-Goals

- No paid LLM/AI API of any kind (the classifier is already local and free — this
  phase doesn't touch it).
- No horizontal scaling, load balancing, or multi-instance worker verification — this
  project's own Known Gaps already flag multi-worker correctness as unverified; Phase 8
  runs exactly one instance of everything, matching how it's always run.
- No paid error-tracking/observability service (e.g. Sentry) — Render's built-in log
  and metrics dashboard is sufficient at this scale; explicitly called out as an
  optional future enhancement, not part of this phase.
- No containerization — assessed in §10 and rejected as adding maintenance surface
  with no deployment benefit on the chosen platform.
- No rate limiting — a real but low-severity gap at this traffic scale, recorded as a
  known gap (§11) rather than fixed in this phase.
- No change to `classification_confidence_threshold`, the trust model, matching
  thresholds, or any classifier pattern/constant.
- No custom domain (per your answer — free platform subdomains only).

## 2. Current State — Production-Readiness Assessment

Verified directly against the repo, not assumed:

- **No GitHub remote** (`git remote -v` is empty) — blocks both GitHub Actions and
  every mainstream free-tier PaaS's git-based deploy flow.
- **No CI, no Dockerfiles, no deployment configuration** anywhere in the repo. The only
  container-related file is the root `docker-compose.yml`, which runs a local Postgres
  for dev only — no backend/frontend/worker service definitions.
- **`/health` exists but is trivial** (`app/main.py`): returns a static
  `{"status": "ok"}` with no database check, and there is no equivalent for the
  worker at all.
- **Cookie auth depends on an implicit same-origin trick.** `frontend/vite.config.ts`
  proxies `/api` to `http://localhost:8000`, so in dev the browser sees every API call
  as same-origin. No frontend fetch call (`src/api/*.ts`) sets
  `credentials: 'include'`, and the auth cookie is `samesite="lax"`
  (`app/auth/router.py`) — both only work *because* of the dev proxy's same-origin
  illusion. Deploying frontend and backend to two different origins without
  replicating this would silently break login.
- **OAuth redirect URIs are already environment-aware** — `request.url_for(...)`
  (`app/auth/router.py`, `app/gmail/router.py`) builds the callback URL from the
  incoming request, not a hardcoded value. This is good, but it means the URL's
  scheme depends on Starlette trusting proxy headers, which isn't configured yet.
- **Migrations are entirely manual today** (`uv run alembic upgrade head`, per
  CLAUDE.md's "Local dev environment" section) — Phase 5's own manual-testing findings
  already record a real incident where this was forgotten.
- **The worker is a separate, foreground-only process** (`python -m app.sync.worker`)
  with no host-level integration, health surface, or restart policy defined anywhere.
- **Local dev's weak `SECRET_KEY`** (12 bytes, confirmed via a `pytest`
  `InsecureKeyLengthWarning` seen during this investigation) must never be reused in
  production — a fresh one is generated in §4.

## 3. Architecture

### 3.1 Topology

```
┌──────────────────────────┐         ┌──────────────────────────────────┐
│ Render Static Site         │  /api/* │ Render Web Service                  │
│ (frontend, Vite build)      │────────▶│ (FastAPI, uvicorn)                   │
│ *.onrender.com, CDN-served,  │ rewrite │  + background worker thread          │
│ no spin-down                 │         │  (run_forever(), started at            │
└──────────────────────────┘         │   FastAPI startup — existing code,      │
                                       │   unmodified — see §3.3)                 │
                                       └───────────────┬──────────────────────────┘
                                                        │ DATABASE_URL
                                                        ▼
                                            ┌───────────────────────┐
                                            │ Neon (managed Postgres) │
                                            │ free tier, autosuspend  │
                                            └───────────────────────┘
```

Three pieces, all free: a Render Static Site (frontend), one Render free Web Service
(backend + worker), and a Neon free Postgres database.

### 3.2 Why this topology, not the alternatives

**Frontend hosting**: a Render Static Site with a rewrite rule (`/api/* →
<backend-url>/api/*`) rather than a separate origin with CORS + `credentials:
'include'` + `SameSite=None`. This exactly replicates what Vite's dev proxy already
does — the browser sees one origin, cookies keep working with `SameSite=Lax`
unchanged, and **zero frontend code changes** are needed (confirmed: Render's static
site rewrite rules are a supported first-class feature specifically for this
same-origin-proxy pattern, not a workaround).

**Database**: Neon, not Render's own free Postgres. Verified current terms (searched
2026-09-21, since hosting pricing changes fast): Render's free Postgres now expires
after **30 days** plus a 14-day grace period, then is deleted outright — a real trap
for a long-lived portfolio project. Neon's free tier has no such expiry, decouples the
database from whichever compute host is chosen, and auto-suspends/wakes cleanly on
idle — a good fit for a project with intermittent traffic.

**Worker**: run in-process inside the Render Web Service (per your answer), not as a
separate Render Background Worker ($7/mo minimum — Render has no free background-worker
tier, confirmed) and not as a Fly.io VM (Fly's free tier is gone for new accounts as of
2024 — confirmed; ~$2/mo pay-as-you-go now). Detailed in §3.3.

**Hosting platform choice**: Render, not Railway or Fly.io. Railway removed its
indefinite free tier (trial credit + paid Hobby plan). Fly.io has no free tier for new
accounts. Render is the only one of the three with a genuinely free, no-credit-card web
service and static site today.

### 3.3 Running the worker in-process

`app/sync/worker.py::run_forever()` is not modified. A new, small addition:

- A new startup hook (in `app/main.py`'s `lifespan`, or a new
  `app/sync/inprocess.py` module) starts `run_forever()` on a background
  `threading.Thread(daemon=True)` when a new env var, `RUN_WORKER_IN_PROCESS`, is
  `"true"`. Unset (the default), nothing changes — local dev keeps running the worker
  as today's separate `python -m app.sync.worker` process, exactly as documented in
  CLAUDE.md.
- Only set to `"true"` in the Render Web Service's environment.
- SQLAlchemy's synchronous engine/connection pool (already used throughout — this
  project uses sync `Session`, not `AsyncSession`, per `app/db/session.py`) is
  thread-safe for concurrent checkouts by design; running the worker's blocking loop
  in a genuine OS thread alongside Uvicorn's async event loop (itself running request
  handlers in a thread pool for sync code) is a standard, safe pattern — to be
  confirmed empirically at the implementation checkpoint (§13, checkpoint 6), not just
  asserted here.
- Trade-off, stated plainly: the worker only runs while the web service is awake. On
  Render's free tier the service spins down after 15 minutes idle and wakes in ~30-60s
  on the next request. For a portfolio project this is a feature, not a bug — no
  wasted background polling when nobody's visiting, and the worker resumes
  automatically the moment someone does.
- Bonus consequence: combining API and worker into one process means the worker no
  longer needs its own standalone-import-ordering safeguard for the `User` mapper
  registration bug (documented in CLAUDE.md's "Manual testing findings," still guarded
  by `tests/test_sync_worker_entrypoint.py` for the *local, separate-process* path,
  which is unchanged and still needs that guard).

## 4. Secrets & Environment Variables

All secrets live in Render's dashboard environment-variable store (encrypted at rest,
never in git) and Google Cloud Console — never in a committed file, never in a
`render.yaml` Blueprint's plaintext fields (Blueprint secret fields are marked
`sync: false`, requiring manual entry in the dashboard).

**Must be freshly generated for production, never copied from local `.env`:**
`SECRET_KEY` (JWT/session signing — the local dev value is a weak 12-byte key,
confirmed via a `pytest` warning; production needs a real
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` value) and
`GMAIL_TOKEN_ENCRYPTION_KEY` (Fernet key encrypting stored Gmail refresh tokens at
rest — generated the same way the README already documents for local dev, just a
distinct value).

**Backend environment variables in Render**: `DATABASE_URL` (Neon connection string),
`CORS_ORIGINS` (exact production frontend origin — never a wildcard, moot anyway since
`allow_credentials=True` makes browsers reject `*` regardless), `FRONTEND_URL`,
`ENV=production`, `COOKIE_SECURE=true`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`SECRET_KEY`, `GMAIL_TOKEN_ENCRYPTION_KEY`, and the new `RUN_WORKER_IN_PROCESS=true`.
`gmail_sync_backfill_days` and `sync_stale_job_threshold_minutes` keep their existing
defaults unless there's a specific reason to change them.

**Frontend needs zero environment variables.** It only ever calls relative
`/api/v1/...` paths (confirmed: every `src/api/*.ts` file defines `BASE` as a relative
path, no absolute URL anywhere) — a direct benefit of the rewrite-rule architecture:
there is no `VITE_API_URL` to keep in sync across environments, and nothing to get
wrong.

## 5. Google OAuth Reconfiguration

Same OAuth client used today, additive changes only (existing `localhost` entries stay
— local dev must keep working unchanged):

- **Authorized redirect URIs** — add
  `https://<backend>.onrender.com/api/v1/auth/google/callback` and
  `https://<backend>.onrender.com/api/v1/gmail/callback` (two distinct OAuth clients
  are registered today, `"google"` for login and `"google_gmail"` for the Gmail-scope
  connect flow — confirmed in `app/auth/oauth.py` / `app/gmail/oauth.py` — both share
  this one Google Cloud OAuth client's credentials, so both callback paths need
  registering).
- **Authorized JavaScript origins** — add `https://<frontend>.onrender.com`.
- **Consent screen stays in "Testing" mode.** `gmail.readonly` is a Google-designated
  *sensitive* scope; publishing "In production" would trigger Google's app
  verification process (privacy policy, scope justification, possibly a security
  assessment) — real overhead with no benefit for a project whose real users are the
  developer and a handful of reviewers. Staying in Testing (adding reviewers as test
  users, exactly as the developer's own account is added today) is the honest right
  call, not a workaround.
- **Proxy headers.** Render terminates TLS at its edge and forwards plain HTTP
  internally, so `request.url_for(...)` would otherwise build `http://` callback URLs
  that don't match what's registered above. Uvicorn needs
  `--proxy-headers --forwarded-allow-ips='*'` (or Render's specific trusted-proxy IP
  range, whichever the implementation checkpoint confirms is correct) so it trusts the
  `X-Forwarded-Proto` header.

## 6. Database Migrations During Deployment

Render's "Pre-Deploy Command" (confirmed current via Render's own changelog) runs
after a build succeeds and before the new instance starts serving traffic — set to
`cd backend && uv run alembic upgrade head`. This makes migrations automatic, ordered
correctly relative to new code, and impossible to forget (closing the exact gap
Phase 5's manual-testing findings already hit once locally). Confirmed failure
behavior: if the pre-deploy command (the migration) fails, the deploy is marked failed
and the previous instance keeps serving — the new, broken instance never receives
traffic. One asymmetry worth designing around: if the *migration* succeeds but the
*new application code* has a bug, redeploying the previous code image does **not**
undo the migration — the schema change persists. This is the standard reason
migrations should stay backward-compatible with the previous app version for at least
one deploy cycle (additive changes, not drop-and-recreate in the same step) — worth
stating as a house rule for this project's migrations going forward, not just a Phase 8
one-off.

Given this is a single-maintainer portfolio app, not a multi-instance production
system with live traffic during deploys, a straightforward "migrate, then deploy" is
appropriate — no expand/contract blue-green migration choreography is needed. Neon's
point-in-time restore serves as the safety net if a migration ever needs undoing.

## 7. Health / Readiness Checks

- **`/health`** (exists, unchanged) — liveness only, proves the process is up.
- **`/health/ready`** (new) — a trivial `SELECT 1` against the database, used both by
  Render's own health-check configuration and for manual verification.
- **`/health/worker`** (new) — the worker is now an in-process thread with no external
  visibility (no separate OS process to `ps` for). Track a module-level "last
  successful poll" timestamp, updated by the worker loop each tick, and fail this
  endpoint if it's gone stale beyond a threshold — reusing the exact staleness concept
  `sync/worker.py::reap_stale_jobs` already applies internally to jobs, just observed
  from outside for the worker itself.

## 8. Logging & Observability

Render captures stdout/stderr automatically and surfaces it in its dashboard Logs
tab — no new logging service is needed. The existing `logger.exception(...)` calls
throughout `sync/worker.py` and elsewhere already produce useful output; production
just needs `INFO`-level logging with timestamps (Uvicorn's default format already
includes them). Render's built-in metrics tab (CPU/memory/request count) requires no
setup. Explicitly **not** adding Sentry or similar — real value at larger scale, not
enough value here to justify another account/dependency; recorded as an optional
future enhancement, not part of this phase's scope.

## 9. GitHub Actions CI Pipeline

Verified directly against `backend/tests/conftest.py`: the test suite fully
self-bootstraps its own test database (creates `<db>_test` via a `CREATE DATABASE`
issued over an admin connection, then runs the *real* Alembic migrations against it
from scratch, not `Base.metadata.create_all`) — so CI needs nothing beyond a running
Postgres service container and the right env vars; there is no separate migration step
to script for tests, since every test run already exercises the full migration path
from empty to head.

Two parallel jobs on push/PR to `main`:

- **Backend**: a `postgres:16` service container (matching the version already used in
  `docker-compose.yml`), env vars set directly in the workflow (`DATABASE_URL` pointed
  at the service container, placeholder `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  exactly as the README already documents being sufficient for tests, a freshly
  generated `SECRET_KEY` and `GMAIL_TOKEN_ENCRYPTION_KEY` inline in the workflow — no
  real secret ever enters CI). `uv sync`, then `uv run pytest` — this already runs
  `tests/test_evaluation_accuracy.py`'s hard `MIN_*` regression bars as part of the
  normal suite, so the classifier regression gate this phase was asked to add is
  enforced here, not a separate mechanism. Also run `uv run python -m
  evaluation.compare` for visibility into the exact before/after numbers in the CI
  log — informational (the enforced gate is already the pytest bars), not a second,
  potentially-conflicting pass/fail check on the same data.
- **Frontend**: `npm ci` (a `package-lock.json` is committed, confirmed), `npx tsc -b`,
  `npx oxlint`, `npm run build`.

Both jobs are marked required status checks on `main` (a GitHub branch-protection
setting, configured once, not part of the workflow YAML). Render's own auto-deploy
watches `main` independently of GitHub Actions — the effective flow is **PR → CI must
pass → merge → Render auto-deploys from `main`**, without GitHub Actions needing to
trigger the deploy itself.

## 10. Docker/Containerization — Assessed and Rejected

Neither service has a system-level dependency that needs OS control: `psycopg[binary]`
bundles its own libpq (confirmed in `pyproject.toml`), no headless browser, no compiled
native extensions. Render's native Python and Node buildpacks already pin exact
versions via `uv.lock` and `package-lock.json`, matching what a Dockerfile's `pip
install` step would otherwise pin manually. A Dockerfile here would be two more files
to keep in sync with the real dependency manifests, for no deployment benefit Render's
native runtime doesn't already provide — exactly the kind of infrastructure added for
its own sake this phase was explicitly asked to avoid. The one place Docker carries
real, different value is *local-dev* environment parity (one `docker compose up`
bringing up db+api+worker+frontend together) — a legitimate but separate DX
improvement, out of scope for this production-deployment phase.

## 11. Security Concerns

- HTTPS everywhere — free automatic TLS on both Render services; no extra work beyond
  the platform choice itself.
- `COOKIE_SECURE=true` and exact (non-wildcard) `CORS_ORIGINS` in production — both
  already exist as settings (`app/core/config.py`), just need correct values (§4).
- The OAuth consent screen staying in "Testing" mode (§5) is itself a real
  access-control boundary — only pre-approved test users can complete sign-in at all.
- **Known gap, not fixed in this phase**: no rate limiting exists on any endpoint
  today, including `/api/v1/gmail/sync` (the most expensive endpoint to abuse, since it
  triggers real Gmail API calls). Real but low-severity at this project's traffic
  scale — recorded here consistent with this project's existing practice (see
  CLAUDE.md's "Known gaps" section) of documenting a gap rather than speculatively
  fixing everything.
- Enable GitHub's free Dependabot alerts on the new repo for both the Python and Node
  dependency manifests — zero setup cost, real value.
- Rotating `GMAIL_TOKEN_ENCRYPTION_KEY` in the future invalidates every stored Gmail
  refresh token (existing `GmailConnection` rows), forcing affected users to
  reconnect — an operational note to document, not tooling to build at this scale.
- A git-history secret scan (confirming no real credential ever landed in a commit,
  despite `backend/.env` being gitignored since Phase 1) is an explicit checkpoint
  (§13.8) before the repo goes public, not just assumed safe from memory.
- `/docs` (FastAPI's Swagger UI) stays enabled — a portfolio project benefits from a
  reviewer being able to explore the API, and the schema is already visible in the
  public source regardless. Trivially reversible if this call changes.

## 12. End-to-End Verification (Post-Deploy)

In order: `/health` → `/health/ready` → `/health/worker` → frontend loads with every
`/api/*` call showing as same-origin (no CORS errors in the browser Network tab) →
Google login completes as a registered test user and lands back on the dashboard
authenticated → "Connect Gmail" completes the second OAuth consent
(`gmail.readonly` + offline access) and creates a `GmailConnection` row → "Sync Gmail"
creates a `SyncJob` and it actually gets processed (confirmed by watching
`/health/worker`'s timestamp advance, proving the in-process thread is really
running) → at least one real email lands in the review queue or auto-applies as an
`Application` → Approve/Reject moves an item out of the queue correctly → manual
add/edit/delete on an application behaves as documented (`source="manual"`, never
silently overwritten) → wait out a real 15-minute idle spin-down and confirm
everything above still works after the cold-start wake → push one trivial, reversible
migration through the full CI → merge → auto-deploy pipeline and confirm the
pre-deploy command actually applies it before new code starts serving.

## 13. Phase 8 Checkpoints (High-Level)

Full step-by-step detail belongs in the implementation plan (`writing-plans` skill);
this is the checkpoint shape that plan will be built around. Each checkpoint is either
additive-and-reversible (an env var, a platform config, a new small file) or
infrastructure that can simply be deleted with no effect on local dev — nothing here
risks the existing local-dev workflow, since `RUN_WORKER_IN_PROCESS` defaults off and
`sync/worker.py` itself is never modified.

1. **Repo + CI foundation** — push to a new public GitHub repo, add the Actions
   workflow (§9), make both jobs required status checks on `main`. *Rollback: nothing
   is deployed yet — zero production risk.*
2. **Database provisioning** — create the Neon project, apply migrations from a local
   machine pointed at it, confirm connectivity and schema before any app touches it.
   *Rollback: delete the Neon project.*
3. **Backend deploy skeleton (no worker yet)** — deploy FastAPI to Render against
   Neon, verify `/health` and `/health/ready`. *Rollback: delete the Render service.*
4. **OAuth production wiring** — add production redirect URIs/origins in Google
   Console (§5), set real secrets in Render (§4), verify the full login round-trip.
   *Rollback: remove the added redirect URIs; no data at risk.*
5. **Frontend deploy + rewrite rule** — deploy the Static Site, configure the rewrite
   rule, verify same-origin behavior directly in the browser. *Rollback: deleting the
   Static Site doesn't touch the backend.*
6. **In-process worker integration** (§3.3) — add the startup wrapper, deploy, verify
   `/health/worker` and a real processed sync job. *Rollback: flip
   `RUN_WORKER_IN_PROCESS` off — falls back to "no worker running," not a regression
   from any state this project has ever shipped in.*
7. **CI-gated auto-deploy** — connect Render's auto-deploy to `main`, confirm (via
   branch protection) it only fires post-merge with CI green. *Rollback: disable
   auto-deploy, revert to manual deploys.*
8. **Security & observability pass** (§11) — Dependabot enabled, git-history secret
   scan, confirm cookie/CORS settings are exactly right in the live environment.
9. **Full end-to-end verification** (§12) against the live deployment — the gate for
   "did Phase 8 actually work," not just "did each piece deploy."
10. **Documentation** — production URLs and deploy steps in `README.md`; a "Phase 8
    results" section in `CLAUDE.md`, matching this project's established
    per-phase-results convention.

## 14. Cost Summary

| Component | Platform | Cost |
|---|---|---|
| Frontend (Static Site) | Render | $0 |
| Backend + worker (Web Service) | Render | $0 (free tier: 15-min idle spin-down, ~30-60s cold-start wake) |
| Database | Neon | $0 (free tier: autosuspend on idle, wakes on connect) |
| CI | GitHub Actions | $0 (public-repo Actions minutes are free) |
| Domain | Render/Neon subdomains | $0 (per your answer — no custom domain) |
| **Total** | | **$0/month** |
