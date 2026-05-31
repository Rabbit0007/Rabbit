"""Authentication router.

This is an additive router that exposes the ``/api/auth`` endpoints. Task 1.3
implements the registration endpoint; login, logout, ``me``, and password change
endpoints are added by subsequent tasks on this same router.

The router relies on the ``users``, ``sessions``, and ``login_attempts`` tables
created by :mod:`cairn.server.auth_db` and the request/response models defined in
:mod:`cairn.server.auth_models`.
"""

from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from cairn.server.auth_models import LoginRequest, RegisterRequest, UserResponse
from cairn.server.db import get_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])

# bcrypt work factor (requirement 1.3: minimum cost factor of 12).
BCRYPT_COST = 12

# Default session lifetime (requirement 1.1 / 3.1: defaults to 24 hours).
SESSION_DURATION = timedelta(hours=24)

# Login rate limiting (requirements 2.3, 2.4): at most 5 failed attempts per
# username within a sliding 15-minute window before the account is temporarily
# locked.
MAX_FAILED_LOGIN_ATTEMPTS = 5
RATE_LIMIT_WINDOW = timedelta(minutes=15)
CAPTCHA_DURATION = timedelta(minutes=5)

# Cookie name carrying the server-side session token. The auth middleware
# (task 2.1) reads the token from this cookie.
SESSION_COOKIE_NAME = "session_token"

# Browsers reject or refuse to resend ``Secure`` cookies on plain HTTP. Keep the
# production default secure, but allow local HTTP development origins to retain
# the session cookie when the UI runs at http://127.0.0.1 or http://localhost.
LOCAL_HTTP_COOKIE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Timestamp format used throughout the codebase (see services.utcnow).
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_CAPTCHA_CHALLENGES: dict[str, tuple[str, datetime]] = {}


def _format_timestamp(value: datetime) -> str:
    """Format a datetime using the codebase-wide UTC timestamp convention."""
    return value.strftime(_TIMESTAMP_FORMAT)


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt with cost factor 12.

    Returns the encoded bcrypt hash (which embeds the salt and cost factor) as a
    UTF-8 string suitable for storage in the ``users.password_hash`` column.
    """
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_COST))
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash.

    Returns ``True`` when the password matches. Returns ``False`` for a mismatch
    or for a malformed/invalid stored hash (``bcrypt.checkpw`` raises ``ValueError``
    for hashes it cannot parse), so callers always receive a boolean rather than an
    exception.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_session_token() -> str:
    """Generate a session token with at least 128 bits of entropy.

    ``secrets.token_hex(32)`` returns 64 hex characters representing 32 random
    bytes (256 bits of entropy), comfortably exceeding the 128-bit requirement.
    """
    return secrets.token_hex(32)


def _create_session(conn, user_id: str, now: datetime) -> str:
    """Create a session row for ``user_id`` and return the session token."""
    token = generate_session_token()
    expires_at = now + SESSION_DURATION
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, _format_timestamp(now), _format_timestamp(expires_at)),
    )
    return token


def cookie_secure_for_request(request: Request) -> bool:
    """Return whether the session cookie should use the Secure attribute."""
    host = request.url.hostname or ""
    return not (request.url.scheme == "http" and host in LOCAL_HTTP_COOKIE_HOSTS)


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    """Attach the session token as an HTTP-only SameSite=Strict cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=cookie_secure_for_request(request),
        samesite="strict",
        path="/",
    )


def _cleanup_captchas(now: datetime) -> None:
    expired = [
        captcha_id
        for captcha_id, (_answer, expires_at) in _CAPTCHA_CHALLENGES.items()
        if expires_at <= now
    ]
    for captcha_id in expired:
        _CAPTCHA_CHALLENGES.pop(captcha_id, None)


def _verify_captcha(captcha_id: str | None, captcha_answer: str | None) -> None:
    now = datetime.now(timezone.utc)
    _cleanup_captchas(now)
    if not captcha_id or captcha_answer is None:
        raise HTTPException(status_code=400, detail="验证码为必填项")
    expected = _CAPTCHA_CHALLENGES.pop(captcha_id, None)
    if expected is None:
        raise HTTPException(status_code=400, detail="验证码已过期，请刷新后重试")
    answer, expires_at = expected
    if expires_at <= now or captcha_answer.strip() != answer:
        raise HTTPException(status_code=400, detail="验证码错误，请重试")


@router.get("/captcha")
def captcha() -> dict[str, str | int]:
    """Return a short arithmetic captcha challenge for login/register forms."""
    now = datetime.now(timezone.utc)
    _cleanup_captchas(now)
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 2
    captcha_id = secrets.token_urlsafe(16)
    answer = str(left + right)
    _CAPTCHA_CHALLENGES[captcha_id] = (answer, now + CAPTCHA_DURATION)
    return {
        "captcha_id": captcha_id,
        "question": f"{left} + {right} = ?",
        "expires_in": int(CAPTCHA_DURATION.total_seconds()),
    }


@router.post("/register", response_model=UserResponse, status_code=201)
def register(body: RegisterRequest, response: Response, request: Request) -> UserResponse:
    """Register a new user.

    Input validation (username format and password length) is enforced by the
    :class:`RegisterRequest` model, which raises a 422 validation error for
    invalid input (requirements 1.4, 1.6, 1.7). This handler enforces
    case-insensitive username uniqueness (requirements 1.2, 1.5), stores the
    password as a bcrypt hash with cost 12 (requirement 1.3), creates a session
    with a 24-hour expiry, and sets an HTTP-only session cookie (requirement 1.1).
    """
    _verify_captcha(body.captcha_id, body.captcha_answer)
    username_lower = body.username.lower()
    now = datetime.now(timezone.utc)
    created_at = _format_timestamp(now)
    user_id = f"user_{uuid.uuid4().hex}"

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username_lower = ?",
            (username_lower,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Username already taken")

        password_hash = hash_password(body.password)
        try:
            conn.execute(
                """
                INSERT INTO users (id, username, username_lower, password_hash, created_at, disabled)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (user_id, body.username, username_lower, password_hash, created_at),
            )
        except sqlite3.IntegrityError:
            # The UNIQUE constraint on username_lower guards against a race where
            # two concurrent registrations both pass the existence check above.
            raise HTTPException(status_code=409, detail="Username already taken")

        token = _create_session(conn, user_id, now)

    _set_session_cookie(response, token, request)
    return UserResponse(id=user_id, username=body.username, created_at=created_at)


# A lazily-computed bcrypt hash used to equalize response timing when the
# submitted username does not exist. Performing a real verification against this
# dummy hash keeps the not-found path indistinguishable (by timing) from the
# wrong-password path, reinforcing the generic-error guarantee (requirement 2.2).
_DUMMY_PASSWORD_HASH: str | None = None


def _dummy_password_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_PASSWORD_HASH


def _count_recent_failed_attempts(conn, username_lower: str, window_start: datetime) -> int:
    """Count failed login attempts for a username within the sliding window."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS failures
        FROM login_attempts
        WHERE username_lower = ? AND success = 0 AND attempted_at >= ?
        """,
        (username_lower, _format_timestamp(window_start)),
    ).fetchone()
    return int(row["failures"])


def _record_login_attempt(username_lower: str, now: datetime, success: bool) -> None:
    """Persist a login attempt row in its own committed transaction.

    This is intentionally a separate connection scope: :func:`get_conn` rolls back
    on exception, so recording a failed attempt must be committed *before* the
    handler raises an authentication error — otherwise the rate-limit counter would
    never advance.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO login_attempts (username_lower, attempted_at, success) VALUES (?, ?, ?)",
            (username_lower, _format_timestamp(now), 1 if success else 0),
        )


@router.post("/login", response_model=UserResponse)
def login(body: LoginRequest, response: Response, request: Request) -> UserResponse:
    """Authenticate a user and create a session.

    The :class:`LoginRequest` model guarantees both fields are present and
    non-empty (422 otherwise). This handler:

    - Enforces a sliding-window rate limit of at most 5 failed attempts per
      username in 15 minutes; the 6th attempt is rejected with 429 (requirements
      2.3, 2.4).
    - Validates credentials and returns a single generic 401 for an unknown
      username, a wrong password, or a disabled account, without distinguishing
      the cause (requirements 2.2, 2.5).
    - On success, creates a session with a 24-hour expiry and sets an HTTP-only,
      Secure, SameSite=Strict cookie (requirement 2.1).
    """
    _verify_captcha(body.captcha_id, body.captcha_answer)
    username_lower = body.username.lower()
    now = datetime.now(timezone.utc)
    window_start = now - RATE_LIMIT_WINDOW

    # 1. Rate-limit check (requirements 2.3, 2.4). Read-only, so no commit needed.
    with get_conn() as conn:
        failed_count = _count_recent_failed_attempts(conn, username_lower, window_start)
    if failed_count >= MAX_FAILED_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429, detail="Too many attempts. Try again later."
        )

    # 2. Look up the user and verify the password (requirements 2.2, 2.5).
    with get_conn() as conn:
        user = conn.execute(
            """
            SELECT id, username, password_hash, created_at, disabled
            FROM users WHERE username_lower = ?
            """,
            (username_lower,),
        ).fetchone()

    if user is None:
        # Equalize timing with the valid-user path before failing.
        verify_password(body.password, _dummy_password_hash())
        authenticated = False
    else:
        authenticated = not user["disabled"] and verify_password(
            body.password, user["password_hash"]
        )

    if not authenticated:
        _record_login_attempt(username_lower, now, success=False)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 3. Success: record the attempt and create the session in one transaction.
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO login_attempts (username_lower, attempted_at, success) VALUES (?, ?, 1)",
            (username_lower, _format_timestamp(now)),
        )
        token = _create_session(conn, user["id"], now)

    _set_session_cookie(response, token, request)
    return UserResponse(
        id=user["id"], username=user["username"], created_at=user["created_at"]
    )


# ---------------------------------------------------------------------------
# Task 1.5: logout, me, and password change endpoints.
#
# These are appended after the existing endpoints (registration from task 1.3,
# login from task 1.4) to keep the additions self-contained and avoid editing
# shared import/helper lines that concurrent work also touches. The imports
# below are deliberately scoped to this section for the same reason.
# ---------------------------------------------------------------------------

from cairn.server.auth_models import PasswordChangeRequest  # noqa: E402


def _clear_session_cookie(response: Response, request: Request) -> None:
    """Remove the session cookie, matching the attributes used when setting it.

    Used on logout (requirement 3.3) and when rejecting requests carrying an
    invalid/expired session token (requirements 3.2, 3.6).
    """
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=cookie_secure_for_request(request),
        samesite="strict",
    )


def _unauthorized_response(request: Request) -> JSONResponse:
    """Build a 401 response that also clears the (invalid) session cookie."""
    response = JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
    )
    _clear_session_cookie(response, request)
    return response


def _authenticate_session(conn, token: str | None, now: datetime):
    """Return the user row for a valid, unexpired session token, else ``None``.

    A session is valid when the token exists in the ``sessions`` table and its
    ``expires_at`` is strictly in the future. This is a small local helper so it
    does not collide with the auth middleware (task 2.1) which lives in a
    separate module and owns sliding-window expiration.
    """
    if not token:
        return None
    session = conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token = ?",
        (token,),
    ).fetchone()
    if session is None:
        return None
    expires_at = datetime.strptime(session["expires_at"], _TIMESTAMP_FORMAT).replace(
        tzinfo=timezone.utc
    )
    if expires_at <= now:
        return None
    return conn.execute(
        "SELECT id, username, created_at, password_hash, disabled FROM users WHERE id = ?",
        (session["user_id"],),
    ).fetchone()


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    """Log the current user out (requirement 3.3).

    Deletes the session row keyed by the cookie token so the session is
    invalidated immediately (well within the 1-second requirement, since it is a
    single indexed DELETE), and clears the session cookie on the response.
    Logging out is idempotent: a missing or unknown token still returns 204 with
    the cookie cleared.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response = Response(status_code=204)
    _clear_session_cookie(response, request)
    return response


@router.get("/me", response_model=UserResponse)
def me(request: Request):
    """Return the current authenticated user (requirement 3.3 session lookup).

    Resolves the user from the session cookie. Missing, unknown, or expired
    tokens are rejected with a 401 and the stale cookie is cleared
    (requirements 3.2, 3.6).
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        user = _authenticate_session(conn, token, now)
    if user is None:
        return _unauthorized_response(request)
    return UserResponse(
        id=user["id"],
        username=user["username"],
        created_at=user["created_at"],
    )


@router.put("/password", status_code=204)
def change_password(body: PasswordChangeRequest, request: Request) -> Response:
    """Change the current user's password (requirements 4.1-4.4).

    The new-password policy (length + complexity) is enforced by
    :class:`PasswordChangeRequest`, which yields a 422 for non-compliant input
    (requirement 4.4). This handler verifies the supplied current password
    (requirement 4.2 — a mismatch yields 401 and leaves the stored hash
    untouched), stores the new bcrypt hash on success (requirement 4.1), and
    invalidates every other active session for the user while keeping the
    current session valid (requirement 4.3).
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        user = _authenticate_session(conn, token, now)
        if user is None:
            return _unauthorized_response(request)

        current_ok = verify_password(body.current_password, user["password_hash"])
        if not current_ok:
            raise HTTPException(status_code=401, detail="Invalid current credentials")

        new_hash = hash_password(body.new_password)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user["id"]),
        )
        # Invalidate all *other* sessions for this user; the session used to make
        # this request remains valid (requirement 4.3).
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token != ?",
            (user["id"], token),
        )

    return Response(status_code=204)
