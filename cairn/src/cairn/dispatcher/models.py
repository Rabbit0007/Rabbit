from __future__ import annotations

from dataclasses import dataclass

from cairn.dispatcher.runtime.cancellation import TaskCancellation


@dataclass(slots=True)
class RunningTask:
    project_id: str
    task_type: str
    worker_name: str
    cancellation: TaskCancellation
    step_id: str | None = None
    intent_id: str | None = None  # backward compat
    fact_count: int | None = None
    hint_count: int | None = None
    goal_count: int | None = None
    open_step_count: int | None = None
    open_intent_count: int | None = None  # backward compat


@dataclass(slots=True)
class DecideCheckpoint:
    fact_count: int
    hint_count: int
    goal_count: int
    open_step_count: int


# Backward compat
ReasonCheckpoint = DecideCheckpoint
