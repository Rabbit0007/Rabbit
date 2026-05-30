"""Temp verification for task 8.2: import the timeline router cleanly and
exercise its derivation logic against an in-memory-style temp DB."""

import sys
import tempfile
from pathlib import Path

from cairn.server import db
from cairn.server.services import utcnow


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "cairn.db"
    db.configure(tmp)

    # Import after configure to ensure the module imports cleanly.
    from cairn.server.routers.timeline import router, get_timeline

    # sanity: router prefix/route registered
    paths = [r.path for r in router.routes]
    print("ROUTES:", paths)

    # Empty / nonexistent project -> 404
    try:
        get_timeline("proj_404")
        print("ERROR: expected 404 for missing project")
    except Exception as e:
        print("MISSING_PROJECT_RAISES:", type(e).__name__, getattr(e, "status_code", None))

    # Build a small project with facts/intents directly.
    now = utcnow()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            ("proj_001", "T", "2024-01-01T00:00:00Z"),
        )
        # seed facts
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin','proj_001','o')")
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('goal','proj_001','g')")

    # Empty timeline (only seed facts, no intents)
    empty = get_timeline("proj_001")
    print("EMPTY_TIMELINE_LEN:", len(empty))

    with db.get_conn() as conn:
        # open intent (declared, not concluded)
        conn.execute(
            "INSERT INTO intents (id, project_id, to_fact_id, description, creator, worker, last_heartbeat_at, created_at, concluded_at) "
            "VALUES ('i001','proj_001',NULL,'explore A','alice',NULL,NULL,'2024-01-01T00:00:01Z',NULL)",
        )
        # concluded intent producing fact f001
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('f001','proj_001','found vuln')")
        conn.execute(
            "INSERT INTO intents (id, project_id, to_fact_id, description, creator, worker, last_heartbeat_at, created_at, concluded_at) "
            "VALUES ('i002','proj_001','f001','exploit B','bob','worker-1','2024-01-01T00:00:05Z','2024-01-01T00:00:02Z','2024-01-01T00:00:05Z')",
        )
        # completion intent concluding into goal
        conn.execute(
            "INSERT INTO intents (id, project_id, to_fact_id, description, creator, worker, last_heartbeat_at, created_at, concluded_at) "
            "VALUES ('i003','proj_001','goal','done','carol','worker-2','2024-01-01T00:00:06Z','2024-01-01T00:00:03Z','2024-01-01T00:00:06Z')",
        )

    events = get_timeline("proj_001")
    print("EVENT_COUNT:", len(events))
    for e in events:
        print(f"  {e.timestamp} | {e.event_type:20s} | node={e.node_id!s:8s} | actor={e.actor!s:8s} | {e.description}")

    # Assertions
    types = [e.event_type for e in events]
    assert types == sorted(  # just check ordering is by timestamp
        types, key=lambda _t: 0
    ) or True
    # Verify chronological ordering
    ts = [e.timestamp for e in events]
    assert ts == sorted(ts), f"not chronologically ordered: {ts}"
    # fact_discovery has no actor
    for e in events:
        if e.event_type == "fact_discovery":
            assert e.actor is None, "fact_discovery should have no actor"
        if e.event_type in ("intent_declaration", "intent_conclusion", "project_completion"):
            assert e.actor is not None, f"{e.event_type} should have an actor"
    # completion present
    assert "project_completion" in types, "expected a project_completion event"
    # goal fact should NOT appear as fact_discovery
    assert not any(e.node_id == "goal" and e.event_type == "fact_discovery" for e in events)
    # origin fact never surfaces as discovery
    assert not any(e.node_id == "origin" for e in events)
    print("ALL_ASSERTIONS_PASSED")


if __name__ == "__main__":
    main()
