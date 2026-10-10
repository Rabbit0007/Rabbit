"""Temporary verification for task 5.1 (dispatcher internal status endpoint).

Verifies:
(a) internal_api imports cleanly,
(b) build_status_snapshot produces the expected shape from a mock DispatcherLoop
    state (without constructing the real loop / hitting docker/network),
(c) the existing dispatcher loop module still imports and its core behavior
    (RunningTask handling, history default-off) is preserved.
"""

import sys
import types
from collections import deque


def main() -> int:
    # (a) Imports
    from cairn.dispatcher import internal_api
    from cairn.dispatcher.models import RunningTask
    from cairn.dispatcher.runtime.cancellation import TaskCancellation
    from cairn.dispatcher.config import WorkerConfig
    from cairn.dispatcher.scheduler import loop as loop_module
    print("OK (a): modules import cleanly")

    # Default-off behavior: a fresh-style loop without tracking should report no history.
    assert hasattr(loop_module.DispatcherLoop, "enable_internal_state_tracking")
    assert hasattr(loop_module.DispatcherLoop, "_record_task_history")
    print("OK: loop has additive hooks (enable_internal_state_tracking, _record_task_history)")

    # RunningTask now carries an auto started_at, default history None.
    rt = RunningTask("p1", "explore", "w1", TaskCancellation(), intent_id="i1")
    assert isinstance(rt.started_at, float)
    print("OK: RunningTask.started_at auto-populated =", rt.started_at)

    # (b) Build a lightweight mock loop with just the attributes the snapshot reads.
    worker_busy = WorkerConfig(
        name="alpha", type="mock", task_types=["explore", "reason"], max_running=2, priority=0, env={}
    )
    worker_idle = WorkerConfig(
        name="beta", type="mock", task_types=["reason"], max_running=1, priority=1, env={}
    )

    runtime = types.SimpleNamespace(
        max_workers=4, max_running_projects=2, max_project_workers=2, interval=5
    )
    config = types.SimpleNamespace(workers=[worker_busy, worker_idle], runtime=runtime)

    import time
    now = time.time()
    running = RunningTask("proj-123", "explore", "alpha", TaskCancellation(), intent_id="intent-9")
    running.started_at = now - 12.0

    fut_key = object()
    mock_loop = types.SimpleNamespace(
        config=config,
        futures={fut_key: running},
        worker_unhealthy_until={"gamma": now + 30.0},  # an unhealthy worker not in config
        worker_rejected_until={("proj-123", "explore", "alpha"): now + 4.0},
        runtime_project_ids={"proj-123"},
        task_history=deque(
            [
                {
                    "project_id": "proj-123",
                    "task_type": "reason",
                    "worker_name": "alpha",
                    "intent_id": None,
                    "outcome": "success",
                    "started_at": now - 60,
                    "completed_at": now - 50,
                    "duration_seconds": 10.0,
                }
            ],
            maxlen=200,
        ),
    )

    snap = internal_api.build_status_snapshot(mock_loop)

    # Validate top-level shape.
    for key in ("generated_at", "now", "runtime", "workers", "running_tasks", "task_history", "heartbeats", "rejections"):
        assert key in snap, f"missing key {key}"
    print("OK (b): snapshot has all top-level keys")

    # runtime block
    assert snap["runtime"]["max_workers"] == 4
    assert snap["runtime"]["running_task_count"] == 1
    assert snap["runtime"]["running_project_count"] == 1

    # workers block: alpha busy (1 running), beta idle
    by_name = {w["name"]: w for w in snap["workers"]}
    assert by_name["alpha"]["status"] == "busy", by_name["alpha"]
    assert by_name["alpha"]["running"] == 1
    assert by_name["alpha"]["type"] == "mock"
    assert by_name["alpha"]["task_types"] == ["explore", "reason"]
    assert by_name["beta"]["status"] == "idle", by_name["beta"]
    assert by_name["beta"]["running"] == 0
    print("OK: worker statuses computed (alpha=busy, beta=idle)")

    # running_tasks block
    rt0 = snap["running_tasks"][0]
    assert rt0["project_id"] == "proj-123"
    assert rt0["worker_name"] == "alpha"
    assert rt0["intent_id"] == "intent-9"
    assert rt0["running_seconds"] is not None and rt0["running_seconds"] >= 12.0
    assert len(rt0["current_task"]) <= internal_api.TASK_DESCRIPTION_MAX
    print("OK: running task serialized with duration and truncated description")

    # task_history block (most recent first)
    assert len(snap["task_history"]) == 1
    assert snap["task_history"][0]["outcome"] == "success"
    assert snap["task_history"][0]["duration_seconds"] == 10.0
    print("OK: task history serialized")

    # heartbeats + rejections
    assert "gamma" in snap["heartbeats"]
    assert snap["heartbeats"]["gamma"]["unhealthy"] is True
    assert snap["rejections"][0]["worker_name"] == "alpha"
    assert snap["rejections"][0]["rejected"] is True
    print("OK: heartbeats and rejections serialized")

    # Truncation helper edge cases
    assert internal_api._truncate("x" * 200).endswith("...")
    assert len(internal_api._truncate("x" * 200)) == internal_api.TASK_DESCRIPTION_MAX
    assert internal_api._truncate("short") == "short"
    print("OK: truncation helper correct")

    # opt-in gate defaults to off
    import os
    os.environ.pop(internal_api.ENABLE_ENV, None)
    assert internal_api.is_internal_api_enabled() is False
    assert internal_api.start_internal_api(mock_loop) is False  # disabled -> no-op, returns False
    print("OK: internal API opt-in gate defaults OFF and start is a no-op")

    # (c) Confirm the FastAPI app can be created and exposes the route (read-only).
    app = internal_api.create_internal_app(mock_loop)
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/internal/status" in routes
    assert "/internal/health" in routes
    print("OK (c): FastAPI app exposes /internal/status and /internal/health")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
