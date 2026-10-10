"""Project templates router.

Additive router exposing the ``/api/templates`` endpoints that back the Template
Engine. It does **not** touch any dispatcher, scheduler, worker, or existing
router code. It reads the built-in templates from
:mod:`cairn.server.templates_service` (an in-memory Python constant) and the
user's saved custom templates from the ``templates`` table created by
:mod:`cairn.server.product_db`.

Three endpoints are provided:

* ``GET /api/templates`` — returns the built-in templates (labelled
  ``is_builtin=True``, ``user_id=None``) merged with the requesting user's saved
  custom templates (labelled ``is_builtin=False`` with the owning ``user_id``).
  Requirements 12.1, 12.2, 13.2.
* ``POST /api/templates`` — persists a custom template for the requesting user,
  enforcing the per-user limit of 50 templates. Requirements 13.1, 13.6, 13.7.
* ``DELETE /api/templates/{template_id}`` — deletes a custom template only when
  the requesting user owns it; a template owned by another user is rejected with
  an ownership error and left in place. Requirements 13.4, 13.5.

User identity is read from ``request.state.user`` (a ``{"id", "username", ...}``
mapping) which the auth middleware injects on an authenticated request. The
dual-auth scheme means that when ``CAIRN_INTERNAL_TOKEN`` is unset the protected
routers preserve their pre-auth (open) behaviour and ``request.state.user`` is
never set; in that case custom templates are scoped to a single shared
``ANONYMOUS_USER_ID`` so the endpoints remain usable without a session. Built-in
templates are always returned regardless of the authenticated user.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Path, Request

from cairn.server.db import get_conn
from cairn.server.templates_models import CreateTemplateRequest, TemplateResponse
from cairn.server.templates_service import BUILTIN_TEMPLATES

router = APIRouter(prefix="/api/templates", tags=["templates"])

# Per-user cap on saved custom templates (requirements 13.6, 13.7).
MAX_TEMPLATES_PER_USER = 50

# Fallback owner used when no authenticated user is present on the request. The
# auth middleware only injects ``request.state.user`` once an operator opts into
# enforcement by setting ``CAIRN_INTERNAL_TOKEN``; without it the routers run in
# their pre-auth (open) mode, so custom templates are scoped to this single
# shared identity rather than failing the request.
ANONYMOUS_USER_ID = "anonymous"

# Timestamp format used throughout the codebase (matches ``services.utcnow``).
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _current_user_id(request: Request) -> str:
    """Resolve the requesting user's id, falling back to the anonymous owner.

    The auth middleware injects ``request.state.user`` as a mapping carrying the
    user ``id`` on an authenticated request. When that state is absent (the
    dispatcher-safe open mode, where ``CAIRN_INTERNAL_TOKEN`` is unset) custom
    templates are scoped to :data:`ANONYMOUS_USER_ID` so the endpoints stay
    usable without a session.
    """
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        user_id = user.get("id")
        if isinstance(user_id, str) and user_id:
            return user_id
    return ANONYMOUS_USER_ID


def _format_timestamp(value: datetime) -> str:
    """Format a datetime using the codebase-wide UTC timestamp convention."""
    return value.strftime(_TIMESTAMP_FORMAT)


def _parse_hints(hints_json: str) -> list[dict[str, str]]:
    """Decode the stored ``hints_json`` column into a list of hint mappings.

    Stored hints are a JSON array of ``{content, creator}`` objects. A malformed
    or non-array value is treated as "no hints" rather than raising, so a single
    bad row can never break listing a user's templates.
    """
    try:
        decoded = json.loads(hints_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    hints: list[dict[str, str]] = []
    for item in decoded:
        if isinstance(item, dict):
            hints.append({str(k): str(v) for k, v in item.items()})
    return hints


def _builtin_templates() -> list[TemplateResponse]:
    """Return the built-in templates as :class:`TemplateResponse` objects.

    Built-in templates live as an in-memory constant (no DB storage); each is
    surfaced with ``is_builtin=True`` and ``user_id=None`` (requirements 12.1,
    12.2).
    """
    return [TemplateResponse(**template) for template in BUILTIN_TEMPLATES]


@router.get("", response_model=list[TemplateResponse])
def list_templates(request: Request) -> list[TemplateResponse]:
    """List the built-in templates merged with the user's custom templates.

    Built-in templates are always returned (requirement 12.1), each labelled
    ``is_builtin=True``. The requesting user's saved custom templates are
    appended, each labelled ``is_builtin=False`` with the owning ``user_id``
    (requirement 13.2), ordered by creation time (then id) for a stable result.
    Only the requesting user's own custom templates are included.
    """
    user_id = _current_user_id(request)

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, title, origin, goal, hints_json, created_at
            FROM templates
            WHERE user_id = ?
            ORDER BY created_at, id
            """,
            (user_id,),
        ).fetchall()

    templates = _builtin_templates()
    for row in rows:
        templates.append(
            TemplateResponse(
                id=row["id"],
                title=row["title"],
                origin=row["origin"],
                goal=row["goal"],
                hints=_parse_hints(row["hints_json"]),
                is_builtin=False,
                user_id=row["user_id"],
            )
        )
    return templates


@router.post("", response_model=TemplateResponse, status_code=201)
def create_template(body: CreateTemplateRequest, request: Request) -> TemplateResponse:
    """Create a custom template for the requesting user.

    Field validation (title/origin/goal 1-200 chars, 0-10 hints) is enforced by
    :class:`CreateTemplateRequest`, which yields a 422 for invalid input
    (requirement 13.1). This handler enforces the per-user cap of 50 templates:
    a user who already has 50 saved templates is rejected with a 409
    (requirements 13.6, 13.7). On success the stored template is returned with
    ``is_builtin=False`` and the owning ``user_id`` (requirement 13.1 round-trip).
    """
    user_id = _current_user_id(request)
    now = datetime.now(timezone.utc)
    created_at = _format_timestamp(now)
    template_id = f"tmpl_{uuid.uuid4().hex}"
    hints_json = json.dumps(body.hints)

    with get_conn() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM templates WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if int(count_row["n"]) >= MAX_TEMPLATES_PER_USER:
            raise HTTPException(
                status_code=409,
                detail=f"Template limit reached ({MAX_TEMPLATES_PER_USER})",
            )

        try:
            conn.execute(
                """
                INSERT INTO templates
                    (id, user_id, title, origin, goal, hints_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    user_id,
                    body.title,
                    body.origin,
                    body.goal,
                    hints_json,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # The id is a fresh uuid so a collision is effectively impossible;
            # surface any constraint violation as a 409 rather than a 500.
            raise HTTPException(
                status_code=409, detail="Could not create template"
            ) from exc

    return TemplateResponse(
        id=template_id,
        title=body.title,
        origin=body.origin,
        goal=body.goal,
        hints=body.hints,
        is_builtin=False,
        user_id=user_id,
    )


@router.delete("/{template_id}", status_code=204)
def delete_template(
    request: Request,
    template_id: str = Path(..., min_length=1),
) -> None:
    """Delete a custom template the requesting user owns.

    Looks up the template by id. A template that does not exist yields a 404
    (design "Template not found"). A template owned by a different user is
    rejected with a 403 and left in place (requirement 13.5). When the requesting
    user owns the template it is removed permanently (requirement 13.4).

    Built-in template ids are not stored in the ``templates`` table, so a request
    to delete a built-in id is treated as "not found".
    """
    user_id = _current_user_id(request)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Template not found")
        if row["user_id"] != user_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot delete template owned by another user",
            )
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))

    return None
