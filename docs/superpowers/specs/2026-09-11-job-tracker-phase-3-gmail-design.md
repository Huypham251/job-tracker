# Job Application Tracker — Phase 3 Design: Gmail Integration

**Date:** 2026-09-11
**Status:** Approved for implementation planning
**Scope:** Phase 3 only — let an authenticated user explicitly connect their
Gmail account via a second, incremental OAuth consent; securely store the
resulting tokens; retrieve Gmail messages on demand; and disconnect/revoke.
Builds directly on Phase 2
(`docs/superpowers/specs/2026-09-11-job-tracker-phase-2-auth-design.md`),
which already anticipated this phase (its §3 and §8 explicitly reserve an
"encrypted column... never mixed into this session JWT" for Gmail).

## 1. Goal & Non-Goals

### Goal

1. An authenticated user can connect their Gmail account via a distinct,
   explicit consent step — never implied by logging in.
2. The backend requests the minimum scope appropriate for this phase and
   future extraction work, and stores only what it needs.
3. Google access/refresh tokens are stored encrypted at rest, never logged,
   never exposed via any API response.
4. A user can disconnect Gmail: we revoke the grant at Google and delete our
   stored connection.
5. The backend can retrieve Gmail messages for the connected account on
   demand (a "test retrieval" endpoint), without persisting message content.
6. The frontend shows connection status and offers connect/disconnect/fetch
   actions.
7. All schema changes go through Alembic; new behavior is covered by tests.

### Non-Goals (explicitly deferred)

LLM classification/extraction of message content, automatic application
creation or status updates from email, Redis, Celery/background jobs,
scheduling/polling for new mail, rate limiting, monitoring, persisting any
Gmail message data (subjects/snippets/bodies) in our database, supporting
more than one connected Gmail account per user, CI, deployment.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Gmail OAuth scope | `gmail.readonly` | Read-only, full message content. Narrower `gmail.metadata` would block the future LLM-extraction phase (needs message bodies) and force a second re-consent later. `gmail.readonly` is the narrowest scope that won't need upgrading. |
| Login vs. Gmail consent | Fully separate, second Authlib client (`google_gmail`) | Logging in must never grant Gmail access. Every `/gmail/*` route requires an existing session (`get_current_user`) — connecting Gmail is an authenticated action, not an identity mechanism. |
| Token exchange/refresh library | Authlib for the handshake, hand-rolled `httpx` for refresh + Gmail REST calls | Authlib already handles the risky part (state/nonce/PKCE/code exchange) and is a proven dependency from Phase 2. Refresh and the two Gmail endpoints we need are single documented HTTP calls — not worth a second, heavier client library (`google-api-python-client`) with its own credential-object model. |
| Message persistence | None — retrieval is transient | Directly serves "avoid unnecessarily retrieving/storing unrelated email data." The test-retrieval endpoint fetches live from Gmail and returns results to the caller; nothing about message content touches our DB. Real storage/retention design is deferred to the extraction phase that actually needs it. |
| Test-retrieval filtering | None — N most recent messages, no query filter | Keyword/relevance filtering is a classification concern that belongs in the future LLM-extraction phase. Phase 3 only needs to prove the OAuth + Gmail API plumbing works end-to-end. |
| Data fetched per message | `format=metadata` (`Subject`, `From`, `Date`) + snippet | Even though `gmail.readonly` permits full body access, the test endpoint doesn't need bodies yet — fetching only headers is a concrete data-minimization step beyond what the scope alone requires. |
| Token storage | New `gmail_connections` table (not a column on `User`) | Keeps connect/disconnect lifecycle and token metadata out of the core identity table; disconnect becomes a clean row delete. One row per user (`user_id` unique FK). |
| Token encryption | Fernet (`cryptography`), key from new `GMAIL_TOKEN_ENCRYPTION_KEY` env var | Symmetric encryption at rest, consistent with the existing "secrets live in `.env`" trade-off accepted in Phase 2 for one dev/local instance. |
| Disconnect behavior | Revoke at Google (`POST /revoke`) **and** delete our row, regardless of revoke outcome | A disconnect that only deletes our row would leave "Job Tracker" listed as an authorized app on the user's Google account. Deleting our row unconditionally (even if revoke fails) avoids a disconnect button that doesn't disconnect; a failed revoke is logged, not fatal. |
| Google Cloud OAuth client | Reuse the existing `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` | Same Google Cloud project/OAuth client can request an additional scope on a second consent; no new credentials needed, only enabling the Gmail API and adding the new redirect URI + scope in Cloud Console. |

## 3. Project Structure — additions and changes

```
backend/
├── alembic/versions/
│   └── 0004_add_gmail_connections.py       # NEW
├── app/
│   ├── core/
│   │   └── config.py                       # MODIFIED — gmail_token_encryption_key
│   └── gmail/                              # NEW package
│       ├── __init__.py
│       ├── oauth.py                        # Authlib "google_gmail" client (gmail.readonly, offline+consent)
│       ├── crypto.py                       # Fernet encrypt/decrypt helpers
│       ├── models.py                       # GmailConnection
│       ├── schemas.py                      # GmailStatus, GmailMessageSummary
│       ├── service.py                      # connect/disconnect/status/token-refresh/message-listing
│       └── router.py                       # /connect, /callback, /status, /disconnect, /messages
├── pyproject.toml                          # MODIFIED — add `cryptography`
└── tests/
    └── test_gmail.py                       # NEW

frontend/src/
├── types/
│   └── gmail.ts                            # NEW
├── api/
│   └── gmail.ts                            # NEW — getGmailStatus(), disconnectGmail(), fetchGmailMessages()
└── components/
    └── GmailPanel.tsx                      # NEW — connect/disconnect/status/fetch UI, rendered on dashboard
```

## 4. Data Model

### New table: `gmail_connections`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `uuid4` | consistent with `users`/`applications` |
| `user_id` | `UUID` | FK → `users.id`, **unique**, not null, `ON DELETE CASCADE` | one Gmail connection per app user |
| `google_email` | `varchar(255)` | not null | the Gmail account granted (may differ from the login email) |
| `access_token_encrypted` | `text` | not null | Fernet ciphertext; short-lived (~1hr) |
| `refresh_token_encrypted` | `text` | not null | Fernet ciphertext; long-lived |
| `token_expiry` | `timestamptz` | not null | when `access_token_encrypted` needs refreshing |
| `scope` | `varchar(255)` | not null | granted scope string, recorded for audit |
| `created_at` / `updated_at` | `timestamptz` | not null, `TimestampMixin` | same pattern as `users`/`applications` |

No message content is stored anywhere. `GmailConnection.owner: Mapped["User"]` /
`User.gmail_connection: Mapped["GmailConnection" | None]` one-to-one
relationship, `cascade="all, delete-orphan"` on the `User` side (deleting a
`User` deletes their Gmail connection too, same pattern as `applications`).

### Migration `0004_add_gmail_connections`

1. Create `gmail_connections` table with the FK (`ondelete="CASCADE"`) and a
   unique index on `user_id`.
2. `downgrade()` drops it.

No changes to `users` or `applications`.

## 5. Gmail Connection Flow

Libraries: `authlib` (second registered client), `httpx` (refresh + Gmail
REST calls), `cryptography` (Fernet, token encryption).

```
Browser                    FastAPI                          Google
  │ (already logged in — access_token session cookie present)
  │ GET /api/v1/gmail/connect
  ├──────────────────────────▶
  │                           current_user = Depends(get_current_user)   [401 if not logged in]
  │                           Authlib builds consent URL:
  │                             scope=gmail.readonly
  │                             access_type=offline&prompt=consent  (forces a refresh_token every time)
  │                             state/nonce → SessionMiddleware cookie
  │  302 → accounts.google.com (consent screen names Gmail read access specifically)
  ◀──────────────────────────┤
  │  (user approves)
  ├───────────────────────────────────────────────────────────────────▶
  │  302 → /api/v1/gmail/callback?code=...&state=...
  ◀───────────────────────────────────────────────────────────────────┤
  ├──────────────────────────▶
  │                           current_user = Depends(get_current_user)  [our session cookie rides along —
  │                                                                       SameSite=Lax allows top-level GET nav]
  │                           verify state; exchange code → {access_token, refresh_token, expires_in, scope}
  │                           GET .../gmail/v1/users/me/profile (learns google_email — gmail.readonly
  │                                                               already covers this endpoint, no extra scope)
  │                           encrypt both tokens; upsert GmailConnection(user_id=current_user.id, ...)
  │  302 → FRONTEND_URL
  ◀──────────────────────────┤
```

Both `/gmail/connect` and `/gmail/callback` require `get_current_user`, so
the connection is always attributed to whoever is logged in at the time —
there is no path to the callback without our own session cookie already
present.

## 6. Gmail Retrieval Flow

```
service.get_valid_access_token(connection):
   if token_expiry - now() < 60s:   # refresh a minute early rather than racing expiry mid-request
       POST https://oauth2.googleapis.com/token   (grant_type=refresh_token)
       decrypt→use existing refresh_token; update access_token_encrypted + token_expiry in place
   return decrypted access_token

service.list_recent_messages(access_token, limit):
   GET https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=limit
   for each id:
       GET .../messages/{id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date
   return [{id, subject, from, date, snippet}, ...]
```

`GET /api/v1/gmail/messages?limit=20` calls the above and returns the list
as JSON. Nothing is written to any table by this endpoint — the response is
the only place the data exists after the request completes.

## 7. Disconnect Flow

`POST /api/v1/gmail/disconnect`:
1. Load the current user's `GmailConnection` (404 if none).
2. Decrypt the refresh token; `POST https://oauth2.googleapis.com/revoke`
   with it (revoking a refresh token also invalidates derived access
   tokens).
3. Delete the `GmailConnection` row **regardless of the revoke call's
   outcome** — log a warning on failure, don't block the delete. A stale
   grant at Google with no local record is a smaller problem than a
   disconnect button that doesn't disconnect.
4. Respond `204`.

## 8. API Contract

New router `app/gmail/router.py`, mounted at `/api/v1/gmail`. **Every
endpoint requires `get_current_user`** (no anonymous Gmail routes):

| Method | Path | Response | Notes |
|---|---|---|---|
| `GET` | `/api/v1/gmail/connect` | `302` → Google | full navigation, same reasoning as `/auth/google/login` |
| `GET` | `/api/v1/gmail/callback` | `302` → `FRONTEND_URL` | stores the encrypted connection |
| `GET` | `/api/v1/gmail/status` | `200` `GmailStatus` | `{connected: bool, email: str \| null, connected_at: datetime \| null}` |
| `POST` | `/api/v1/gmail/disconnect` | `204` | `404` if not connected |
| `GET` | `/api/v1/gmail/messages?limit=20` | `200` `list[GmailMessageSummary]` | `404` if not connected (same `GmailNotConnected` → 404 convention as `ApplicationNotFound`); `limit` defaults to 20, capped at 50 |

`GmailMessageSummary`: `id`, `subject`, `from_`, `date`, `snippet`. No token
material is ever present in any response body.

## 9. Frontend Structure & Data Flow

```
GmailPanel.tsx (rendered on the dashboard once logged in)
   │  on mount: GET /api/v1/gmail/status
   │  not connected → "Connect Gmail" <a href="/api/v1/gmail/connect"> (real navigation)
   │  connected     → "Connected as <email>" + "Disconnect" button + "Fetch recent messages" button
   │                    fetch → GET /api/v1/gmail/messages, render list in component state (not persisted)
```

- `api/gmail.ts`: `getGmailStatus()`, `disconnectGmail()`,
  `fetchGmailMessages(limit)` — same `parseResponse<T>` pattern as
  `api/auth.ts`/`api/applications.ts`.
- The "Connect Gmail" control is a real `<a href>`, not a JS click handler —
  same reasoning as the existing Google login link (OAuth redirects don't
  work through `fetch`/XHR).
- No new frontend dependencies.

## 10. Security & Privacy Considerations

| Concern | Position |
|---|---|
| Token storage | Both `access_token` and `refresh_token` encrypted at rest with Fernet, key from `GMAIL_TOKEN_ENCRYPTION_KEY` (new env var, generated the same way as `SECRET_KEY`). Decrypted only in-memory inside `app/gmail/service.py`, only for the duration of a refresh or an API call. Never logged, never returned in any API response. |
| Secret storage | Same accepted trade-off as Phase 2 §8: fine for one dev/local instance in `.env`; needs a real secrets manager before any shared/production deployment. |
| Login/Gmail separation | Enforced structurally, not just by convention: `google` and `google_gmail` are independent Authlib clients with independent scopes and independent state/nonce handshakes. `/auth/*` never touches `gmail_connections`; `/gmail/*` never mints or reads the session JWT itself (only depends on `get_current_user` like any other protected route). |
| Cross-user access | `gmail_connections.user_id` is unique and every route requires `get_current_user`; no connection id is ever accepted from the client, so there's no id to enumerate. |
| Data minimization | `gmail.readonly` is broad, but the test-retrieval endpoint requests `format=metadata` with only `Subject`/`From`/`Date` — bodies and attachments are never fetched even though the scope would allow it, because nothing yet needs them. No message content is persisted (§1). |
| Revoke-then-delete | Disconnect revokes at Google and deletes locally regardless of revoke outcome (§7) — the failure mode is a stale grant at Google, never a stale row in our DB. |
| CSRF | Same mitigation as login: Authlib `state` (via `SessionMiddleware`) + `SameSite=Lax` on the session cookie gating every `/gmail/*` route. |
| Logging discipline | Errors during token refresh or message fetch must log message/connection **ids**, never token values or message content. |
| Consent transparency | Gmail's sensitive-scope consent screen is shown on its own, separate step — a user can use Job Tracker (log in, manage applications) indefinitely without ever seeing it, until they explicitly click "Connect Gmail." |

## 11. Testing Plan

Follows the existing `test_auth.py` conventions: monkeypatch Authlib and
outbound HTTP calls rather than hitting real Google; reuse the `auth_client`
/ `other_auth_client` fixtures already in `conftest.py`.

`backend/tests/test_gmail.py` (new):

- `GET /gmail/status` unauthenticated → `401`; authenticated + not connected
  → `{connected: false, email: null, connected_at: null}`.
- Monkeypatch `oauth.google_gmail.authorize_access_token` (same technique as
  the existing `test_google_callback_oauth_error_redirects_to_frontend`) to
  simulate a successful callback → a `GmailConnection` row is created; assert
  the stored `access_token_encrypted`/`refresh_token_encrypted` values are
  **not** the plaintext tokens; `/gmail/status` now returns
  `{connected: true, email: ...}`.
- OAuth error on the Gmail callback → redirects to `FRONTEND_URL`, no row
  created (mirrors the existing login-callback error test).
- `GET /gmail/messages` with a `token_expiry` in the past → monkeypatch the
  refresh POST, assert it's called once and the stored access
  token/expiry are updated.
- `GET /gmail/messages` → monkeypatch the Gmail `messages.list`/`messages.get`
  HTTP calls, assert the returned JSON shape, and assert no new row exists in
  any table afterward (proves nothing was persisted).
- `POST /gmail/disconnect` → monkeypatch the revoke call, assert it's called
  with the decrypted refresh token and the row is deleted; a second test
  covers revoke-call-raises but the row is still deleted.
- `POST /gmail/disconnect` when not connected → `404`.
- Cross-user isolation: `other_auth_client`'s `/gmail/status` and
  `/gmail/messages` never see or act on a different user's connection.

## 12. Google Cloud Console Setup (documented in the plan/README, not code)

Using the existing OAuth Client ID from Phase 2: enable the **Gmail API** on
the project, add `https://www.googleapis.com/auth/gmail.readonly` to the
consent screen's scopes (this will mark the app as requesting a sensitive
scope — expected for local dev/testing mode), and add
`http://localhost:8000/api/v1/gmail/callback` as an additional authorized
redirect URI alongside the existing `/auth/google/callback` one. Generate
`GMAIL_TOKEN_ENCRYPTION_KEY` the same way as `SECRET_KEY` and add it to
`backend/.env` / `.env.example`. The implementation plan will spell out the
exact clicks.

## 13. Preserving Phase 1 & 2 Functionality

Nothing about login, sessions, or application CRUD changes. Gmail connection
state is entirely additive: an existing user who never clicks "Connect
Gmail" sees no behavior change at all. All Phase 1/2 tests continue to pass
unmodified.
