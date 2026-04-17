"""
api/auth_routes.py — Authentication endpoints for PortGuard multi-tenant system.

Routes (all under /api/v1/auth):
    POST /register  — create a new organization account
    POST /login     — authenticate and receive a JWT access token
    POST /logout    — revoke the current token (requires valid token)
    GET  /me        — return current organization info (requires valid token)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from portguard.auth import (
    create_access_token,
    get_auth_db,
    get_current_organization,
    hash_password,
    verify_password_safe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Maximum failed login attempts per IP per minute before rate-limiting kicks in.
_MAX_FAILED_PER_MINUTE: int = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=120, description="Company or organization name")
    email: str = Field(..., min_length=5, max_length=254, description="Primary contact email address")
    password: str = Field(..., min_length=8, max_length=128, description="Account password (min 8 characters)")

    @field_validator("org_name")
    @classmethod
    def org_name_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Company name must not be blank.")
        return stripped

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Invalid email address.")
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            raise ValueError("Invalid email address.")
        return v


class RegisterResponse(BaseModel):
    organization_id: str
    org_name: str
    email: str
    message: str


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        return v.strip().lower()


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    organization_id: str
    org_name: str
    email: str


class MeResponse(BaseModel):
    organization_id: str
    org_name: str
    email: str


class LogoutResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(request: RegisterRequest) -> RegisterResponse:
    """Create a new organization account.

    All fields are required:
    - ``org_name``: company display name, 2–120 characters
    - ``email``: must be a valid email address and not already registered
    - ``password``: minimum 8 characters

    Returns the new organization's UUID and a confirmation message.

    Errors:
    - 409 CONFLICT if the email is already registered
    - 422 UNPROCESSABLE ENTITY for validation failures
    """
    auth_db = get_auth_db()

    pw_hash = hash_password(request.password)

    try:
        org_id = auth_db.create_organization(
            org_name=request.org_name,
            email=request.email,
            password_hash=pw_hash,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EMAIL_TAKEN", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("register: unexpected error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "REGISTRATION_ERROR",
                "message": "Registration failed due to an internal error. Please try again.",
            },
        )

    logger.info("New organization registered: '%s' <%s>", request.org_name, request.email)
    return RegisterResponse(
        organization_id=org_id,
        org_name=request.org_name,
        email=request.email,
        message="Account created successfully. You can now log in.",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, req: Request) -> LoginResponse:
    """Authenticate an organization and return a JWT access token.

    The token is valid for 24 hours.  Store it in JavaScript memory only —
    do NOT persist it to localStorage, sessionStorage, or cookies.

    Rate limiting: after 5 failed attempts in 60 seconds from the same IP,
    further attempts are rejected with HTTP 429.

    Errors:
    - 401 EMAIL_NOT_FOUND if no account exists for that email
    - 401 WRONG_PASSWORD if the password does not match
    - 403 ORGANIZATION_INACTIVE if the account is deactivated
    - 429 RATE_LIMITED if rate limit is exceeded
    - 500 TOKEN_ERROR if JWT creation fails unexpectedly
    """
    auth_db = get_auth_db()
    ip = (req.client.host if req.client else "unknown")

    logger.info("Login attempt | email=%s ip=%s", request.email, ip)

    # Rate limit: block on too many recent failures from this IP.
    recent_failures = auth_db.count_recent_failures(ip, window_seconds=60)
    if recent_failures >= _MAX_FAILED_PER_MINUTE:
        logger.info("Login rate-limited | ip=%s failures=%d", ip, recent_failures)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": (
                    "Too many failed login attempts from your IP address. "
                    "Please wait a minute before trying again."
                ),
            },
        )

    org = auth_db.get_organization_by_email(request.email)

    # Email not found — run a dummy bcrypt to normalize timing, then reject.
    if org is None:
        verify_password_safe(request.password, None)
        auth_db.record_login_attempt(ip, succeeded=False)
        logger.info("Login failed: email not found | email=%s", request.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "EMAIL_NOT_FOUND", "message": "No account found with that email address."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Email found — verify password.
    if not verify_password_safe(request.password, org["password_hash"]):
        auth_db.record_login_attempt(ip, succeeded=False)
        logger.info("Login failed: wrong password | email=%s", request.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "WRONG_PASSWORD", "message": "Incorrect password. Please try again."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not org["is_active"]:
        logger.info("Login failed: account inactive | email=%s", request.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORGANIZATION_INACTIVE",
                "message": "This account has been deactivated. Contact support.",
            },
        )

    auth_db.record_login_attempt(ip, succeeded=True)
    auth_db.update_last_login(org["organization_id"])

    try:
        token, _jti = create_access_token(
            org_id=org["organization_id"],
            org_name=org["org_name"],
            email=org["email"],
        )
    except Exception as exc:
        logger.error(
            "Token creation failed | email=%s error=%s", org["email"], exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "TOKEN_ERROR", "message": "Something went wrong. Please try again."},
        )

    logger.info("Login successful | email=%s org_id=%s", org["email"], org["organization_id"])
    return LoginResponse(
        access_token=token,
        organization_id=org["organization_id"],
        org_name=org["org_name"],
        email=org["email"],
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", response_model=LogoutResponse)
def logout(current_org: dict = Depends(get_current_organization)) -> LogoutResponse:
    """Revoke the current access token.

    After logout, the token is added to the server-side revocation list and
    any future requests using it will receive HTTP 401.  The client must
    discard the token from memory.

    Requires a valid Bearer token in the Authorization header.
    """
    auth_db = get_auth_db()

    jti = current_org["jti"]
    exp = current_org.get("exp")

    if exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    else:
        expires_at = datetime.now(timezone.utc).isoformat()

    auth_db.revoke_token(jti, expires_at)
    logger.info("Logout: %s", current_org["email"])

    return LogoutResponse(message="Logged out successfully. Your token has been revoked.")


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
def me(current_org: dict = Depends(get_current_organization)) -> MeResponse:
    """Return identity information for the currently authenticated organization.

    Useful for the frontend to re-hydrate the session state after a page
    refresh by validating the in-memory token against the server.

    Requires a valid Bearer token in the Authorization header.
    """
    return MeResponse(
        organization_id=current_org["organization_id"],
        org_name=current_org["org_name"],
        email=current_org["email"],
    )
