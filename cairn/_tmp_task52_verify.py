"""Temporary verification for task 5.2 (worker history schema + models)."""
import sqlite3

from cairn.server import product_db
from cairn.server.workers_models import (
    CURRENT_TASK_MAX_LENGTH,
    WorkerStatus,
    WorkerTaskHistoryEntry,
)

out = []

# 1. Schema DDL is valid and creates the table + indexes on a fresh sqlite db.
conn = sqlite3.connect(":memory:")
conn.executescript(product_db.PRODUCT_SCHEMA)
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()}
indexes = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index'"
).fetchall()}
out.append(f"worker_task_history in tables: {'worker_task_history' in tables}")
out.append(f"idx_worker_history_worker present: {'idx_worker_history_worker' in indexes}")
out.append(f"idx_worker_history_time present: {'idx_worker_history_time' in indexes}")

cols = [(r[1], r[2], r[3]) for r in conn.execute(
    "PRAGMA table_info(worker_task_history)"
).fetchall()]
out.append(f"columns: {cols}")

# CHECK constraint rejects an invalid outcome.
conn.execute("INSERT INTO worker_task_history "
             "(worker_name, project_id, task_type, started_at, outcome) "
             "VALUES ('w', 'p', 'explore', '2024-01-01T00:00:00Z', 'success')")
try:
    conn.execute("INSERT INTO worker_task_history "
                 "(worker_name, project_id, task_type, started_at, outcome) "
                 "VALUES ('w', 'p', 'explore', '2024-01-01T00:00:00Z', 'bogus')")
    out.append("CHECK rejects bad outcome: FAIL (insert succeeded)")
except sqlite3.IntegrityError:
    out.append("CHECK rejects bad outcome: True")

# nullable completed_at / duration_seconds / intent_id accepted.
conn.execute("INSERT INTO worker_task_history "
             "(worker_name, project_id, task_type, started_at, outcome) "
             "VALUES ('w2', 'p2', 'reason', '2024-01-01T00:00:00Z', 'released')")
out.append("nullable completed_at/duration/intent_id accepted: True")
conn.close()

# 2. WorkerStatus model.
ws = WorkerStatus(name="alpha", type="claudecode", status="idle", tasks_completed=0)
out.append(f"WorkerStatus defaults: current_task={ws.current_task!r} "
           f"avg={ws.avg_duration_seconds!r} hb={ws.last_heartbeat_seconds_ago!r}")

long_task = "x" * 200
ws2 = WorkerStatus(name="beta", type="t", status="busy", current_task=long_task,
                   tasks_completed=3, avg_duration_seconds=12.5,
                   last_heartbeat_seconds_ago=2.0)
out.append(f"current_task truncated to {CURRENT_TASK_MAX_LENGTH}: "
           f"{len(ws2.current_task) == CURRENT_TASK_MAX_LENGTH}")

short = "scan target"
ws3 = WorkerStatus(name="g", type="t", status="busy", current_task=short,
                   tasks_completed=1)
out.append(f"short current_task unchanged: {ws3.current_task == short}")

try:
    WorkerStatus(name="x", type="t", status="bogus", tasks_completed=0)
    out.append("WorkerStatus rejects bad status: FAIL")
except Exception:
    out.append("WorkerStatus rejects bad status: True")

# 3. WorkerTaskHistoryEntry model.
h = WorkerTaskHistoryEntry(project_name="proj", task_type="explore",
                           description="d", started_at="2024-01-01T00:00:00Z",
                           duration_seconds=5.0, outcome="success")
out.append(f"history entry ok: {h.outcome == 'success' and h.duration_seconds == 5.0}")
h2 = WorkerTaskHistoryEntry(project_name="proj", task_type="explore",
                            description="d", started_at="2024-01-01T00:00:00Z",
                            outcome="released")
out.append(f"history entry duration_seconds default None: {h2.duration_seconds is None}")
try:
    WorkerTaskHistoryEntry(project_name="p", task_type="t", description="d",
                           started_at="2024-01-01T00:00:00Z", outcome="bogus")
    out.append("history entry rejects bad outcome: FAIL")
except Exception:
    out.append("history entry rejects bad outcome: True")

with open("_tmp_task52_result.txt", "w") as f:
    f.write("\n".join(out) + "\n")
print("done")
