"""Pydantic models for the worker dashboard.

This module is intentionally a standalone module (``workers_models.py``) rather
than a ``models/workers.py`` package member. The existing ``cairn.server.models``
is a single module (``models.py``) that is imported across the dispatcher and
server (``from cairn.server.models import ...``). Introducing a ``models/``
package would shadow that module and break those imports, so these models live
in their own additive module instead -- mirroring the convention established by
``auth_models.py`` and ``vulnerabilities_models.py``.

The field shapes follow design.md (New Pydantic Models section). ``WorkerStatus``
is the per-worker summary surfaced by ``GET /api/workers``; ``WorkerTaskHistoryEntry``
is a single row returned by ``GET /api/workers/{name}/history`` and maps onto the
``worker_task_history`` table created in ``product_db.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# The current-task description shown for a busy worker is truncated to this many
# characters so the dashboard card stays compact (requirement 10, design.md).
CURRENT_TASK_MAX_LENGTH = 120


class WorkerStatus(BaseModel):
    """Per-worker status and health metrics for the worker dashboard.

    ``current_task`` is truncated to :data:`CURRENT_TASK_MAX_LENGTH` characters.
    ``avg_duration_seconds`` and ``last_heartbeat_seconds_ago`` are ``None`` when
    no data is available (e.g. a worker that has completed zero tasks, or one
    that has never reported a heartbeat).
    """

    name: str
    type: str
    status: Literal["idle", "busy", "offline"]
    current_task: str | None = None
    tasks_completed: int
    avg_duration_seconds: float | None = None
    last_heartbeat_seconds_ago: float | None = None

    @field_validator("current_task")
    @classmethod
    def truncate_current_task(cls, value: str | None) -> str | None:
        """Truncate the current-task description to the dashboard limit."""
        if value is None:
            return None
        if len(value) > CURRENT_TASK_MAX_LENGTH:
            return value[:CURRENT_TASK_MAX_LENGTH]
        return value


class WorkerTaskHistoryEntry(BaseModel):
    """A single historical task executed by a worker.

    Mirrors a row of the ``worker_task_history`` table joined with ``projects``
    to resolve ``project_name``. ``duration_seconds`` is ``None`` for a task that
    never completed (e.g. one that was released or is otherwise missing a
    recorded duration).
    """

    project_name: str
    task_type: str
    description: str
    started_at: str
    duration_seconds: float | None = None
    outcome: Literal["success", "failed", "rejected", "released"]
