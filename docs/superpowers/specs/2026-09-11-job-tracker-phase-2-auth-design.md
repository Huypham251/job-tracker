# Job Application Tracker — Phase 2 Design: Auth & Multi-User

**Date:** 2026-09-11
**Status:** Approved for implementation planning
**Scope:** Phase 2 only — Google OAuth login, a `User` model, and scoping all
application CRUD to the authenticated user. Builds directly on Phase 1
(`docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`).

## 1. Goal & Non-Goals

### Goal

Turn the single-shared-list Phase 1 tracker into a multi-user application:

1. Users sign in with Google.
2. The backend can identify the current user on every request (`get_current_user`).
3. Every application belongs to exactly one user.
4. A user can only see, create, edit, and delete their own applications.
5. The frontend gates the dashboard behind login and offers logout.
6. Authentication and authorization are covered by tests.
7. All schema changes go through Alembic.

### Non-Goals (explicitly deferred)

Gmail API integration, LLM extraction, Redis, Celery/background jobs, rate
limiting, monitoring, CI, deployment, CSRF-token middleware (SameSite is the
accepted mitigation for now — see §8), server-side session revocation
(consequence of stateless JWT — see §8), multi-provider login (Google only).

## 2. Phase 1 review findings addressed here

- **Alembic test-fixture drift (flagged at the end of Phase 1, now fixed):**
  `backend/tests/conftest.py` built the test schema with
  `Base.metadata.create_all()` instead of running real migrations, so
  migration↔model drift was invisible to tests. It already exists: migration
  `0001` gives `applications.status` a `server_default="applied"` the ORM
  model never declared. Phase 2's `conftest.py` runs `alembic upgrade head`
  against the test database instead.
- **Hardcoded test database URL:** `conftest.py` hardcoded
  `postgresql+psycopg://jobtracker:jobtracker@localhost:5432/...`. Phase 2
  derives it from `settings.database_url` (swapping the database name),
  closing that finding too.
- Everything else the Phase 1 final review deferred (CORS + credentials
  combination, `order_by` tiebreaker, frontend a11y, dead template files) is
  unrelated to auth and stays deferred.

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| OAuth provider | Google only | Matches the stated goal; Gmail integration later reuses the same provider. |
| OAuth flow | Backend-driven authorization code flow (Authlib) | Phase 3 will need server-held Gmail refresh tokens (`access_type=offline`) — a frontend-only "get an ID token" flow (Google Identity Services) can't get those without redoing the flow later. Doing it server-side now means Phase 3 adds a scope, not a new architecture. |
| Session mechanism | Backend-issued JWT in an HttpOnly, SameSite=Lax cookie | Decouples our session from Google's token lifecycle; not readable by JS (XSS-resistant); no session-store table or Redis needed yet. |
| User identity key | Google `sub` (`google_sub` column, unique) | Stable even if the user's email changes; `email` is a separate unique column, not the join key. |
| Frontend routing | No router — conditional render in `App.tsx` based on auth state | Only two screens exist (login, dashboard); a router is unjustified complexity until there are more. |
| Cross-user access | Returns `404`, never `403` | Confirms nothing about resource existence to a non-owner (no enumeration leak). Reuses the existing `ApplicationNotFound`. |
| Existing `applications` rows | Truncated by the Phase 2 migration | Local dev/test data, no value; avoids a one-time backfill/placeholder-user migration with no long-term purpose. |
| OAuth library | `authlib` | Handles state/nonce/PKCE and `id_token` signature verification against Google's published keys — hand-rolling this is a well-known way to introduce a CSRF or token-forgery bug. |
| Our session token library | `pyjwt` | Minimal, actively maintained, sufficient for HS256 encode/decode. |
| Gmail scope | Not requested in Phase 2 | Out of scope per the brief; requesting only `openid email profile` keeps the consent screen honest about what this phase actually does. |

## 4. Project Structure — additions and changes

```
backend/
├── alembic/versions/
│   └── 0002_add_users_and_application_ownership.py     # NEW
├── app/
│   ├── main.py                          # MODIFIED — mount auth router, SessionMiddleware
│   ├── core/
│   │   └── config.py                    # MODIFIED — Google creds, JWT secret, cookie settings
│   ├── users/                           # NEW package
│   │   ├── __init__.py
│   │   ├── models.py                    # User
│   │   └── schemas.py                   # UserRead
│   ├── auth/                            # NEW package
│   │   ├── __init__.py
│   │   ├── oauth.py                     # Authlib Google client registration
│   │   ├── jwt.py                       # encode/decode our session token
│   │   ├── dependencies.py              # get_current_user
│   │   └── router.py                    # /google/login, /google/callback, /me, /logout
│   └── applications/
│       ├── models.py                    # MODIFIED — user_id FK + relationship
│       ├── schemas.py                   # MODIFIED — no change to client-facing shape
│       ├── service.py                   # MODIFIED — every fn takes + filters by user_id
│       └── router.py                    # MODIFIED — Depends(get_current_user) on every route
└── tests/
    ├── conftest.py                      # MODIFIED — alembic-driven schema, settings-derived URL, auth fixtures
    ├── test_applications_api.py         # MODIFIED — uses an authenticated client
    └── test_auth.py                     # NEW — authn/authz tests

frontend/src/
├── types/
│   └── user.ts                          # NEW
├── api/
│   └── auth.ts                          # NEW — getCurrentUser(), logout()
├── context/
│   └── AuthContext.tsx                  # NEW — AuthProvider, useAuth()
├── components/
│   ├── LoginPage.tsx                    # NEW
│   ├── UserMenu.tsx                     # NEW
│   └── ApplicationsPage.tsx             # MODIFIED — renders UserMenu
└── App.tsx                              # MODIFIED — AuthProvider + login/dashboard switch
```

## 5. Data Model

### New table: `users`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `uuid4` | consistent with `applications.id` |
| `google_sub` | `varchar(255)` | not null, unique, indexed | Google's stable subject identifier (OIDC `sub` claim) |
| `email` | `varchar(255)` | not null, unique | from the verified `id_token` |
| `name` | `varchar(255)` | not null | from the `id_token`; falls back to `email` if Google omits it |
| `picture_url` | `varchar(1024)` | nullable | Google's profile photo URL, for the frontend user menu |
| `created_at` / `updated_at` | `timestamptz` | not null, `TimestampMixin` | same pattern as `applications` |

### Modified table: `applications`

- Add `user_id: UUID`, FK → `users.id`, **not null**, indexed.
- SQLAlchemy: `Application.owner: Mapped["User"] = relationship(back_populates="applications")`, `User.applications: Mapped[list["Application"]] = relationship(back_populates="owner", cascade="all, delete-orphan")`.
- `ON DELETE CASCADE` at the DB level too — if a `User` row is ever deleted, their applications go with it (no orphaned rows to leak through a reused id later).

### Migration `0002_add_users_and_application_ownership`

One migration, in order:
1. Create `users` table.
2. `TRUNCATE applications` (per §3 — local dev data, no backfill logic to maintain).
3. Add `applications.user_id` as **nullable** first, then `ALTER COLUMN ... SET NOT NULL` (standard two-step so the DDL is valid even though the table is already empty from step 2 — this also happens to be the safe pattern for a non-empty table, modeling the practice correctly for future migrations).
4. Add the FK constraint (`ondelete="CASCADE"`) and an index on `user_id`.

`downgrade()` reverses in the opposite order: drop FK/index/column, then drop `users`.

## 6. Google Authentication Flow

Libraries: `authlib` (OAuth client, `id_token` verification), `pyjwt` (our session token).

```
Browser                          FastAPI                         Google
   │  GET /auth/google/login        │                                │
   ├────────────────────────────────▶                                │
   │                                │  build consent URL             │
   │                                │  (state+nonce → session cookie)│
   │  302 → accounts.google.com     │                                │
   ◀────────────────────────────────┤                                │
   │  (user approves)                                                │
   ├─────────────────────────────────────────────────────────────────▶
   │  302 → /auth/google/callback?code=...&state=...                 │
   ◀─────────────────────────────────────────────────────────────────┤
   ├────────────────────────────────▶                                │
   │                                │  verify state                  │
   │                                │  exchange code for tokens ─────▶
   │                                │  ◀───────────────────── tokens ┤
   │                                │  verify id_token (sig/iss/aud/nonce)
   │                                │  upsert User by google_sub     │
   │                                │  mint our JWT                  │
   │  302 → http://localhost:5173/  │  Set-Cookie: access_token (HttpOnly)
   ◀────────────────────────────────┤                                │
   │  GET /auth/me (cookie rides along, same-origin via Vite proxy)  │
   ├────────────────────────────────▶                                │
   │  200 {id, email, name, picture_url}                             │
   ◀────────────────────────────────┤                                │
```

Steps, in prose:

1. **Login link.** `<a href="/api/v1/auth/google/login">` — a real navigation, not `fetch` (OAuth redirects don't work through XHR/CORS).
2. **`GET /auth/google/login`** — Authlib builds Google's consent URL with our `client_id`, `redirect_uri`, scopes `openid email profile`, and a random `state` + `nonce`. Those are stashed in a short-lived signed cookie via Starlette's `SessionMiddleware` — used *only* for this handshake, entirely separate from the app's real auth cookie.
3. **Google's consent screen** → user approves → Google 302s the browser to `GET /auth/google/callback?code=...&state=...`.
4. **Callback:** Authlib checks `state`, exchanges `code` for tokens server-to-server, verifies the `id_token`'s signature (against Google's JWKS), issuer, audience, and `nonce`. Extracts `sub`, `email`, `name`, `picture`.
5. **Upsert `User`** by `google_sub` (create on first login).
6. **Mint our JWT:** `{sub: <our user id>, exp: now + ACCESS_TOKEN_EXPIRE_MINUTES}`, signed with `SECRET_KEY`.
7. **Set it as the `access_token` cookie** (`HttpOnly`, `SameSite=Lax`, `Secure` from a setting) and **302 to `FRONTEND_URL`**.
8. **Frontend's `AuthProvider`** calls `GET /auth/me` on load. Cookie is attached automatically (same-origin through the Vite proxy in dev — no `credentials` option or CORS change needed). `200` → dashboard; `401` → login screen.
9. **Logout:** `POST /auth/logout` expires the cookie. The JWT itself is stateless and remains valid until it expires — see §8.

## 7. Authorization / Ownership Model

- **`get_current_user`** (`app/auth/dependencies.py`): reads the cookie →
  decodes/verifies the JWT → loads the `User` row → returns it, or raises
  `401` (missing/invalid/expired token, or user since deleted). This is the
  single place JWT logic lives; every protected route depends on it.
- **Service functions gain `user_id` and filter in the query itself:**
  `select(Application).where(Application.id == application_id, Application.user_id == user_id)`
  rather than fetch-then-check. One indexed query; no separate ownership
  branch to forget.
- **Non-owned or nonexistent rows are indistinguishable:** both raise
  `ApplicationNotFound` → `404`. Never `403` for someone else's row.
- **`ApplicationCreate` never accepts `user_id` from the client** — it's
  always `current_user.id`, injected server-side after the dependency
  resolves. There is no request field that can set it.
- **`/auth/me`** is the one endpoint whose entire job is to answer "who am
  I" — also gated by `get_current_user`, so an unauthenticated call gets a
  plain `401` the frontend uses to decide whether to show the login screen.

## 8. Security Considerations (accepted trade-offs, stated explicitly)

| Concern | Position |
|---|---|
| CSRF | No CSRF token / double-submit cookie. `SameSite=Lax` blocks cross-site `POST` from carrying the cookie, which covers this app's shape (same-site SPA, JSON fetches). Revisit if the app ever serves cross-subdomain or accepts cross-site form posts. |
| Session revocation | None — stateless JWT means "logout" only clears the client cookie; the token is valid until it expires even after logout. Acceptable because nothing high-stakes is gated behind a session yet. Revisit before anything sensitive (e.g. account deletion, billing) leans on session validity alone. |
| Secret storage | `SECRET_KEY` and Google client secret live in `.env`, same as `DATABASE_URL` today. Fine for one dev/local instance; not fine for multiple instances or real deployment — needs a secrets manager at that point (deployment is a later phase). |
| `Secure` cookie flag | A setting (`COOKIE_SECURE`), `False` for local `http://localhost`, meant to flip to `True` the moment this serves over `https`. |
| Resource enumeration | Mitigated by the 404-for-everything-not-yours rule (§7). |
| Gmail scope creep | Not requested in Phase 2. Phase 3 will do a *second*, incremental authorization for `gmail.readonly` + `access_type=offline`; that refresh token gets its own encrypted column on `User`, never mixed into this session JWT. |

## 9. API Contract

New router `app/auth/router.py`, mounted at `/api/v1/auth`:

| Method | Path | Auth required | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/v1/auth/google/login` | no | `302` → Google | full navigation |
| `GET` | `/api/v1/auth/google/callback` | no | `302` → `FRONTEND_URL` | sets the auth cookie |
| `GET` | `/api/v1/auth/me` | yes | `200` `UserRead` | `401` if not authenticated |
| `POST` | `/api/v1/auth/logout` | yes | `204` | clears the cookie |

`UserRead`: `id`, `email`, `name`, `picture_url`.

Existing `applications` endpoints (`GET/POST /applications`, `GET/PATCH/DELETE /applications/{id}`) are unchanged in shape — same request/response bodies as Phase 1 — but every one now requires authentication (`401` if the cookie is missing/invalid) and is scoped to `current_user.id`. `ApplicationCreate`/`ApplicationUpdate`/`ApplicationRead` schemas are unchanged; `user_id` is never in the request body and is not exposed in the response body (the frontend never needs it — it only ever sees its own rows).

## 10. Backend Request Flow (applications, post-auth)

```
router.py     current_user = Depends(get_current_user)   [401 short-circuits here]
   │          parse/validate body
   ▼
service.py    e.g. update_application(db, user_id=current_user.id, application_id, data)
   │          - SELECT ... WHERE id = :id AND user_id = :user_id
   │          - None → raise ApplicationNotFound(application_id)   [same as "doesn't exist"]
   │          - apply model_dump(exclude_unset=True); commit; refresh
   ▼
router.py     return; response_model=ApplicationRead serializes (no user_id field)
```

Config (`core/config.py`) gains: `google_client_id`, `google_client_secret`,
`secret_key`, `jwt_algorithm` (default `HS256`),
`access_token_expire_minutes` (default `43200` = 30 days), `frontend_url`
(post-login redirect target), `cookie_secure: bool` (default `False`).

`secret_key` is used for two unrelated things sharing one setting: signing
our long-lived `access_token` JWT (`pyjwt`, HS256), and signing Starlette's
`SessionMiddleware` cookie (the few-seconds-lived OAuth `state`/`nonce`
stash, via `itsdangerous`). Sharing one secret between an HMAC-JWT and an
itsdangerous-signed cookie is cryptographically fine (different algorithms,
different cookies, no shared state) and simpler than managing two secrets
for one dev app — noted explicitly so the plan doesn't invent a second
setting unnecessarily.

## 11. Frontend Structure & Data Flow

```
AuthContext (context/AuthContext.tsx)
   │  useAuth() -> { user, loading, error, refetch, logout }
   │  on mount: GET /api/v1/auth/me -> sets user or null
   ▼
App.tsx
   │  loading -> spinner
   │  !user   -> <LoginPage/>        (renders the Google login <a>)
   │  user    -> <ApplicationsPage/> (now also renders <UserMenu user={user} onLogout={logout}/>)
```

- `api/auth.ts`: `getCurrentUser(): Promise<User>` (throws on non-2xx, same
  `parse<T>` pattern as `api/applications.ts`), `logout(): Promise<void>`.
- `AuthProvider` wraps `<App/>` in `main.tsx`. `useApplications()` is
  unchanged — it still just calls the same `/applications` endpoints; the
  cookie makes them user-scoped transparently, no frontend change needed
  there beyond "don't render this hook's consumer until `user` exists."
- `LoginPage.tsx`: centered card, "Sign in with Google" as a real `<a href="/api/v1/auth/google/login">` styled as a button (not a JS click handler — see §6 step 1).
- `UserMenu.tsx`: shows `picture_url` (fallback to initials if absent) + `name`, a "Log out" button that calls `logout()` then `refetch()`s the auth state (which will now 401 and flip back to the login screen).
- No new dependencies beyond what's already in `frontend/package.json`.

## 12. Testing Plan

### `tests/conftest.py` changes

- `engine` fixture: instead of `Base.metadata.create_all()`, build an
  `alembic.config.Config`, point `sqlalchemy.url` at the (settings-derived)
  test database URL, and run `alembic.command.upgrade(cfg, "head")`;
  teardown runs `command.downgrade(cfg, "base")`. This is the fix from §2.
- `TEST_DATABASE_URL` / `ADMIN_URL` derived from `settings.database_url`
  (via `sqlalchemy.engine.make_url(...).set(database=...)`) instead of a
  hardcoded literal.
- `alembic/env.py` changes to `if not config.get_main_option("sqlalchemy.url"): config.set_main_option(...)` so a caller (the test fixture) can pre-set the URL and Alembic won't stomp it.
- New fixtures: `other_user` / `other_user_client` (a second authenticated
  `TestClient` for a different user, used to prove cross-user isolation) and
  a helper to mint a valid auth cookie for a given `User` directly (via the
  same `app.auth.jwt.create_access_token` function the real login flow
  uses — no need to run the actual OAuth dance in tests).

### `tests/test_auth.py` (new)

- Unauthenticated `GET /applications` → `401`.
- Unauthenticated `GET /auth/me` → `401`.
- Authenticated `GET /auth/me` → `200` with the right user's fields.
- `POST /auth/logout` then `GET /auth/me` → `401`.
- Two distinct users each create an application; each user's `GET /applications` returns only their own.
- User A creates an application; User B's `GET/PATCH/DELETE` on that id → `404` (not `403`).

### `tests/test_applications_api.py` changes

- Existing tests switch from the plain `client` fixture to an authenticated
  one — behavior otherwise unchanged (Phase 1 CRUD still works, just now
  under an authenticated session).

## 13. Google Cloud Console Setup (documented in the plan/README, not code)

Since credentials don't exist yet: create an OAuth consent screen (External,
testing mode is fine for local dev) and a Web application OAuth Client ID in
Google Cloud Console, with authorized redirect URI
`http://localhost:8000/api/v1/auth/google/callback`. The resulting Client ID
and Secret go into `backend/.env` as `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET`. This is a manual, one-time, external step — the plan
will spell out the exact clicks.

## 14. Preserving Phase 1 Functionality

Nothing about the CRUD behavior itself changes: same fields, same status
enum, same PATCH-partial semantics, same validation. The only behavioral
change visible to a signed-in user is that they now only see applications
they created. All Phase 1 tests continue to assert the same things — they
just run as an authenticated user instead of anonymously.
