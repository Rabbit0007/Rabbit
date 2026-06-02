"""Audit log and notification helpers.

Additive, dependency-free helpers used by the product routers to record audit
events and user-facing notifications into the tables created in
``product_db.py``. Both writers are best-effort: a logging failure must never
break the primary operation (status change, deletion, export, ...), so each is
wrapped in a try/except that swallows database errors.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cairn.server.db import get_conn

_NOTIFICATION_LEVELS = {"info", "success", "warning", "danger"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_audit(
    action: str,
    summary: str,
    *,
    actor: str = "admin",
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Append a single audit-log row. Best-effort; never raises."""
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_log
                    (created_at, actor, action, target_type, target_id, summary, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (_now(), actor or "admin", action, target_type, target_id, summary, detail),
            )
    except Exception:  # pragma: no cover - logging must not break the operation
        pass


def record_notification(
    title: str,
    *,
    level: str = "info",
    body: str | None = None,
    link: str | None = None,
) -> None:
    """Append a single notification row. Best-effort; never raises."""
    if level not in _NOTIFICATION_LEVELS:
        level = "info"
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO notifications (created_at, level, title, body, link, read)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (_now(), level, title, body, link),
            )
    except Exception:  # pragma: no cover - logging must not break the operation
        pass
