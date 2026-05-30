"""Pydantic models for the attack timeline.

This module is intentionally a standalone module (``timeline_models.py``) rather
than a ``models/timeline.py`` package member. The existing ``cairn.server.models``
is a single module (``models.py``) that is imported across the dispatcher and
server (``from cairn.server.models import ...``). Introducing a ``models/``
package would shadow that module and break those imports, so these models live
in their own additive module instead -- mirroring the convention established by
``auth_models.py``, ``vulnerabilities_models.py`` and ``workers_models.py``.

The field shapes follow design.md (New Pydantic Models section -- Timeline
models). ``TimelineEvent`` is a single entry returned by
``GET /api/projects/{project_id}/timeline``; the timeline itself is derived from
the existing ``facts`` and ``intents`` data (no new table), so these models are
purely for shaping the API response.
"""

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
    "intent_declaration",
    "intent_conclusion",
    "project_completion",
]


class TimelineEvent(BaseModel):
    """A single chronological event on a project's attack timeline.

    Events are merged from facts and intents and ordered by ``timestamp`` (with
    declaration order as the tiebreaker). ``actor`` is the worker or creator name
    responsible for the event and is ``None`` for events with no associated actor
    (e.g. a raw fact discovery). ``node_id`` is the ``fact_id`` or ``intent_id``
    used by the frontend to highlight the corresponding node in the graph view,
    and is ``None`` for events that do not map onto a graph node.
    """

    id: str
    event_type: TimelineEventType
    description: str
    timestamp: str
    actor: str | None = None
    node_id: str | None = None
