from __future__ import annotations

import time
from dataclasses import dataclass, field

from cairn.dispatcher.runtime.cancellation import TaskCancellation


@dataclass(slots=True)
class RunningTask:
    project_id: str
    task_type: str
    worker_name: str
    cancellation: TaskCancellation
    intent_id: str | None = None
    fact_count: int | None = None
    hint_count: int | None = None
    open_intent_count: int | None = None
    # Wall-clock start time, auto-populated at construction. Used only by the
    # optional read-only internal status API to compute task durations. This is
    # additive metadata and does not affect scheduling behavior.
    started_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ReasonCheckpoint:
    fact_count: int
    hint_count: int
    open_intent_count: int
