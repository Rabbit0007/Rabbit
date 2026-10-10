from __future__ import annotations

from dataclasses import dataclass
import time

from cairn.dispatcher.runtime.cancellation import TaskCancellation


@dataclass(slots=True)
class RunningTask:
    project_id: str
    task_type: str
    worker_name: str
    cancellation: TaskCancellation
    started_at: float = 0.0
    step_id: str | None = None
    intent_id: str | None = None  # backward compat
    fact_count: int | None = None
    hint_count: int | None = None
    goal_count: int | None = None
    open_step_count: int | None = None
    open_intent_count: int | None = None  # backward compat

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.time()


@dataclass(slots=True)
class DecideCheckpoint:
    fact_count: int
    hint_count: int
    goal_count: int
    open_step_count: int
    graph_signature: tuple[object, ...] | None = None


# Backward compat
ReasonCheckpoint = DecideCheckpoint
