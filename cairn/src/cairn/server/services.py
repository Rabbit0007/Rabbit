from __future__ import annotations

import sqlite3
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from cairn.server.models import (
    Finding,
    Goal,
    Intent,
    ProjectDecide,
    ProjectMeta,
    ProjectReason,
    Step,
)
from cairn.server.settings_service import load_settings


SERVER_ACCESS_GOAL_MARKERS = (
    "服务器权限",
    "系统权限",
    "主机权限",
    "getshell",
    "get shell",
    "shell",
    "webshell",
    "rce",
    "命令执行",
    "远程代码执行",
)
LOGIN_GOAL_MARKERS = (
    "成功登录",
    "登录权限",
    "登录系统",
    "后台权限",
    "管理员权限",
    "获取登录",
    "拿到登录",
)
SERVER_ACCESS_PROOF_MARKERS = (
    "已获取服务器权限",
    "获取到服务器权限",
    "拿到服务器权限",
    "获得服务器权限",
    "取得服务器权限",
    "已获取系统权限",
    "拿到系统权限",
    "获得系统权限",
    "取得系统权限",
    "已获取主机权限",
    "拿到主机权限",
    "获得主机权限",
    "取得主机权限",
    "getshell",
    "get shell",
    "反弹 shell",
    "交互式 shell",
    "webshell",
    "uid=",
    "gid=",
    "whoami",
    "id 命令",
    "命令执行成功",
    "远程命令执行成功",
    "rce 成功",
    "root 权限",
    "www-data",
)
LOGIN_PROOF_MARKERS = (
    "成功登录",
    "登录成功",
    "已登录",
    "已成功登录",
    "进入后台",
    "进入管理后台",
    "后台访问成功",
    "获取登录权限",
    "拿到登录权限",
    "有效登录凭据",
    "有效管理员凭据",
    "valid credentials",
    "logged in",
    "authenticated as",
    "authenticated session",
)
NEGATED_PROOF_PREFIX_MARKERS = (
    "未",
    "无",
    "没有",
    "尚无",
    "尚未",
    "无法",
    "不能",
    "未能",
    "暂未",
    "不可",
    "no ",
    "not ",
    "without",
    "failed",
)
NEGATED_PROOF_SUFFIX_MARKERS = (
    "未成功",
    "未返回",
    "失败",
    "无法",
    "不能",
    "无输出",
    "not ",
    "failed",
)
PROOF_MARKERS_REQUIRING_SUFFIX_CHECK = (
    "whoami",
    "uid=",
    "gid=",
    "id 命令",
)
UNSATISFIED_COMPLETION_PATTERNS = (
    r"goal\s*(?:尚)?未达成",
    r"目标\s*(?:尚)?未达成",
    r"(?:尚无|没有|无)证据证明已(?:实现|达成|满足)",
    r"尚未(?:实现|达成|满足).*?(?:服务器权限|系统权限|主机权限|登录权限|登录)",
    r"未(?:获得|获取|取得|拿到).*?(?:服务器权限|系统权限|主机权限|登录权限|登录凭据|有效登录凭据|登录态)",
    r"未成功登录",
    r"暂未形成.*?(?:认证绕过|权限获取|服务器权限|登录权限)",
    r"没有待完成的开放意图",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_project_id(conn: sqlite3.Connection) -> str:
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'project'")
    row = conn.execute("SELECT value FROM counters WHERE name = 'project'").fetchone()
    return f"proj_{row['value']:03d}"


def _next_scoped_id(
    conn: sqlite3.Connection, kind: str, prefix: str, project_id: str
) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO scoped_counters (project_id, kind, value) VALUES (?, ?, 0)",
        (project_id, kind),
    )
    conn.execute(
        "UPDATE scoped_counters SET value = value + 1 WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    )
    row = conn.execute(
        "SELECT value FROM scoped_counters WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    ).fetchone()
    assert row is not None
    return f"{prefix}{row['value']:03d}"


def next_fact_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def next_intent_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "intent", "i", project_id)



def next_goal_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "goal", "g", project_id)


def next_step_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "step", "s", project_id)


def next_finding_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "finding", "v", project_id)


def next_hint_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


def get_project_or_404(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def check_project_active(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_hint_writable(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] not in ("active", "stopped", "completed"):
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_completed(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "completed":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def validate_facts_exist(
    conn: sqlite3.Connection, project_id: str, fact_ids: list[str]
) -> None:
    for fid in fact_ids:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = ? AND project_id = ?", (fid, project_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Fact {fid} not found")


def validate_goal_not_in_sources(fact_ids: list[str]) -> None:
    if "goal" in fact_ids:
        raise HTTPException(400, "goal cannot be used in from")




def validate_goal_exists(conn: sqlite3.Connection, project_id: str, goal_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM goals WHERE id = ? AND project_id = ?", (goal_id, project_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Goal {goal_id} not found")


def get_goal_or_404(conn: sqlite3.Connection, project_id: str, goal_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM goals WHERE id = ? AND project_id = ?", (goal_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Goal not found")
    return row


def goal_to_model(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"], description=row["description"],
        parent_goal_id=row["parent_goal_id"], status=row["status"],
        priority=row["priority"], created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def build_goals(conn: sqlite3.Connection, project_id: str) -> list[Goal]:
    rows = conn.execute(
        "SELECT * FROM goals WHERE project_id = ? ORDER BY priority, created_at",
        (project_id,),
    ).fetchall()
    return [goal_to_model(r) for r in rows]


def check_all_top_goals_completed(conn: sqlite3.Connection, project_id: str) -> bool:
    rows = conn.execute(
        "SELECT 1 FROM goals WHERE project_id = ? AND parent_goal_id IS NULL AND status != 'completed'",
        (project_id,),
    ).fetchall()
    return len(rows) == 0


def step_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Step:
    sources = conn.execute(
        "SELECT fact_id FROM step_sources WHERE step_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Step(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"], description=row["description"],
        goal_id=row["goal_id"], priority=row["priority"],
        creator=row["creator"], worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"], created_at=row["created_at"],
        concluded_at=row["concluded_at"], abandoned=bool(row["abandoned"]),
    )


def build_steps(conn: sqlite3.Connection, project_id: str) -> list[Step]:
    rows = conn.execute(
        "SELECT * FROM steps WHERE project_id = ? ORDER BY priority DESC, created_at",
        (project_id,),
    ).fetchall()
    return [step_to_model(conn, r, project_id) for r in rows]


def get_step_or_404(conn: sqlite3.Connection, project_id: str, step_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM steps WHERE id = ? AND project_id = ?", (step_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Step not found")
    return row


def get_claimable_open_step_or_404(conn: sqlite3.Connection, project_id: str, step_id: str, worker: str) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_step_or_404(conn, project_id, step_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Step already concluded")
    if row["abandoned"]:
        raise HTTPException(409, "Step is abandoned")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Step is currently claimed by {row['worker']}")
    return row


def get_releasable_open_step_or_404(conn: sqlite3.Connection, project_id: str, step_id: str, worker: str) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_step_or_404(conn, project_id, step_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Step already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Step is currently claimed by {row['worker']}")
    return row


def finding_to_model(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"], title=row["title"], description=row["description"],
        severity=row["severity"], fact_id=row["fact_id"], created_at=row["created_at"],
    )


def build_findings(conn: sqlite3.Connection, project_id: str) -> list[Finding]:
    rows = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [finding_to_model(r) for r in rows]


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _contains_non_negated_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for marker in markers:
        needle = marker.lower()
        start = 0
        while True:
            idx = lowered.find(needle, start)
            if idx == -1:
                break
            prefix = lowered[max(0, idx - 14) : idx]
            suffix = lowered[idx + len(needle) : idx + len(needle) + 14]
            suffix_negated = (
                needle in PROOF_MARKERS_REQUIRING_SUFFIX_CHECK
                and _contains_any(suffix, NEGATED_PROOF_SUFFIX_MARKERS)
            )
            prefix_negated = _contains_any(prefix, NEGATED_PROOF_PREFIX_MARKERS)
            if not prefix_negated and not suffix_negated:
                return True
            start = idx + max(len(needle), 1)
    return False


def _contains_unsatisfied_completion(text: str) -> bool:
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in UNSATISFIED_COMPLETION_PATTERNS
    )


def validate_completion_evidence_for_goal(
    conn: sqlite3.Connection,
    project_id: str,
    fact_ids: list[str],
    completion_description: str,
) -> None:
    goal_row = conn.execute(
        "SELECT description FROM goals WHERE project_id = ? AND status = 'active' AND parent_goal_id IS NULL LIMIT 1",
        (project_id,),
    ).fetchone()
    if goal_row is None:
        return

    goal = str(goal_row["description"])
    needs_server_access = _contains_any(goal, SERVER_ACCESS_GOAL_MARKERS)
    needs_login = _contains_any(goal, LOGIN_GOAL_MARKERS)
    if not needs_server_access and not needs_login:
        return
    if _contains_unsatisfied_completion(completion_description):
        raise HTTPException(
            422,
            {
                "code": "goal_evidence_insufficient",
                "message": (
                    "Completion describes the login/server-access goal as unsatisfied. "
                    "Record it as a fact and continue Reason/Explore instead."
                ),
            },
        )

    source_rows = conn.execute(
        f"""
        SELECT description
        FROM facts
        WHERE project_id = ?
          AND id IN ({",".join("?" for _ in fact_ids)})
        """,
        (project_id, *fact_ids),
    ).fetchall()
    evidence = "\n".join(
        [completion_description, *(str(row["description"]) for row in source_rows)]
    )

    has_server_access_proof = _contains_non_negated_any(
        evidence, SERVER_ACCESS_PROOF_MARKERS
    )
    has_login_proof = _contains_non_negated_any(evidence, LOGIN_PROOF_MARKERS)
    if (needs_server_access and has_server_access_proof) or (needs_login and has_login_proof):
        return

    raise HTTPException(
        422,
        {
            "code": "goal_evidence_insufficient",
            "message": (
                "Completion evidence does not prove the requested login or server-access goal. "
                "Record it as a fact and continue Reason/Explore instead."
            ),
        },
    )


def validate_intent_creator_worker(creator: str, worker: str | None) -> None:
    if worker is not None and worker != creator:
        raise HTTPException(400, "worker must be null or equal to creator")


def get_intent_or_404(conn, project_id, intent_id):
    return get_step_or_404(conn, project_id, intent_id)


def get_step_or_404_legacy(
    conn: sqlite3.Connection, project_id: str, intent_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM intents WHERE id = ? AND project_id = ?",
        (intent_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Intent not found")
    return row


def get_claimable_open_intent_or_404(conn, project_id, intent_id, worker):
    return get_claimable_open_step_or_404(conn, project_id, intent_id, worker)


def get_claimable_open_step_or_404_legacy(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_releasable_open_intent_or_404(conn, project_id, intent_id, worker):
    return get_releasable_open_step_or_404(conn, project_id, intent_id, worker)


def get_releasable_open_step_or_404_legacy(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_completion_step_or_409(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM steps WHERE project_id = ? AND to_fact_id = 'goal'",
        (project_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(409, "Completed project is missing its completion step")
    if len(rows) != 1:
        raise HTTPException(409, "Completed project has multiple completion steps")
    return rows[0]


get_completion_intent_or_409 = get_completion_step_or_409


def get_completion_intent_or_409_legacy(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? AND to_fact_id = 'goal'",
        (project_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(409, "Completed project is missing its completion intent")
    if len(rows) != 1:
        raise HTTPException(409, "Completed project has multiple completion intents")
    return rows[0]


def intent_to_model(conn, row, project_id):
    return step_to_model(conn, row, project_id)


def step_to_model_legacy(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Intent:
    sources = conn.execute(
        "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Intent(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"],
        description=row["description"],
        creator=row["creator"],
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        concluded_at=row["concluded_at"],
    )


def build_intents(conn, project_id):
    return build_steps(conn, project_id)


def build_steps_legacy(conn: sqlite3.Connection, project_id: str) -> list[Intent]:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [intent_to_model(conn, r, project_id) for r in rows]


def get_intent_timeout(conn: sqlite3.Connection) -> int:
    return load_settings(conn).intent_timeout


def get_reason_timeout(conn: sqlite3.Connection) -> int:
    return load_settings(conn).reason_timeout


def project_decide_from_row(row: sqlite3.Row) -> ProjectDecide | None:
    keys = row.keys()
    if "decide_worker" in keys:
        worker = row["decide_worker"]
    else:
        worker = row["reason_worker"] if "reason_worker" in keys else None
    if worker is None:
        return None
    def _col(key_old, key_new):
        if key_new in keys:
            return row[key_new]
        return row[key_old] if key_old in keys else ""
    return ProjectDecide(
        worker=worker,
        trigger=_col("reason_trigger", "decide_trigger"),
        started_at=_col("reason_started_at", "decide_started_at"),
        last_heartbeat_at=_col("reason_last_heartbeat_at", "decide_last_heartbeat_at"),
    )


def project_reason_from_row(row: sqlite3.Row) -> ProjectReason | None:
    return project_decide_from_row(row)


def project_meta_from_row(row: sqlite3.Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        decide=project_decide_from_row(row),
    )


def clear_project_decide(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET decide_worker = NULL,
            decide_trigger = NULL,
            decide_started_at = NULL,
            decide_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )


clear_project_reason = clear_project_decide


def clear_project_reason_legacy(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )


def expire_workers(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_intent_timeout(conn)
    now = utcnow()
    query = """
        UPDATE steps
        SET worker = NULL
        WHERE abandoned = 0
        AND to_fact_id IS NULL
          AND worker IS NOT NULL
          AND last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def expire_reason_leases(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_reason_timeout(conn)
    now = utcnow()
    query = """
        UPDATE projects
        SET decide_worker = NULL,
            decide_trigger = NULL,
            decide_started_at = NULL,
            decide_last_heartbeat_at = NULL
        WHERE decide_worker IS NOT NULL
          AND decide_last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(reason_last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


expire_decide_leases = expire_reason_leases
