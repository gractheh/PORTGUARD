"""
portguard/auth.py — Authentication module for PortGuard multi-tenant system.

Provides:
- Password hashing via bcrypt (work factor 12)
- JWT access token creation and verification (HS256, 24h expiry)
- AuthDB: SQLAlchemy-backed storage for organizations, token revocations,
  and login rate-limiting.  Uses PostgreSQL when DATABASE_URL is set;
  falls back to SQLite for local development.
- get_current_organization: FastAPI dependency for protected routes
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt as _bcrypt_lib

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as _IntegrityError, SQLAlchemyError as _SQLAlchemyError

from portguard.db import adapt_stmt, get_engine, split_migration_sql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — loaded from environment so keys can be rotated without
# changing code.  PORTGUARD_JWT_SECRET must be set in production; the
# random fallback is acceptable for development only.
# ---------------------------------------------------------------------------

_SECRET_KEY: str = os.getenv("PORTGUARD_JWT_SECRET") or secrets.token_hex(32)
if not os.getenv("PORTGUARD_JWT_SECRET"):
    logging.getLogger(__name__).warning(
        "PORTGUARD_JWT_SECRET is not set — a random JWT signing key is being used. "
        "All active sessions will be invalidated on every server restart. "
        "Set PORTGUARD_JWT_SECRET in your environment for persistent authentication."
    )
_ALGORITHM: str = "HS256"
_ACCESS_TOKEN_EXPIRE_HOURS: int = 24

# bcrypt work factor 12 — NIST-acceptable; adjust upward as hardware improves.
_BCRYPT_ROUNDS: int = 12

# A static dummy hash used to normalize timing on non-existent-user logins.
# Pre-generated so verify_password_safe doesn't vary based on hash generation.
_DUMMY_HASH: bytes = _bcrypt_lib.hashpw(b"dummy", _bcrypt_lib.gensalt(rounds=_BCRYPT_ROUNDS))

# HTTPBearer extractor — auto_error=False so we can return a clean 401 ourselves.
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt (work factor 12).

    Returns
    -------
    str
        bcrypt hash string (UTF-8 decoded) suitable for storage.
    """
    hashed = _bcrypt_lib.hashpw(plain.encode("utf-8"), _bcrypt_lib.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    return _bcrypt_lib.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def verify_password_safe(plain: str, hashed: Optional[str]) -> bool:
    """Timing-safe password verification that prevents user enumeration.

    When hashed is None (user does not exist), still runs bcrypt against
    a dummy hash to keep response time constant regardless of whether the
    email exists.

    Parameters
    ----------
    plain:
        The plaintext password from the login request.
    hashed:
        The stored bcrypt hash, or None if the user was not found.

    Returns
    -------
    bool
        True only if hashed is not None and the password matches.
    """
    if hashed is None:
        _bcrypt_lib.checkpw(plain.encode("utf-8"), _DUMMY_HASH)
        return False
    return _bcrypt_lib.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT utilities
# ---------------------------------------------------------------------------


def create_access_token(org_id: str, org_name: str, email: str) -> tuple[str, str]:
    """Create a signed JWT access token for an organization.

    Returns
    -------
    (token_str, jti)
        The encoded JWT string and its unique identifier.
    """
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS)

    payload = {
        "sub": org_id,
        "org_name": org_name,
        "email": email,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)
    return token, jti


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token.

    Returns the decoded payload dict on success.

    Raises
    ------
    HTTPException (401)
        If the token is expired, has an invalid signature, or is malformed.
    """
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token is invalid or expired."},
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# AuthDB schema — dialect-neutral DDL (adapted at init time)
# ---------------------------------------------------------------------------

_AUTH_SCHEMA_STMTS: list[str] = [
    """CREATE TABLE IF NOT EXISTS organizations (
        organization_id     TEXT PRIMARY KEY,
        org_name            TEXT NOT NULL,
        email               TEXT NOT NULL UNIQUE,
        password_hash       TEXT NOT NULL,
        is_active           INTEGER NOT NULL DEFAULT 1,
        created_at          TEXT NOT NULL,
        last_login_at       TEXT,
        failed_login_count  INTEGER NOT NULL DEFAULT 0,
        locked_until        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS auth_token_revocations (
        jti         TEXT PRIMARY KEY,
        revoked_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL
    )""",
    # id uses AUTOINCREMENT for SQLite; adapt_stmt converts to BIGSERIAL for PG
    """CREATE TABLE IF NOT EXISTS auth_login_attempts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address      TEXT NOT NULL,
        attempted_at    TEXT NOT NULL,
        succeeded       INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_revocations_jti ON auth_token_revocations(jti)",
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_at ON auth_login_attempts(ip_address, attempted_at)",
    "CREATE INDEX IF NOT EXISTS idx_orgs_email ON organizations(email)",
]


# ---------------------------------------------------------------------------
# AuthDB class
# ---------------------------------------------------------------------------


class AuthDB:
    """SQLAlchemy-backed storage for organizations and token management.

    Uses PostgreSQL when DATABASE_URL is set; falls back to SQLite at
    *db_path* for local development.

    Thread safety
    -------------
    SQLAlchemy's connection pool handles concurrency.  Each write operation
    uses ``engine.begin()`` for automatic commit/rollback semantics.
    """

    def __init__(self, db_path: str = "portguard_auth.db") -> None:
        self._engine, self._dialect = get_engine(db_path)
        self._init_schema()
        logger.info(
            "AuthDB initialized (dialect=%s, path=%s)",
            self._dialect,
            db_path if self._dialect == "sqlite" else "postgresql",
        )

    def _init_schema(self) -> None:
        with self._engine.begin() as conn:
            for stmt in _AUTH_SCHEMA_STMTS:
                adapted = adapt_stmt(stmt, self._dialect)
                if adapted:
                    conn.execute(text(adapted))

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Organization management
    # ------------------------------------------------------------------

    def create_organization(
        self, org_name: str, email: str, password_hash: str
    ) -> str:
        """Create a new organization account.

        Returns
        -------
        str
            The new organization's UUID.

        Raises
        ------
        ValueError
            If the email address is already registered.
        RuntimeError
            On unexpected database errors.
        """
        org_id = str(uuid.uuid4())
        now = self._utcnow()

        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """INSERT INTO organizations
                           (organization_id, org_name, email, password_hash, created_at)
                           VALUES (:org_id, :org_name, :email, :password_hash, :created_at)"""
                    ),
                    {
                        "org_id": org_id,
                        "org_name": org_name,
                        "email": email.lower().strip(),
                        "password_hash": password_hash,
                        "created_at": now,
                    },
                )
            return org_id
        except _IntegrityError:
            raise ValueError(f"The email '{email}' is already registered.")
        except _SQLAlchemyError as exc:
            raise RuntimeError(f"Failed to create organization: {exc}") from exc

    def get_organization_by_email(self, email: str) -> Optional[Any]:
        """Look up an organization by email address.  Returns row or None."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text("SELECT * FROM organizations WHERE email = :email"),
                {"email": email.lower().strip()},
            )
            return result.mappings().fetchone()

    def get_organization_by_id(self, org_id: str) -> Optional[Any]:
        """Look up an organization by its UUID.  Returns row or None."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text("SELECT * FROM organizations WHERE organization_id = :org_id"),
                {"org_id": org_id},
            )
            return result.mappings().fetchone()

    def update_last_login(self, org_id: str) -> None:
        """Record a successful login timestamp and reset the failed login counter."""
        now = self._utcnow()
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """UPDATE organizations
                       SET last_login_at = :now,
                           failed_login_count = 0,
                           locked_until = NULL
                       WHERE organization_id = :org_id"""
                ),
                {"now": now, "org_id": org_id},
            )

    # ------------------------------------------------------------------
    # Token revocation
    # ------------------------------------------------------------------

    def revoke_token(self, jti: str, expires_at: str) -> None:
        """Add a JTI to the revocation list (called on logout).  Idempotent."""
        now = self._utcnow()
        # INSERT OR IGNORE for SQLite; INSERT … ON CONFLICT DO NOTHING for PG
        raw_sql = (
            "INSERT OR IGNORE INTO auth_token_revocations "
            "(jti, revoked_at, expires_at) VALUES (:jti, :revoked_at, :expires_at)"
        )
        adapted = adapt_stmt(raw_sql, self._dialect)
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(adapted),
                    {"jti": jti, "revoked_at": now, "expires_at": expires_at},
                )
        except _SQLAlchemyError as exc:
            logger.warning("revoke_token failed: %s", exc)

    def is_token_revoked(self, jti: str) -> bool:
        """Return True if the JTI has been revoked."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text("SELECT 1 FROM auth_token_revocations WHERE jti = :jti"),
                {"jti": jti},
            )
            return result.fetchone() is not None

    def prune_expired_revocations(self) -> int:
        """Remove revocation records whose tokens have already expired."""
        now = self._utcnow()
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM auth_token_revocations WHERE expires_at < :now"),
                {"now": now},
            )
        return 0

    # ------------------------------------------------------------------
    # Login rate limiting
    # ------------------------------------------------------------------

    def record_login_attempt(self, ip_address: str, succeeded: bool) -> None:
        """Record a login attempt for per-IP rate limiting."""
        now = self._utcnow()
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """INSERT INTO auth_login_attempts
                           (ip_address, attempted_at, succeeded) VALUES (:ip, :at, :ok)"""
                    ),
                    {"ip": ip_address, "at": now, "ok": int(succeeded)},
                )
        except _SQLAlchemyError as exc:
            logger.warning("record_login_attempt failed: %s", exc)

    def count_recent_failures(self, ip_address: str, window_seconds: int = 60) -> int:
        """Count failed login attempts from an IP in the past window_seconds."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        ).isoformat()
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    """SELECT COUNT(*) FROM auth_login_attempts
                       WHERE ip_address = :ip
                         AND attempted_at >= :cutoff
                         AND succeeded = 0"""
                ),
                {"ip": ip_address, "cutoff": cutoff},
            )
            row = result.fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        """Dispose the engine connection pool."""
        try:
            self._engine.dispose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

import threading as _threading

_auth_db: Optional[AuthDB] = None
_auth_db_lock = _threading.Lock()


def get_auth_db() -> AuthDB:
    """Return the module-level AuthDB singleton, initializing on first call.

    The DB path is controlled by the PORTGUARD_AUTH_DB_PATH environment
    variable (default: ``portguard_auth.db``).  When DATABASE_URL is set to
    a PostgreSQL URL, the path is ignored and PostgreSQL is used.
    """
    global _auth_db
    if _auth_db is None:
        with _auth_db_lock:
            if _auth_db is None:
                db_path = os.getenv("PORTGUARD_AUTH_DB_PATH", "portguard_auth.db")
                _auth_db = AuthDB(db_path)
    return _auth_db


# ---------------------------------------------------------------------------
# FastAPI dependency — get_current_organization
# ---------------------------------------------------------------------------


async def get_current_organization(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """FastAPI dependency that validates the Bearer token and returns org info.

    Usage
    -----
    Add ``current_org: dict = Depends(get_current_organization)`` to any
    endpoint that requires authentication.  The returned dict contains:

        organization_id  — UUID string
        org_name         — display name
        email            — login email
        jti              — JWT unique ID (used for logout)
        exp              — token expiry timestamp (Unix int)

    Raises
    ------
    HTTPException (401)
        - No Authorization header present
        - Token is invalid, expired, or revoked
        - Organization not found in database
    HTTPException (403)
        - Organization account is deactivated
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_TOKEN", "message": "Authentication required."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token is malformed (missing jti)."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_db = get_auth_db()

    if auth_db.is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TOKEN_REVOKED",
                "message": "This token has been revoked. Please log in again.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    org_id = payload.get("sub")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token is missing subject claim."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    org = auth_db.get_organization_by_id(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ORGANIZATION_NOT_FOUND", "message": "Organization not found."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not org["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORGANIZATION_INACTIVE",
                "message": "This organization account has been deactivated.",
            },
        )

    return {
        "organization_id": org["organization_id"],
        "org_name": org["org_name"],
        "email": org["email"],
        "jti": jti,
        "exp": payload.get("exp"),
    }
