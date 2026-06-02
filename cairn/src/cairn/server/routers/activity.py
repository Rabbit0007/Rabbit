"""Activity router: audit log and notifications.

Additive router exposing ``/api/audit`` (read-only audit trail) and
``/api/notifications`` (list / unread-count / mark-read / clear) backed by the
``audit_log`` and ``notifications`` tables created in ``product_db.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cairn.server.db import get_conn

router = APIRouter(prefix="/api", tags=["activity"])


class AuditEntry(BaseModel):
    id: int
    created_at: str
    actor: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    summary: str
    detail: str | None = None


class Notification(BaseModel):
    id: int
    created_at: str
    level: str
    title: str
    body: str | None = None
    link: str | None = None
    read: bool


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(limit: int = Query(default=100, ge=1, le=500)) -> list[AuditEntry]:
    """Return recent audit-log entries, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, actor, action, target_type, target_id, summary, detail
            FROM audit_log
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [AuditEntry(**dict(row)) for row in rows]


@router.get("/notifications", response_model=list[Notification])
def list_notifications(limit: int = Query(default=50, ge=1, le=200)) -> list[Notification]:
    """Return recent notifications, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, level, title, body, link, read
            FROM notifications
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [Notification(**{**dict(row), "read": bool(row["read"])}) for row in rows]


@router.get("/notifications/unread-count")
def unread_count() -> dict[str, int]:
    """Return the number of unread notifications."""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE read = 0").fetchone()
    return {"count": int(row["n"])}


@router.post("/notifications/read")
def mark_read(payload: dict | None = None) -> dict[str, int]:
    """Mark notifications as read.

    With an ``ids`` list, marks those rows read; with no body, marks all read.
    Returns the remaining unread count.
    """
    ids = None
    if isinstance(payload, dict):
        raw = payload.get("ids")
        if isinstance(raw, list):
            ids = [int(item) for item in raw if isinstance(item, (int, str)) and str(item).isdigit()]
    with get_conn() as conn:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})", ids)
        else:
            conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        row = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE read = 0").fetchone()
    return {"count": int(row["n"])}


@router.delete("/notifications")
def clear_notifications() -> dict[str, str]:
    """Delete all notifications."""
    with get_conn() as conn:
        conn.execute("DELETE FROM notifications")
    return {"status": "cleared"}
