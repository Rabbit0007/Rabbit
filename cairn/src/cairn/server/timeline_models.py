"""Pydantic models for a timeline derived from native Cairn-Y state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# The kinds of events surfaced on the attack timeline. ``fact_discovery`` is a
# new fact, ``intent_declaration`` / ``intent_conclusion`` bracket an intent's
# lifecycle, and ``project_completion`` marks a project reaching a completed
# state. Constrained as a ``Literal`` so an unexpected value is rejected as a
# validation error.
TimelineEventType = Literal[
    "fact_discovery",
    "step_declaration",
    "step_conclusion",
    "goal_completion",
    # Compatibility with cached responses emitted by older Rabbit builds.
    "intent_declaration",
    "intent_conclusion",
    "project_completion",
]


class TimelineEvent(BaseModel):
    """A single chronological event on a project's attack timeline.

    Events are merged from Goals, Steps and Facts and ordered by ``timestamp``.
    ``actor`` is the worker or creator name
    responsible for the event and is ``None`` for events with no associated actor
    (e.g. a raw fact discovery). ``node_id`` is the Fact, Goal or Step id
    used by the frontend to highlight the corresponding node in the graph view,
    and is ``None`` for events that do not map onto a graph node.
    """

    id: str
    event_type: TimelineEventType
    description: str
    timestamp: str
    actor: str | None = None
    node_id: str | None = None
