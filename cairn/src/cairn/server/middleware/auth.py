"""Authentication middleware dependency.

This is an additive module that provides :func:`require_auth`, a FastAPI
dependency used to protect the existing routers without modifying them. Task 2.2
wires it in via ``app.include_router(..., dependencies=[Depends(require_auth)])``.

Behaviour (requirements 3.1, 3.2, 3.4, 3.5, 3.6, 5.1, 5.2, 5.3, 5.4):

- Reads the session token from the ``session_token`` cookie (the same cookie set
  by :mod:`cairn.server.routers.auth`).
- Validates the token against the ``sessions`` table created by
  :mod:`cairn.server.auth_db`. A session is valid when the token exists and its
  ``expires_at`` is strictly in the future.
- On a valid session, extends the expiration by the configured period (sliding
  window, requirement 3.5), refreshes the cookie's ``Max-Age``, and injects the
  user onto ``request.state``.
- On a missing, malformed, expired, or unknown token, rejects the request and
  clears the (stale) session cookie (requirements 3.2, 3.6, 5.4):
    - browser navigation requests (``Accept: text/html``) receive a 302 redirect
      to the login page (requirement 5.3);
    - all other requests receive a 401 with a JSON ``detail`` body (requirements
      5.1, 5.4).
- Exempt paths bypass authentication entirely (requirement 5.2): the register and
  login endpoints, anything served under the ``/static`` mount, and the root path
  (which serves the login/SPA shell).

Dual authentication (dispatcher compatibility):

The dispatcher communicates with the API over HTTP using a cookieless
``requests.Session`` and hits the same endpoints as the browser UI. To protect
the UI without breaking the dispatcher — and without modifying the dispatcher
(an existing core file) — ``require_auth`` accepts EITHER a valid session cookie
(browser users) OR a valid internal-service token presented via the
``X-Cairn-Internal-Token`` header matching the ``CAIRN_INTERNAL_TOKEN``
environment variable (trusted machine clients).

To keep existing deployments and the current dispatcher working unchanged, the
internal token is OPT-IN: when ``CAIRN_INTERNAL_TOKEN`` is unset, the protected
routers preserve their pre-auth (open) behaviour. Once an operator sets the
variable, requests must present either a valid cookie or the matching header.

The session cookie name and lifetime are reused from
:mod:`cairn.server.routers.auth` so there is a single source of truth.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from fastapi.exceptions import HTTPException

from cairn.server.db import get_conn
from cairn.server.routers.auth import (
    DEFAULT_SESSION_DURATION,
    SESSION_COOKIE_NAME,
    cookie_secure_for_request,
)
from cairn.server.settings_service import load_settings

# Path the browser is redirected to when an unauthenticated navigation request is
# made. The frontend serves its login view from the root path.
LOGIN_PATH = "/"

# ---------------------------------------------------------------------------
# Internal service authentication (dual-auth scheme).
#
# Background: the dispatcher talks to the Cairn API over HTTP via
# ``cairn.dispatcher.protocol.client.CairnClient`` using a plain
# ``requests.Session``. It is a *machine* client: it sends neither a browser
# session cookie nor any auth header. The dispatcher hits the same endpoints the
# browser UI uses (``/projects``, ``/projects/{id}/intents/...``, ``/settings``,
# ``/projects/{id}/export``, ...). Protecting those endpoints with cookie-only
# auth would return 401 to every dispatcher request and break the dispatch loop.
#
# To protect the browser-facing UI *without* breaking the dispatcher — and
# without modifying the dispatcher (an existing core file) — we add an optional
# shared-secret path: a trusted machine client may authenticate by sending the
# ``X-Cairn-Internal-Token`` header matching the ``CAIRN_INTERNAL_TOKEN``
# environment variable.
#
# Non-breaking default: when ``CAIRN_INTERNAL_TOKEN`` is *not* set, the
# dispatcher-facing routers preserve their pre-auth (open) behaviour so existing
# deployments and the dispatcher keep working exactly as before. Setting the
# environment variable is an explicit, operator-driven opt-in to enforcement;
# once set, the dispatcher must be configured to send the matching header.
# ---------------------------------------------------------------------------

# Environment variable holding the shared internal-service token. Optional.
INTERNAL_TOKEN_ENV = "CAIRN_INTERNAL_TOKEN"

# Header a trusted machine client (e.g. the dispatcher) uses to present the
# internal token. Compared case-insensitively by Starlette's header lookup.
INTERNAL_TOKEN_HEADER = "x-cairn-internal-token"

# Timestamp format used throughout the codebase (matches ``services.utcnow`` and
# the format the auth router stores ``sessions.expires_at`` in).
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Paths exempt from authentication (requirement 5.2).
EXEMPT_PATHS = frozenset(
    {
        "/api/auth/register",
        "/api/auth/login",
        LOGIN_PATH,
    }
)

# Path prefixes exempt from authentication (the static assets mount).
EXEMPT_PREFIXES = ("/static/",)


def _format_timestamp(value: datetime) -> str:
    """Format a datetime using the codebase-wide UTC timestamp convention."""
    return value.strftime(_TIMESTAMP_FORMAT)


def _parse_timestamp(value: str) -> datetime:
    """Parse a stored timestamp back into an aware UTC datetime."""
    return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def is_exempt(path: str) -> bool:
    """Return ``True`` when ``path`` bypasses authentication (requirement 5.2)."""
    if path in EXEMPT_PATHS:
        return True
    return path.startswith(EXEMPT_PREFIXES)


def _configured_internal_token() -> str | None:
    """Return the configured internal-service token, or ``None`` when unset.

    Reads ``CAIRN_INTERNAL_TOKEN`` from the environment at call time (rather than
    import time) so deployments can set it before startup and tests can toggle it.
    An empty/whitespace-only value is treated as unset.
    """
    token = os.environ.get(INTERNAL_TOKEN_ENV)
    if token is None:
        return None
    token = token.strip()
    return token or None


def _has_valid_internal_token(request: Request) -> bool:
    """Return ``True`` when the request carries a valid internal-service token.

    The dispatcher (and any other trusted machine client) authenticates by
    sending the ``X-Cairn-Internal-Token`` header matching ``CAIRN_INTERNAL_TOKEN``.
    Comparison is constant-time to avoid leaking the secret via timing. Returns
    ``False`` when no token is configured so the caller can fall back to the
    dispatcher-safe open behaviour.
    """
    configured = _configured_internal_token()
    if configured is None:
        return False
    presented = request.headers.get(INTERNAL_TOKEN_HEADER)
    if not presented:
        return False
    return secrets.compare_digest(presented, configured)


def _clear_cookie_header(request: Request) -> str:
    """Build a ``Set-Cookie`` header value that clears the session cookie.

    Uses a throwaway :class:`Response` so the cleared cookie matches exactly the
    attributes (path, HttpOnly, Secure, SameSite) used when the auth router sets
    it, ensuring the browser actually removes it.
    """
    scratch = Response()
    scratch.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=cookie_secure_for_request(request),
        samesite="strict",
    )
    return scratch.headers["set-cookie"]


def _reject(request: Request) -> "HTTPException":
    """Build the rejection error for an unauthenticated request.

    Browser navigation requests (``Accept: text/html``) get a 302 redirect to the
    login page (requirement 5.3); everything else gets a 401 (requirements 5.1,
    5.4). In both cases the stale session cookie is cleared (requirements 3.2,
    3.6).
    """
    headers = {"set-cookie": _clear_cookie_header(request)}
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        headers["location"] = LOGIN_PATH
        return HTTPException(
            status_code=302,
            detail="Authentication required",
            headers=headers,
        )
    return HTTPException(
        status_code=401,
        detail="Authentication required",
        headers=headers,
    )


def require_auth(request: Request, response: Response) -> sqlite3.Row:
    """FastAPI dependency enforcing an authenticated session.

    Returns the authenticated user row and injects it onto ``request.state.user``
    so downstream handlers can access it. Raises an :class:`HTTPException` (401 or
    302) when authentication fails.
    """
    # Safety net: exempt paths never require authentication (requirement 5.2).
    # The dependency is only attached to protected routers, but guarding here
    # keeps it safe to apply broadly.
    if is_exempt(request.url.path):
        return None  # type: ignore[return-value]

    # Internal-service auth path (dual-auth): a trusted machine client such as the
    # dispatcher authenticates with the X-Cairn-Internal-Token header. This is the
    # mechanism that lets us protect the browser UI without breaking the
    # dispatcher's cookieless HTTP calls.
    #
    # Dispatcher-safe default: if CAIRN_INTERNAL_TOKEN is not configured, the
    # existing (cookieless) dispatcher and any current deployment must keep
    # working unchanged. In that case we do not enforce auth on these routers and
    # allow the request through, preserving pre-auth behaviour. Configuring the
    # env var is an explicit opt-in to enforcement.
    if _configured_internal_token() is None:
        # No internal token configured: do not break the dispatcher or existing
        # deployments. Allow through without injecting a user (no session).
        return None  # type: ignore[return-value]
    if _has_valid_internal_token(request):
        # Trusted machine client (e.g. dispatcher) presenting the shared secret.
        return None  # type: ignore[return-value]

    token = request.cookies.get(SESSION_COOKIE_NAME)
    now = datetime.now(timezone.utc)

    user_row: sqlite3.Row | None = None
    session_duration = DEFAULT_SESSION_DURATION
    if token:
        with get_conn() as conn:
            try:
                session_duration = timedelta(hours=load_settings(conn).session_duration_hours)
            except Exception:
                session_duration = DEFAULT_SESSION_DURATION
            session = conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token = ?",
                (token,),
            ).fetchone()
            if session is not None:
                expires_at = _parse_timestamp(session["expires_at"])
                if expires_at <= now:
                    # Expired: drop the dead session row (requirement 3.2).
                    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                else:
                    candidate = conn.execute(
                        "SELECT id, username, created_at FROM users WHERE id = ?",
                        (session["user_id"],),
                    ).fetchone()
                    if candidate is None:
                        # Orphaned session with no user; treat as invalid.
                        conn.execute(
                            "DELETE FROM sessions WHERE token = ?", (token,)
                        )
                    else:
                        # Sliding-window extension (requirement 3.5): push the
                        # expiry out by the configured period.
                        new_expires = now + session_duration
                        conn.execute(
                            "UPDATE sessions SET expires_at = ? WHERE token = ?",
                            (_format_timestamp(new_expires), token),
                        )
                        user_row = candidate

    if user_row is None:
        raise _reject(request)

    # Refresh the cookie's Max-Age so the sliding window is reflected client-side
    # too, preserving the HTTP-only/Secure/SameSite attributes (requirement 3.4).
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(session_duration.total_seconds()),
        httponly=True,
        secure=cookie_secure_for_request(request),
        samesite="strict",
        path="/",
    )

    request.state.user = {
        "id": user_row["id"],
        "username": user_row["username"],
        "created_at": user_row["created_at"],
    }
    return user_row
