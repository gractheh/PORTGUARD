# PORTGUARD — Authentication & Multi-Tenant Data Isolation Architecture

**Version:** 1.0  
**Date:** 2026-04-10  
**Status:** Approved for Implementation  
**Scope:** JWT-based authentication, organization-scoped data isolation, and frontend auth flow

---

## Table of Contents

1. [Design Decisions and Rationale](#1-design-decisions-and-rationale)
2. [Auth System — Technology Stack](#2-auth-system--technology-stack)
3. [Data Model — New and Modified Tables](#3-data-model--new-and-modified-tables)
4. [API Changes — Endpoints and Middleware](#4-api-changes--endpoints-and-middleware)
5. [Frontend Changes](#5-frontend-changes)
6. [Data Isolation — Enforcement Model](#6-data-isolation--enforcement-model)
7. [Security Requirements and Controls](#7-security-requirements-and-controls)
8. [Migration Strategy](#8-migration-strategy)
9. [Threat Model and Attack Surface](#9-threat-model-and-attack-surface)
10. [Implementation Sequence](#10-implementation-sequence)

---

## 1. Design Decisions and Rationale

### 1.1 JWT vs Session-Based Authentication

**Decision: JWT (JSON Web Tokens) with a server-side refresh token registry.**

| Factor | JWT | Session-based |
|---|---|---|
| Stateless verification | Yes — no DB lookup per request | No — session lookup on every request |
| Revocation | Requires refresh token registry | Trivial — delete session row |
| SQLite compatibility | Excellent — only writes on token issue/refresh/logout | Requires sessions table with high write rate |
| API-first design | Natural — `Authorization: Bearer <token>` | Awkward for programmatic clients (cookie handling) |
| Multi-process | Stateless — any process can verify | Requires shared session store |
| Complexity | Moderate — two-token pattern | Low (single token) |

PORTGUARD's primary use case is API access by programmatic clients (customs brokers, ERP integrations, the browser demo). JWT maps naturally to this: each request is independently authenticated by verifying the signature against a shared secret, with no database roundtrip. The `Procfile` targets a single-process PaaS deployment, so the stateless model also simplifies scaling.

The one genuine weakness of JWT — inability to instantly revoke access tokens — is addressed by keeping access token lifetime short (24 hours) and storing refresh tokens in SQLite so that logout and forced revocation work correctly.

**Two-token pattern:**
- **Access token** — short-lived (24 hours), stateless, verified by signature only, passed on every API request
- **Refresh token** — long-lived (7 days), opaque, stored in `auth_refresh_tokens` table, exchanged for a new access token

This is the same pattern used by Google OAuth, GitHub, and Auth0. It gives us the statelesness of JWT for normal request handling while preserving the ability to fully revoke a session by deleting the refresh token row.

### 1.2 Algorithm: HS256

Use **HMAC-SHA256 (HS256)** for token signing. The secret key (`JWT_SECRET_KEY`) is a 256-bit random value stored as an environment variable — never in source code or the database.

RS256 (asymmetric signing) is appropriate only when multiple independent services need to verify tokens without sharing a secret. PORTGUARD is a single-service application; HS256 has equivalent security with less operational complexity.

### 1.3 Password Hashing: bcrypt

**Decision: `bcrypt` via `passlib[bcrypt]` with work factor 12.**

bcrypt is the correct choice for password storage in 2026:
- Adaptive: work factor is tunable as hardware gets faster
- Memory-hard: resists GPU-accelerated brute-force
- Widely audited: no known practical attacks against proper implementations
- Work factor 12 produces ~250ms hash time on modern hardware — imperceptible to users, expensive for attackers

Argon2id (the PHC winner) would also be acceptable, but bcrypt has broader library support and the practical security difference at this scale is negligible. Do not use PBKDF2 or SHA-based hashing — they are not memory-hard.

### 1.4 SQLite for Auth Storage

Auth data (organizations, refresh tokens, login attempts) lives in the same SQLite file as the pattern learning data (`portguard_patterns.db`) or an adjacent dedicated auth database. **Recommendation: keep them separate** — `portguard_auth.db` — so auth schema migrations don't interlock with pattern DB migrations and the two concerns can evolve independently.

The existing pattern DB uses WAL mode and a threading.Lock for write serialization. The auth DB will use the same pattern.

---

## 2. Auth System — Technology Stack

### 2.1 Python Libraries

| Library | Version | Purpose |
|---|---|---|
| `python-jose[cryptography]` | ≥3.3.0 | JWT encoding/decoding (HS256) |
| `passlib[bcrypt]` | ≥1.7.4 | Password hashing with bcrypt |
| `slowapi` | ≥0.1.9 | Rate limiting middleware for FastAPI |
| `python-multipart` | already present | Form data support (already in requirements) |

Add to `requirements.txt`:
```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
slowapi>=0.1.9
```

### 2.2 Environment Variables

```
# Required — generate with: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=<256-bit random hex>

# Optional overrides
JWT_ACCESS_TOKEN_EXPIRE_HOURS=24     # default: 24
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7      # default: 7
LOGIN_RATE_LIMIT_PER_MINUTE=5        # default: 5
BCRYPT_ROUNDS=12                     # default: 12
AUTH_DB_PATH=portguard_auth.db       # default: portguard_auth.db
```

`JWT_SECRET_KEY` is the only hard requirement. The application must refuse to start if it is absent or empty — fail-fast at startup, not at first login attempt.

### 2.3 Token Structure

**Access token claims (JWT payload):**

```json
{
  "sub": "org_01J8F3...",          // organization_id (UUID)
  "org_name": "Acme Imports LLC",  // display name (denormalized for UI)
  "email": "admin@acme.com",       // organization email
  "iat": 1712764800,               // issued at (Unix timestamp)
  "exp": 1712851200,               // expiry: iat + 24h
  "jti": "tok_9k2m..."             // unique token ID (UUID, for future blacklist use)
}
```

The `sub` claim is always `organization_id`. The JWT is **not** encrypted — it is signed. Treat it as readable by anyone who has it. Never put secrets or sensitive compliance findings in token claims.

**Refresh token:**
- Opaque random string: `secrets.token_urlsafe(48)` → 64-character URL-safe string
- Not a JWT — it has no self-describing structure
- Stored hashed in `auth_refresh_tokens` (the plaintext value is only ever sent to the client once, on issuance; never logged)
- Linked to `organization_id` and `access_jti` for traceability

### 2.4 Token Refresh Flow

```
Client                              Server
  |                                   |
  | POST /api/v1/auth/refresh         |
  |   { "refresh_token": "<opaque>" } |
  |---------------------------------->|
  |                                   |-- Hash refresh_token
  |                                   |-- Look up in auth_refresh_tokens
  |                                   |-- Check: not expired, not revoked
  |                                   |-- Delete old refresh token row
  |                                   |-- Issue new access token (new exp, new jti)
  |                                   |-- Issue new refresh token (rotation)
  |<----------------------------------|
  |   { "access_token": "...",        |
  |     "refresh_token": "..." }      |
```

**Refresh token rotation** — each refresh operation consumes the old refresh token and issues a new one. If a stolen refresh token is used after the legitimate client has already rotated it, the server detects the reuse (the token is already gone from the DB) and can optionally revoke all sessions for that organization.

---

## 3. Data Model — New and Modified Tables

### 3.1 New Table: `organizations`

Primary account record for each company using PORTGUARD.

```sql
CREATE TABLE organizations (
    -- Identity
    organization_id     TEXT PRIMARY KEY,           -- UUID v4 e.g. "org_01J8F3..."
    created_at          TEXT NOT NULL,              -- ISO-8601 UTC

    -- Account credentials
    org_name            TEXT NOT NULL,              -- "Acme Imports LLC"
    email               TEXT NOT NULL UNIQUE,       -- login identifier (lowercase)
    password_hash       TEXT NOT NULL,              -- bcrypt hash (60 chars)

    -- API key (programmatic access, separate from JWT)
    api_key_hash        TEXT UNIQUE,                -- SHA-256 of the raw API key; NULL until generated
    api_key_prefix      TEXT,                       -- first 8 chars of raw key e.g. "pg_live_" (display only)

    -- Account state
    is_active           INTEGER NOT NULL DEFAULT 1, -- 0 = suspended/deleted
    last_login_at       TEXT,                       -- ISO-8601 UTC, updated on each successful login
    failed_login_count  INTEGER NOT NULL DEFAULT 0, -- reset on successful login
    locked_until        TEXT,                       -- ISO-8601 UTC; NULL = not locked

    -- Metadata
    plan_tier           TEXT NOT NULL DEFAULT 'standard'  -- reserved for future billing tiers
);

CREATE INDEX idx_organizations_email ON organizations(email);
CREATE INDEX idx_organizations_api_key_hash ON organizations(api_key_hash)
    WHERE api_key_hash IS NOT NULL;
```

**Design notes:**
- `organization_id` uses a `org_` prefix to make IDs human-readable and distinguish them from other UUID columns.
- `email` is the login identifier, stored lowercase. Uniqueness is enforced at the DB level, not just application level.
- `password_hash` stores only the bcrypt output — never the plaintext password, not even transiently in logs.
- `api_key_hash` is a SHA-256 hash of the raw API key. The raw key is only shown once at generation time (same model as GitHub PATs). The prefix is stored for display so users can identify which key is which without exposing the key itself.
- `failed_login_count` and `locked_until` support account lockout after repeated failures (distinct from IP-based rate limiting).

### 3.2 New Table: `auth_refresh_tokens`

Server-side registry of issued refresh tokens. Enables logout and forced revocation.

```sql
CREATE TABLE auth_refresh_tokens (
    -- Identity
    token_id            TEXT PRIMARY KEY,   -- UUID v4
    organization_id     TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,

    -- Token data
    token_hash          TEXT NOT NULL UNIQUE,  -- SHA-256 of the raw refresh token
    access_jti          TEXT NOT NULL,          -- jti claim of the paired access token

    -- Lifecycle
    issued_at           TEXT NOT NULL,          -- ISO-8601 UTC
    expires_at          TEXT NOT NULL,          -- ISO-8601 UTC (issued_at + 7 days)
    revoked_at          TEXT,                   -- NULL = active; set on logout or rotation
    revoked_reason      TEXT,                   -- "logout" | "rotation" | "admin" | "suspicious_reuse"

    -- Context
    user_agent          TEXT,                   -- for session display in a future UI
    ip_address          TEXT                    -- for audit log
);

CREATE INDEX idx_refresh_tokens_org ON auth_refresh_tokens(organization_id);
CREATE INDEX idx_refresh_tokens_hash ON auth_refresh_tokens(token_hash);
-- Partial index: only non-revoked tokens need fast lookup
CREATE INDEX idx_refresh_tokens_active ON auth_refresh_tokens(token_hash)
    WHERE revoked_at IS NULL;
```

**Rotation invariant:** when a refresh token is consumed to issue a new one, the old row has `revoked_at` set (reason: `"rotation"`) and a new row is inserted. The table is append-only from an audit perspective — rows are never deleted, only marked revoked.

**Cleanup:** a background job (or on-startup sweep) should delete rows where `expires_at < now() AND revoked_at IS NOT NULL` to prevent unbounded table growth. Revoked and expired rows have no operational value.

### 3.3 New Table: `auth_login_attempts`

Per-IP rate limiting state. Kept in SQLite rather than in-memory so limits survive process restarts and are not defeated by multi-worker deployments.

```sql
CREATE TABLE auth_login_attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address          TEXT NOT NULL,
    attempted_at        TEXT NOT NULL,   -- ISO-8601 UTC
    email_attempted     TEXT,            -- for audit; which account was targeted
    success             INTEGER NOT NULL DEFAULT 0   -- 0 = failed, 1 = succeeded
);

-- Used for rate limit window queries:
CREATE INDEX idx_login_attempts_ip_time ON auth_login_attempts(ip_address, attempted_at);
```

The rate limit query is: count rows where `ip_address = ? AND attempted_at > (now - 60s) AND success = 0`. If count ≥ 5, reject the request with HTTP 429.

Rows older than 24 hours have no operational value and can be pruned on startup or via a periodic sweep.

### 3.4 Modified Tables — Adding `organization_id`

Every table that holds organization-specific data must gain an `organization_id` column. These changes are applied via forward-only schema migrations (identical to the existing `schema_migrations` pattern in `pattern_db.py`).

**Tables requiring modification:**

| Table | Change | Notes |
|---|---|---|
| `shipment_history` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Default is only for migration of existing rows |
| `pattern_outcomes` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Outcomes belong to the org that submitted them |
| `shipper_profiles` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Each org builds its own shipper reputation scores |
| `consignee_profiles` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Each org builds its own consignee reputation scores |
| `route_risk_profiles` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Route fraud rates are org-specific observations |
| `hs_code_baselines` | Add `organization_id TEXT NOT NULL DEFAULT '__system__'` | Value baselines reflect org's own commodity mix |

**The `__system__` sentinel:** existing rows written before auth was introduced are assigned a sentinel organization ID `__system__`. These rows are effectively unreachable once auth is enforced because no JWT will carry `sub: __system__`. This is intentional — legacy data does not automatically become visible to any registered organization. Operators who want to migrate legacy data to a specific organization can do so manually via a one-time script.

**New composite primary keys / unique constraints:** Several tables currently use `shipper_key` or `route_key` as unique identifiers. These must become `(organization_id, shipper_key)` pairs, because two different organizations may have independent data about the same shipper.

```sql
-- Example: shipper_profiles current unique constraint (conceptual)
-- BEFORE: UNIQUE(shipper_key)
-- AFTER:  UNIQUE(organization_id, shipper_key)

-- Route risk profiles
-- BEFORE: UNIQUE(route_key)  where route_key = "CN|Los Angeles"
-- AFTER:  UNIQUE(organization_id, route_key)

-- HS code baselines
-- BEFORE: UNIQUE(hs_prefix)
-- AFTER:  UNIQUE(organization_id, hs_prefix)
```

**New indexes for isolation performance:**

```sql
-- All hot query paths filter by organization_id first
CREATE INDEX idx_shipment_history_org ON shipment_history(organization_id, analyzed_at DESC);
CREATE INDEX idx_shipper_profiles_org ON shipper_profiles(organization_id, shipper_key);
CREATE INDEX idx_consignee_profiles_org ON consignee_profiles(organization_id, consignee_key);
CREATE INDEX idx_route_risk_org ON route_risk_profiles(organization_id, route_key);
CREATE INDEX idx_hs_baselines_org ON hs_code_baselines(organization_id, hs_prefix);
CREATE INDEX idx_pattern_outcomes_org ON pattern_outcomes(organization_id, analysis_id);
```

### 3.5 Complete Schema: Auth DB (`portguard_auth.db`)

The three auth tables (`organizations`, `auth_refresh_tokens`, `auth_login_attempts`) live in a separate SQLite file from the pattern data. This enforces a clean separation of concerns and means auth schema changes never risk the pattern data and vice versa.

The `portguard_patterns.db` receives only the `organization_id` column additions via migration.

---

## 4. API Changes — Endpoints and Middleware

### 4.1 New Auth Endpoints

All auth endpoints live under `/api/v1/auth/`. They are the only routes that do not require a valid JWT.

---

**`POST /api/v1/auth/register`**

Create a new organization account.

```
Request body:
{
  "org_name": "Acme Imports LLC",      // 2–200 characters
  "email": "admin@acme.com",           // valid email format, lowercased before storage
  "password": "s3cur3P@ssword!"        // min 8 chars; validated but never stored
}

Success response — HTTP 201:
{
  "organization_id": "org_01J8F3...",
  "org_name": "Acme Imports LLC",
  "email": "admin@acme.com",
  "created_at": "2026-04-10T14:30:00Z"
}

Error responses:
  HTTP 400 — validation failure:
    { "error": "Password must be at least 8 characters.", "code": "WEAK_PASSWORD" }
    { "error": "Invalid email address.", "code": "INVALID_EMAIL" }
    { "error": "org_name is required.", "code": "MISSING_FIELD" }
  HTTP 409 — email already registered:
    { "error": "An account with this email already exists.", "code": "EMAIL_EXISTS" }
```

**Implementation notes:**
- Hash the password with bcrypt (work factor 12) before any database write.
- Generate `organization_id` as a UUID prefixed with `org_` inside the application — never let the client set it.
- The registration response does **not** include tokens. The user must log in as a second step. This separates account creation from session establishment.
- Email is normalized to lowercase before uniqueness check and storage.

---

**`POST /api/v1/auth/login`**

Exchange credentials for tokens. Subject to rate limiting.

```
Request body:
{
  "email": "admin@acme.com",
  "password": "s3cur3P@ssword!"
}

Success response — HTTP 200:
{
  "access_token": "<JWT>",
  "refresh_token": "<opaque 64-char string>",
  "token_type": "bearer",
  "expires_in": 86400,
  "org_name": "Acme Imports LLC",
  "organization_id": "org_01J8F3..."
}

Error responses:
  HTTP 401 — wrong credentials (always use this message; never distinguish "email not found" from "wrong password"):
    { "error": "Invalid email or password.", "code": "INVALID_CREDENTIALS" }
  HTTP 429 — rate limited:
    { "error": "Too many login attempts. Try again in 60 seconds.", "code": "RATE_LIMITED" }
    (Include header: Retry-After: 60)
  HTTP 423 — account locked (after 10 consecutive account-level failures, separate from IP rate limit):
    { "error": "Account temporarily locked. Try again in 15 minutes.", "code": "ACCOUNT_LOCKED" }
```

**Implementation notes:**
- Rate limit check (IP-based, 5 failures/minute) runs **before** any database credential lookup to avoid timing side-channels and to make denial-of-service enumeration expensive.
- Use `passlib`'s `CryptContext.verify()` — it is constant-time by design, preventing timing attacks.
- On successful login: reset `failed_login_count`, set `last_login_at`, insert a new `auth_refresh_tokens` row, record a success in `auth_login_attempts`.
- On failed login: increment `failed_login_count`, record a failure in `auth_login_attempts`. After 10 consecutive failures on the same account (regardless of IP), set `locked_until = now + 15 minutes`.
- Even if the email does not exist, still call `bcrypt.verify()` against a dummy hash before returning 401. This prevents timing-based email enumeration — a real-world attack that works when `"no such user"` returns in 0.1ms and `"wrong password"` returns in 250ms.

---

**`POST /api/v1/auth/refresh`**

Exchange a refresh token for a new access token and new refresh token.

```
Request body:
{
  "refresh_token": "<opaque 64-char string>"
}

Success response — HTTP 200:
{
  "access_token": "<new JWT>",
  "refresh_token": "<new opaque string>",
  "token_type": "bearer",
  "expires_in": 86400
}

Error responses:
  HTTP 401 — token not found, already revoked, or expired:
    { "error": "Refresh token is invalid or expired.", "code": "INVALID_REFRESH_TOKEN" }
```

**Implementation notes:**
- Hash the incoming token with SHA-256, look it up in `auth_refresh_tokens` where `revoked_at IS NULL`.
- Check `expires_at > now`. If expired, mark it revoked (reason: `"expired"`) and return 401.
- On valid token: mark old row `revoked_at = now, revoked_reason = "rotation"`, insert new refresh token row, issue new access token.
- Suspicious reuse detection: if the token is found but `revoked_at IS NOT NULL` with reason `"rotation"`, a replay attack may be in progress. Optionally: revoke all active refresh tokens for that organization and force re-login.

---

**`POST /api/v1/auth/logout`**

Revoke the current refresh token. Requires a valid access token.

```
Request headers:
  Authorization: Bearer <access_token>

Request body:
{
  "refresh_token": "<opaque string>"    // the refresh token to revoke
}

Success response — HTTP 200:
{
  "message": "Logged out successfully."
}

Error responses:
  HTTP 401 — missing or invalid access token:
    { "error": "Authentication required.", "code": "UNAUTHORIZED" }
```

**Implementation notes:**
- The access token is verified first (via the standard auth middleware).
- The refresh token to revoke must belong to the same `organization_id` extracted from the access token — preventing one org from logging out another.
- Mark the refresh token row `revoked_at = now, revoked_reason = "logout"`.
- Access tokens cannot be revoked (they are stateless). After logout, the access token remains technically valid until its `exp`. The client must discard it immediately. The 24-hour window is the maximum exposure; for high-security requirements this could be reduced to 1 hour.

---

**`GET /api/v1/auth/me`**

Return the authenticated organization's profile. Useful for the frontend to populate the header after a token refresh.

```
Request headers:
  Authorization: Bearer <access_token>

Success response — HTTP 200:
{
  "organization_id": "org_01J8F3...",
  "org_name": "Acme Imports LLC",
  "email": "admin@acme.com",
  "created_at": "2026-04-10T14:30:00Z",
  "last_login_at": "2026-04-10T14:35:00Z"
}
```

### 4.2 Protected Endpoints — JWT Middleware

All existing endpoints except `/api/v1/health` must require a valid JWT. This is implemented as a FastAPI dependency, not inline code in each route handler.

**Dependency signature:**
```
get_current_org(token: str = Depends(oauth2_scheme)) -> Organization
```

The dependency:
1. Extracts the `Authorization: Bearer <token>` header.
2. Decodes and verifies the JWT signature using `JWT_SECRET_KEY`.
3. Checks `exp` claim — rejects expired tokens.
4. Looks up the `organization_id` (`sub` claim) in the `organizations` table to confirm the account is still active (`is_active = 1`). This is the one DB lookup per request; it is fast (single PK lookup by indexed UUID).
5. Returns an `Organization` object that route handlers use to scope all DB queries.

**If validation fails at any step:**
```
HTTP 401
WWW-Authenticate: Bearer
{ "error": "Authentication required.", "code": "UNAUTHORIZED" }
```

Always return 401, never 403, when the token is missing or invalid. Reserve 403 for "authenticated but not permitted" scenarios (e.g., a future roles system).

**Endpoint protection matrix:**

| Endpoint | Auth required | Notes |
|---|---|---|
| `GET /api/v1/health` | No | Liveness probe — must remain public |
| `GET /demo` | No | Demo HTML loads first, then authenticates via JS |
| `POST /api/v1/auth/register` | No | Account creation is pre-auth |
| `POST /api/v1/auth/login` | No | Credential exchange is pre-auth |
| `POST /api/v1/auth/refresh` | No | Refresh token is the credential |
| `POST /api/v1/auth/logout` | **Yes** | Requires access token to identify org |
| `GET /api/v1/auth/me` | **Yes** | |
| `POST /api/v1/analyze` | **Yes** | |
| `POST /api/v1/analyze/upload` | **Yes** | |
| `POST /api/v1/feedback` | **Yes** | Outcome must be scoped to org |
| `GET /api/v1/pattern-history` | **Yes** | Must return only org's own history |
| `POST /api/v1/screen` | **Yes** | Structured pipeline |
| `POST /api/v1/parse` | **Yes** | |
| `POST /api/v1/classify` | **Yes** | |
| `POST /api/v1/assess-risk` | **Yes** | |
| `GET /api/v1/reports/{id}` | **Yes** | Must verify report belongs to org |

### 4.3 Rate Limiting Implementation

Use `slowapi` (a Starlette/FastAPI-compatible port of Flask-Limiter) with the client IP as the rate limit key.

**Configuration:**
```
POST /api/v1/auth/login    — 5 per minute per IP
POST /api/v1/auth/register — 10 per hour per IP (prevent account creation spam)
POST /api/v1/auth/refresh  — 30 per minute per IP (token refresh should be infrequent)
```

The rate limiter state is backed by the `auth_login_attempts` table for the login endpoint (so limits survive restarts) and in-memory for the others (lower stakes, simpler implementation).

When the limit is exceeded, `slowapi` returns HTTP 429 with a `Retry-After` header. The client should respect this header.

---

## 5. Frontend Changes

### 5.1 Token Storage Strategy

The spec requires tokens stored **in memory, not localStorage**. This is the correct security posture:

- `localStorage` is accessible to any JavaScript running on the page, making it vulnerable to XSS attacks.
- In-memory storage (a JavaScript module-level variable) is not accessible to injected scripts from other origins.
- Trade-off: the token is lost on page refresh. Users must re-authenticate after a hard refresh.

**Implementation pattern — JS module singleton:**

```javascript
// auth.js (conceptual module)
let _accessToken = null;
let _refreshToken = null;
let _orgName = null;
let _orgId = null;

export function setTokens(accessToken, refreshToken, orgName, orgId) { ... }
export function getAccessToken() { return _accessToken; }
export function isAuthenticated() { return _accessToken !== null; }
export function clearTokens() { _accessToken = _refreshToken = _orgName = _orgId = null; }
```

**Refresh on page load:** On `DOMContentLoaded`, the app checks `isAuthenticated()`. If false, it shows the login screen. There is no automatic re-authentication on refresh — the user must log in again. This is acceptable for a compliance tool used in controlled environments. If seamless re-auth is required in a future iteration, `refresh_token` can be stored in an `HttpOnly, Secure, SameSite=Strict` cookie (which is inaccessible to JavaScript), but this requires the API and frontend to be served from the same origin.

### 5.2 UI State Machine

```
App load
  │
  ├─ isAuthenticated() = false ──► Show LOGIN SCREEN
  │                                    │
  │                                    ├─ Submit login form
  │                                    │     POST /api/v1/auth/login
  │                                    │     ├─ Success → setTokens(), show MAIN APP
  │                                    │     └─ Failure → show error message
  │                                    │
  │                                    └─ "Create account" link → Show REGISTER SCREEN
  │                                           │
  │                                           ├─ Submit register form
  │                                           │     POST /api/v1/auth/register
  │                                           │     ├─ Success → redirect to LOGIN SCREEN
  │                                           │     └─ Failure → show field-level errors
  │                                           │
  │                                           └─ "Back to login" link
  │
  └─ isAuthenticated() = true  ──► Show MAIN APP
                                       │
                                       ├─ All API calls include Authorization header
                                       │
                                       ├─ On any 401 response → clearTokens(), show LOGIN SCREEN
                                       │
                                       └─ Logout button → POST /api/v1/auth/logout
                                                          clearTokens()
                                                          show LOGIN SCREEN
```

### 5.3 Login Screen

Replaces the current `demo.html` main content when no token is present. Minimal, functional:

- Fields: Email, Password
- Button: "Sign In"
- Link: "Create an account" → switch to register screen
- Error display area (inline, beneath the form): shows messages like "Invalid email or password." or "Too many attempts. Try again in 60 seconds."
- On submit: disable the button and show a spinner until response arrives (prevents double-submission)
- Keyboard: `Enter` key submits the form

### 5.4 Registration Screen

- Fields: Company Name, Email, Password, Confirm Password
- Client-side validation before submission:
  - All fields required
  - Valid email format
  - Password ≥ 8 characters
  - Password and Confirm Password match
- Error display: field-level inline errors + a general error area for server-returned errors
- On success: show a success message ("Account created. Please sign in.") and switch to login screen — do not auto-login, per the design decision in §4.1

### 5.5 Authenticated App Header

When logged in, the top section of `demo.html` must show:
- Company name (from token claims or `/api/v1/auth/me` response): `"Acme Imports LLC"`
- Email (optional, smaller text)
- Logout button (top-right corner)

The company name and email are available in the JWT payload — no extra API call is needed to display them. Read them from the decoded token claims after login.

### 5.6 Authenticated API Request Wrapper

All `fetch()` calls in `demo.html` must be replaced with a wrapper that automatically adds the `Authorization` header and handles 401 responses:

```javascript
// Conceptual wrapper
async function apiRequest(url, options = {}) {
  const token = getAccessToken();
  if (!token) {
    showLoginScreen();
    throw new Error("Not authenticated");
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });

  if (response.status === 401) {
    clearTokens();
    showLoginScreen();
    throw new Error("Session expired. Please log in again.");
  }

  return response;
}
```

This single wrapper ensures every API call is authenticated and all session expirations are handled uniformly.

---

## 6. Data Isolation — Enforcement Model

### 6.1 Defense in Depth

Data isolation is enforced at **three independent layers**. Failing at any single layer does not expose another organization's data if the other two hold:

1. **JWT claim layer** — the access token carries `organization_id`. The auth middleware extracts and validates this claim before any route handler runs.

2. **Application layer** — every `PatternDB` method, every query in `api/app.py`, and every route handler in `portguard/api/routes.py` receives `organization_id` as a required parameter and passes it to the database layer.

3. **Database layer** — every SQL query that touches multi-tenant tables includes `WHERE organization_id = ?` as a parameter. No query can return rows from another organization because the SQL itself is scoped.

### 6.2 PatternDB Method Signature Changes

Every public method on `PatternDB` that reads or writes organization-specific data gains an `organization_id` parameter. There is no default value — callers are forced to be explicit.

```
# Current signatures (no isolation):
PatternDB.record_shipment(fingerprint, decision, rules_fired, confidence)
PatternDB.record_outcome(analysis_id, outcome, officer_id, notes, case_ref)
PatternDB.get_shipper_profile(shipper_name)
PatternDB.get_consignee_profile(consignee_name)
PatternDB.get_route_risk(origin, destination)
PatternDB.get_hs_baseline(hs_code_prefix)

# New signatures (explicit organization_id, no default):
PatternDB.record_shipment(organization_id, fingerprint, decision, rules_fired, confidence)
PatternDB.record_outcome(organization_id, analysis_id, outcome, officer_id, notes, case_ref)
PatternDB.get_shipper_profile(organization_id, shipper_name)
PatternDB.get_consignee_profile(organization_id, consignee_name)
PatternDB.get_route_risk(organization_id, origin, destination)
PatternDB.get_hs_baseline(organization_id, hs_code_prefix)
```

### 6.3 PatternEngine Propagation

`PatternEngine.score(request: ScoringRequest)` becomes `PatternEngine.score(organization_id: str, request: ScoringRequest)`. The `organization_id` is threaded through to every `PatternDB` call inside the engine.

### 6.4 Report Store Isolation

The current `reports_store: dict[str, ScreeningReport]` in `portguard/api/routes.py` is keyed by `report_id` only. After auth is added, `GET /api/v1/reports/{report_id}` must verify that the report's embedded `organization_id` matches the requesting org:

```python
report = reports_store.get(report_id)
if not report:
    raise HTTPException(404, ...)
if report.organization_id != current_org.organization_id:
    raise HTTPException(404, ...)  # Return 404, not 403 — don't confirm existence
```

Return 404 (not 403) when a valid organization requests another org's report. Returning 403 would confirm the report ID exists, which is an information leak.

### 6.5 Isolation Test Invariants

The following invariants must be enforced by integration tests after implementation:

1. Organization A's shipper profile for "Dragon Phoenix Trading" is invisible to Organization B, even if B has analyzed shipments from the same shipper.
2. Organization A's `GET /api/v1/pattern-history` returns zero results when Organization B has data but A does not.
3. Organization A cannot access Organization B's report by guessing or knowing the `report_id`.
4. The `POST /api/v1/feedback` endpoint, when called by Organization A with a `shipment_id` that belongs to Organization B, returns 404.
5. A JWT signed with the correct secret but carrying a non-existent `organization_id` in `sub` returns 401.

---

## 7. Security Requirements and Controls

### 7.1 Password Policy

| Requirement | Implementation |
|---|---|
| Minimum 8 characters | Validated in `POST /api/v1/auth/register` before any DB operation |
| No maximum length (bcrypt handles up to 72 bytes) | Do not impose an artificial upper bound |
| No "complexity rules" (uppercase + number + symbol) | Complexity rules reduce entropy in practice; minimum length is sufficient |
| Common password rejection (optional, future) | Check against a top-10,000 passwords list |

Passwords are validated at the API layer **before** bcrypt hashing — invalid passwords never touch the hash function.

### 7.2 Token Expiry

| Token | Expiry | Rationale |
|---|---|---|
| Access token | 24 hours | Long enough to avoid constant friction; short enough to limit exposure window |
| Refresh token | 7 days | Covers a standard work week; forces re-login if idle for a week |
| Account lockout | 15 minutes | After 10 consecutive failed attempts on same account |

### 7.3 Rate Limiting

| Endpoint | Limit | Window | Key | Response |
|---|---|---|---|---|
| `POST /api/v1/auth/login` | 5 failures | 60 seconds | Client IP | HTTP 429 + `Retry-After: 60` |
| `POST /api/v1/auth/register` | 10 requests | 1 hour | Client IP | HTTP 429 |
| `POST /api/v1/auth/refresh` | 30 requests | 60 seconds | Client IP | HTTP 429 |

**IP extraction note:** when deployed behind a reverse proxy (nginx, Cloudflare), the real client IP is in `X-Forwarded-For` or `CF-Connecting-IP`. The rate limiter must be configured to trust these headers only from known proxy IPs — otherwise any client can spoof their IP and bypass the limit entirely.

### 7.4 Sensitive Route Responses

All protected routes return exactly this response when the token is missing or invalid — no variation, no additional detail:

```
HTTP 401
WWW-Authenticate: Bearer
Content-Type: application/json

{ "error": "Authentication required.", "code": "UNAUTHORIZED" }
```

This uniform response prevents information leakage about why authentication failed (expired vs. invalid vs. wrong signature).

### 7.5 Credential Handling Rules

- Passwords are **never** logged. Ensure no FastAPI request logging middleware captures request bodies on the auth endpoints.
- The raw refresh token is **never** stored — only its SHA-256 hash. It is sent to the client exactly once.
- The raw API key (for programmatic access) is shown to the user exactly once at generation time. After that, only the hash and display prefix are stored.
- `JWT_SECRET_KEY` must never appear in logs, error messages, or tracebacks. If the key is accidentally logged, rotate it immediately and revoke all active sessions.
- The `Authorization` header must be excluded from any access log format used in production.

### 7.6 HTTPS Requirement

Auth cannot be safely deployed over plain HTTP. JWT tokens and refresh tokens sent over HTTP are trivially intercepted. The `Procfile` currently targets a PaaS (Render) which enforces HTTPS at the load balancer level — this is sufficient. Any local development setup must document that auth is insecure over HTTP and is for development only.

### 7.7 CORS

The existing CORS middleware in `api/app.py` currently allows all origins (`allow_origins=["*"]`). After auth is added, this must be restricted to the actual frontend origin in production. During development, `localhost` origins are acceptable. Wildcard CORS with credentials is a security vulnerability.

```python
# Development
allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"]

# Production
allow_origins=["https://your-production-domain.com"]

# Never in production
allow_origins=["*"]  # ← must not be used when credentials are in play
```

---

## 8. Migration Strategy

### 8.1 Schema Migration Approach

PORTGUARD's `pattern_db.py` already implements a forward-only migration system via the `schema_migrations` table. The same mechanism applies here. Migrations are identified by name, checked for prior application, and run exactly once.

**Migration sequence for auth:**

```
Migration 001_create_auth_tables
  — Creates organizations, auth_refresh_tokens, auth_login_attempts in portguard_auth.db
  — Idempotent: wrapped in CREATE TABLE IF NOT EXISTS

Migration 002_add_organization_id_to_pattern_tables
  — ALTER TABLE shipment_history ADD COLUMN organization_id TEXT NOT NULL DEFAULT '__system__'
  — (repeated for all six affected tables)
  — Creates all organization_id indexes
  — Updates unique constraints (DROP old, CREATE new composite)
  — Applied to portguard_patterns.db

Migration 003_pattern_table_composite_keys
  — Rebuilds unique indexes as (organization_id, entity_key) composites
  — SQLite does not support ALTER TABLE DROP CONSTRAINT — tables must be rebuilt
  — Uses CREATE TABLE + INSERT + DROP + RENAME pattern
```

**SQLite composite key rebuild procedure (for Migration 003):**

SQLite does not support `DROP INDEX` on indexes created via `UNIQUE` column constraints — they must be handled by recreating the table. The standard procedure:

```sql
-- 1. Create new table with correct schema
CREATE TABLE shipper_profiles_new (
    organization_id TEXT NOT NULL,
    shipper_key     TEXT NOT NULL,
    ...
    UNIQUE(organization_id, shipper_key)
);
-- 2. Copy data
INSERT INTO shipper_profiles_new SELECT '__system__', * FROM shipper_profiles;
-- 3. Drop old table
DROP TABLE shipper_profiles;
-- 4. Rename
ALTER TABLE shipper_profiles_new RENAME TO shipper_profiles;
-- 5. Recreate indexes
CREATE INDEX idx_shipper_profiles_org ON shipper_profiles(organization_id, shipper_key);
```

### 8.2 Existing Data Handling

The `portguard_patterns.db` file currently exists in the repo with real data. After migration:
- All existing rows get `organization_id = '__system__'`
- This data is effectively quarantined — no authenticated org can access it
- An operator who wants to preserve this data can run: `UPDATE shipment_history SET organization_id = 'org_XXXXX' WHERE organization_id = '__system__'` after identifying the target organization

### 8.3 Startup Validation

On application startup, `AuthDB.__init__()` must:
1. Verify `JWT_SECRET_KEY` is set in environment — crash with `SystemExit` and a clear message if not
2. Run any pending schema migrations
3. Log the count of registered organizations (INFO level)

```
[startup] JWT_SECRET_KEY loaded (first 4 chars: 9f3a...)
[startup] Auth DB initialized at portguard_auth.db
[startup] Schema migrations: 3 applied, 0 pending
[startup] Registered organizations: 4
```

---

## 9. Threat Model and Attack Surface

### 9.1 Identified Threats and Mitigations

| Threat | Attack Vector | Mitigation |
|---|---|---|
| Credential stuffing | Automated login attempts with leaked credential lists | IP rate limiting (5/min), account lockout (10 attempts) |
| Password brute force | Targeted guessing against one account | Account lockout after 10 failures (15 min), bcrypt work factor 12 |
| JWT forgery | Crafting tokens with arbitrary claims | HS256 with 256-bit secret; secret never exposed |
| JWT replay | Reusing a captured valid token | Short 24h expiry; HTTPS in production prevents capture |
| Refresh token theft | Token stolen from client storage | In-memory storage (not localStorage); HTTPS |
| Refresh token replay | Stolen token used after rotation | Rotation detection: revoked token reuse triggers session revocation |
| Cross-organization data access | Querying another org's data by ID | Triple-layer isolation (JWT claim, application, SQL) |
| Email enumeration | Timing difference between "user not found" and "wrong password" | Dummy bcrypt verify when email not found |
| Account enumeration via registration | Check if an email is registered | Registration returns 409 — this is unavoidable for UX; acceptable trade-off |
| XSS token theft | Script injection reading tokens | In-memory storage (inaccessible to injected scripts) |
| CSRF | Cross-site form submission | JWT in Authorization header (not cookies) is not CSRF-vulnerable by default |
| SQL injection | Malicious input in query parameters | All queries use parameterized statements (already enforced by sqlite3 API) |
| Timing side-channel on login | Measuring response time to enumerate users | Constant-time bcrypt.verify() even for non-existent users |

### 9.2 Out of Scope (Documented Limitations)

- **Email verification:** accounts can be created with unverified email addresses. For a compliance tool used in B2B contexts, this is acceptable in v1; add email verification in a future sprint.
- **Multi-user organizations:** this architecture supports one set of credentials per organization. Future work: add an `org_users` table mapping individual users to organizations with role-based permissions (admin, analyst, read-only).
- **Audit logging of compliance decisions:** the pattern DB records decisions, but there is no separate audit log of which user triggered each analysis. Future work: add `triggered_by_user_id` to `shipment_history`.
- **Key rotation:** rotating `JWT_SECRET_KEY` invalidates all active access tokens. A dual-key rotation strategy (support old key during grace period) is future work.

---

## 10. Implementation Sequence

The auth system should be built in this order to maintain a working application at each step:

**Phase 1 — Auth DB and data model (no breaking changes)**
1. Create `portguard/auth_db.py` — `AuthDB` class with all three auth tables
2. Write and run Migration 002 — add `organization_id` columns to pattern tables (with `DEFAULT '__system__'`)
3. Write and run Migration 003 — rebuild composite unique keys
4. Update `PatternDB` method signatures to require `organization_id` (with backward-compat `organization_id='__system__'` default during transition — remove default after auth is wired up)
5. Write unit tests for `AuthDB` (register, login, token operations, rate limiting)

**Phase 2 — Auth endpoints (additive, no existing endpoints broken)**
1. Create `portguard/auth/` module: `models.py`, `tokens.py`, `passwords.py`, `rate_limiter.py`
2. Add `/api/v1/auth/register`, `/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/logout`, `/api/v1/auth/me` endpoints
3. Add `get_current_org` FastAPI dependency
4. Write integration tests for all auth endpoints
5. Verify startup fails if `JWT_SECRET_KEY` is missing

**Phase 3 — Protect existing endpoints**
1. Apply `get_current_org` dependency to all non-public routes in both `api/app.py` and `portguard/api/routes.py`
2. Thread `organization_id` through all `PatternDB` and `PatternEngine` calls
3. Update `reports_store` access to enforce org scoping
4. Remove `organization_id='__system__'` defaults from `PatternDB`
5. Run full test suite — all existing tests must pass with mocked auth

**Phase 4 — Frontend auth**
1. Add auth module to `demo.html`
2. Implement login and register screens
3. Replace raw `fetch()` calls with authenticated wrapper
4. Add company name to header and logout button
5. Manual end-to-end test: register two organizations, verify data isolation

**Phase 5 — Hardening**
1. Restrict CORS origins
2. Ensure password fields excluded from request logging
3. Add integration tests for cross-organization isolation invariants (§6.5)
4. Load test the rate limiter
5. Document `JWT_SECRET_KEY` rotation procedure in a runbook

---

*This document covers the complete technical design. No code is written until this architecture is reviewed and approved. Implementation follows Phase 1 → Phase 5 sequentially.*
