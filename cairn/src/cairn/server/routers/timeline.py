"""Attack timeline router.

This is an additive, read-only router exposing
``GET /api/projects/{project_id}/timeline``. The timeline is *derived* from the
existing ``facts`` and ``intents`` tables -- there is no dedicated timeline table
(see design.md, "Attack Timeline"). Nothing in this module mutates existing
core tables; it only reads them.

Deriving timestamps
-------------------
The ``facts`` table stores only ``id``, ``project_id`` and ``description`` -- it
has **no** ``created_at`` column. Timestamps therefore come from the ``intents``
table, which records ``created_at`` (declaration) and ``concluded_at``
(conclusion). A discovered fact is the ``to_fact_id`` of the intent that
concluded into it, so that fact's discovery time is the producing intent's
``concluded_at``.

The two seed facts every project starts with -- ``origin`` (the starting
context) and ``goal`` (the objective) -- are *given*, not discovered, and have no
producing intent, so they are not surfaced as ``fact_discovery`` events. As a
result a freshly created project with no intents yields an empty timeline,
matching requirement 14.5's empty state.

Event derivation (requirements 14.1-14.4)
-----------------------------------------
For each intent (processed in declaration order):

* ``intent_declaration`` -- always, at ``created_at``; actor is the ``creator``.
* When the intent is concluded:
    * if it concludes into the ``goal`` fact it is the project's completion
      intent and yields a ``project_completion`` event at ``concluded_at``;
    * otherwise it yields an ``intent_conclusion`` event at ``concluded_at`` plus
      a ``fact_discovery`` event for the fact it produced (the new fact node).

``actor`` is the responsible worker/creator for intent-related events and is
``None`` for a raw ``fact_discovery`` (per :mod:`cairn.server.timeline_models`).
``node_id`` is the intent or fact id used by the frontend to highlight the
corresponding graph node.

Ordering (requirement 14.1)
---------------------------
Events are returned sorted by ``timestamp`` ascending with declaration order as
the tiebreaker for events sharing the same timestamp. Timestamps are stored as
zero-padded ISO-8601 UTC strings (``%Y-%m-%dT%H:%M:%SZ``) which sort correctly
lexicographically. The tiebreaker is a monotonic sequence assigned as events are
generated in declaration order (intents in ``created_at``/insertion order, and
within an intent: declaration, then conclusion, then the resulting discovery).
"""

from __future__ import annotations

from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.services import get_project_or_404
from cairn.server.timeline_models import TimelineEvent

router = APIRouter(prefix="/api/projects/{project_id}/timeline", tags=["timeline"])

# The fact id of a project's objective. An intent that concludes into this fact
# is the project's completion intent and is surfaced as a ``project_completion``
# event rather than an ordinary ``intent_conclusion`` (requirement 14.2).
_GOAL_FACT_ID = "goal"


@router.get("", response_model=list[TimelineEvent])
def get_timeline(project_id: str) -> list[TimelineEvent]:
    """Return the project's attack timeline as chronologically ordered events.

    Returns a 404 when the project does not exist and an empty list for a
    project that has recorded no activity (requirement 14.5). Events are merged
    from the project's facts and intents and ordered by timestamp with
    declaration order as the tiebreaker (requirement 14.1).
    """
    with get_conn() as conn:
        # 404 when the project does not exist (mirrors the existing routers).
        get_project_or_404(conn, project_id)

        # Facts carry no timestamp of their own; we read their descriptions and
        # resolve discovery times from the producing intent below.
        fact_rows = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        fact_descriptions = {row["id"]: row["description"] for row in fact_rows}

        # Intents drive the timeline: declaration order is (created_at, insertion
        # order) so equal-timestamp declarations keep their original sequence.
        intent_rows = conn.execute(
            """
            SELECT id, to_fact_id, description, creator, worker, created_at, concluded_at
            FROM intents
            WHERE project_id = ?
            ORDER BY created_at, rowid
            """,
            (project_id,),
        ).fetchall()

    # Each entry is (timestamp, sequence, event). ``sequence`` is a global
    # monotonic counter assigned in declaration order so that sorting by
    # (timestamp, sequence) yields "timestamp ascending, declaration order as
    # tiebreaker" (requirement 14.1).
    ordered: list[tuple[str, int, TimelineEvent]] = []
    sequence = 0

    for intent in intent_rows:
        intent_id = intent["id"]
        created_at = intent["created_at"]
        concluded_at = intent["concluded_at"]
        to_fact_id = intent["to_fact_id"]

        # Intent declaration: always present; the actor is the declaring creator.
        ordered.append(
            (
                created_at,
                sequence,
                TimelineEvent(
                    id=f"intent_declaration:{intent_id}",
                    event_type="intent_declaration",
                    description=intent["description"],
                    timestamp=created_at,
                    actor=intent["creator"],
                    node_id=intent_id,
                ),
            )
        )
        sequence += 1

        if concluded_at is None:
            continue

        if to_fact_id == _GOAL_FACT_ID:
            # Concluding into the goal fact marks the project complete; the goal
            # fact already existed at creation, so no fact_discovery is emitted.
            ordered.append(
                (
                    concluded_at,
                    sequence,
                    TimelineEvent(
                        id=f"project_completion:{intent_id}",
                        event_type="project_completion",
                        description=intent["description"],
                        timestamp=concluded_at,
                        actor=intent["worker"],
                        node_id=intent_id,
                    ),
                )
            )
            sequence += 1
            continue

        # Ordinary conclusion: the intent concluded and produced a new fact.
        ordered.append(
            (
                concluded_at,
                sequence,
                TimelineEvent(
                    id=f"intent_conclusion:{intent_id}",
                    event_type="intent_conclusion",
                    description=intent["description"],
                    timestamp=concluded_at,
                    actor=intent["worker"],
                    node_id=intent_id,
                ),
            )
        )
        sequence += 1

        # The discovered fact node produced by this conclusion. A raw fact
        # discovery has no actor (see timeline_models).
        if to_fact_id is not None and to_fact_id in fact_descriptions:
            ordered.append(
                (
                    concluded_at,
                    sequence,
                    TimelineEvent(
                        id=f"fact_discovery:{to_fact_id}",
                        event_type="fact_discovery",
                        description=fact_descriptions[to_fact_id],
                        timestamp=concluded_at,
                        actor=None,
                        node_id=to_fact_id,
                    ),
                )
            )
            sequence += 1

    ordered.sort(key=lambda item: (item[0], item[1]))
    return [event for _timestamp, _sequence, event in ordered]
