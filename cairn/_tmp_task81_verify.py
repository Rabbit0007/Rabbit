"""Temporary verification for task 8.1 (timeline_models)."""

from pydantic import ValidationError

from cairn.server.timeline_models import TimelineEvent

lines = []

# 1. Instantiate with all fields populated.
ev = TimelineEvent(
    id="evt-1",
    event_type="fact_discovery",
    description="Discovered open SSH port",
    timestamp="2024-01-01T00:00:00Z",
    actor="explorer-1",
    node_id="fact-123",
)
lines.append(f"full: {ev.model_dump()}")

# 2. Instantiate with optional fields omitted (actor/node_id default to None).
ev2 = TimelineEvent(
    id="evt-2",
    event_type="project_completion",
    description="Project completed",
    timestamp="2024-01-02T00:00:00Z",
)
lines.append(f"defaults: actor={ev2.actor!r} node_id={ev2.node_id!r}")

# 3. All four valid event types accepted.
for et in ("fact_discovery", "intent_declaration", "intent_conclusion", "project_completion"):
    TimelineEvent(id="x", event_type=et, description="d", timestamp="t")
lines.append("all 4 event_type literals accepted: OK")

# 4. Invalid event_type rejected by the Literal constraint.
try:
    TimelineEvent(id="x", event_type="bogus_event", description="d", timestamp="t")
    lines.append("ERROR: invalid event_type was NOT rejected")
except ValidationError:
    lines.append("invalid event_type rejected: OK")

with open("_tmp_task81_result.txt", "w") as f:
    f.write("\n".join(lines) + "\n")
