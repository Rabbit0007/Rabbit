from fastapi import APIRouter, HTTPException

from cairn.project_context_files import ensure_project_context_file
from cairn.server.db import get_conn
from cairn.server.models import (
    CompleteRequest,
    CreateProjectRequest,
    DecideClaimRequest,
    Fact,
    Goal,
    Hint,
    HeartbeatRequest,
    ProjectDetail,
    ProjectMeta,
    ProjectSummary,
    ReopenRequest,
    ReopenResponse,
    UpdateProjectTitleRequest,
    UpdateProjectStatusRequest,
)
from cairn.server.services import (
    build_findings,
    build_goals,
    build_steps,
    check_all_top_goals_completed,
    check_project_completed,
    check_project_active,
    clear_project_decide,
    expire_decide_leases,
    expire_workers,
    get_project_or_404,
    goal_to_model,
    next_fact_id,
    next_goal_id,
    next_hint_id,
    next_step_id,
    next_project_id,
    project_decide_from_row,
    project_meta_from_row,
    step_to_model,
    utcnow,
    validate_facts_exist,
)

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects():
    with get_conn() as conn:
        expire_workers(conn)
        expire_decide_leases(conn)
        rows = conn.execute("""
            SELECT p.*,
                (SELECT COUNT(*) FROM facts WHERE project_id = p.id) AS fact_count,
                (SELECT COUNT(*) FROM steps WHERE project_id = p.id) AS step_count,
                (SELECT COUNT(*) FROM steps WHERE project_id = p.id AND concluded_at IS NULL AND worker IS NOT NULL AND abandoned = 0) AS working_step_count,
                (SELECT COUNT(*) FROM steps WHERE project_id = p.id AND concluded_at IS NULL AND worker IS NULL AND abandoned = 0) AS unclaimed_step_count,
                (SELECT COUNT(*) FROM goals WHERE project_id = p.id) AS goal_count,
                (SELECT COUNT(*) FROM findings WHERE project_id = p.id) AS finding_count,
                (SELECT COUNT(*) FROM hints WHERE project_id = p.id) AS hint_count
            FROM projects p
            ORDER BY p.created_at
        """).fetchall()
        return [
            ProjectSummary(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                bootstrap_enabled=bool(row["bootstrap_enabled"]),
                created_at=row["created_at"],
                decide=project_decide_from_row(row),
                fact_count=row["fact_count"],
                intent_count=row["step_count"],  # backward compat
                working_intent_count=row["working_step_count"],
                unclaimed_intent_count=row["unclaimed_step_count"],
                step_count=row["step_count"],
                working_step_count=row["working_step_count"],
                unclaimed_step_count=row["unclaimed_step_count"],
                goal_count=row["goal_count"],
                finding_count=row["finding_count"],
                hint_count=row["hint_count"],
            )
            for row in rows
        ]


@router.post("/projects", response_model=ProjectDetail, status_code=201)
def create_project(body: CreateProjectRequest):
    with get_conn() as conn:
        pid = next_project_id(conn)
        now = utcnow()

        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, created_at) "
            "VALUES (?, ?, 'active', 1, ?)",
            (pid, body.title, now),
        )
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            ("origin", pid, body.origin),
        )

        # Create the main goal (no longer a Fact)
        gid = next_goal_id(conn, pid)
        conn.execute(
            "INSERT INTO goals (id, project_id, description, parent_goal_id, status, priority, created_at, completed_at) "
            "VALUES (?, ?, ?, NULL, 'active', 0, ?, NULL)",
            (gid, pid, body.goal, now),
        )

        hints = []
        if body.hints:
            for h in body.hints:
                hid = next_hint_id(conn, pid)
                conn.execute(
                    "INSERT INTO hints (id, project_id, content, creator, created_at) VALUES (?, ?, ?, ?, ?)",
                    (hid, pid, h.content, h.creator, now),
                )
                hints.append(Hint(id=hid, content=h.content, creator=h.creator, created_at=now))

        ensure_project_context_file(pid, body.origin, body.goal)

        return ProjectDetail(
            project=ProjectMeta(
                id=pid,
                title=body.title,
                status="active",
                bootstrap_enabled=True,
                created_at=now,
                decide=None,
            ),
            facts=[Fact(id="origin", description=body.origin)],
            steps=[],
            goals=[Goal(id=gid, description=body.goal, parent_goal_id=None,
                        status="active", priority=0, created_at=now, completed_at=None)],
            findings=[],
            hints=hints,
        )


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str):
    with get_conn() as conn:
        expire_workers(conn, project_id)
        expire_decide_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)

        facts = conn.execute(
            "SELECT * FROM facts WHERE project_id = ?", (project_id,)
        ).fetchall()

        return ProjectDetail(
            project=project_meta_from_row(row),
            facts=[Fact(**dict(f)) for f in facts],
            steps=build_steps(conn, project_id),
            goals=build_goals(conn, project_id),
            findings=build_findings(conn, project_id),
            hints=[Hint(**dict(h)) for h in conn.execute(
                "SELECT * FROM hints WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()],
        )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


@router.put("/projects/{project_id}/title", response_model=ProjectMeta)
def update_project_title(project_id: str, body: UpdateProjectTitleRequest):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        conn.execute(
            "UPDATE projects SET title = ? WHERE id = ?",
            (body.title, project_id),
        )
        updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return project_meta_from_row(updated)


@router.put("/projects/{project_id}/status", response_model=ProjectMeta)
def update_project_status(project_id: str, body: UpdateProjectStatusRequest):
    with get_conn() as conn:
        expire_decide_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current_status = row["status"]
        if current_status == "completed":
            raise HTTPException(409, "Completed projects cannot change status")
        if current_status == body.status:
            return project_meta_from_row(row)

        conn.execute(
            "UPDATE projects SET status = ? WHERE id = ?",
            (body.status, project_id),
        )
        if body.status == "stopped":
            conn.execute(
                "UPDATE steps SET worker = NULL WHERE project_id = ? AND concluded_at IS NULL",
                (project_id,),
            )
            clear_project_decide(conn, project_id)
        updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return project_meta_from_row(updated)


# ── Decide (was Reason) endpoints ──────────────────────────────────────

@router.post("/projects/{project_id}/decide/claim", response_model=ProjectMeta)
def claim_project_decide(project_id: str, body: DecideClaimRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_decide_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current_worker = row["decide_worker"]
        if current_worker is not None and current_worker != body.worker:
            raise HTTPException(409, f"Project decide is currently claimed by {current_worker}")
        if current_worker == body.worker:
            return project_meta_from_row(row)

        now = utcnow()
        conn.execute(
            """
            UPDATE projects
            SET decide_worker = ?,
                decide_trigger = ?,
                decide_started_at = ?,
                decide_last_heartbeat_at = ?
            WHERE id = ?
            """,
            (body.worker, body.trigger, now, now, project_id),
        )
        updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return project_meta_from_row(updated)


@router.post("/projects/{project_id}/decide/heartbeat", response_model=ProjectMeta)
def heartbeat_project_decide(project_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_decide_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current_worker = row["decide_worker"]
        if current_worker is None:
            raise HTTPException(409, "Project decide is not currently claimed")
        if current_worker != body.worker:
            raise HTTPException(409, f"Project decide is currently claimed by {current_worker}")

        now = utcnow()
        conn.execute(
            "UPDATE projects SET decide_last_heartbeat_at = ? WHERE id = ?",
            (now, project_id),
        )
        updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return project_meta_from_row(updated)


@router.post("/projects/{project_id}/decide/release", response_model=ProjectMeta)
def release_project_decide(project_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_decide_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current_worker = row["decide_worker"]
        if current_worker is None:
            return project_meta_from_row(row)
        if current_worker != body.worker:
            raise HTTPException(409, f"Project decide is currently claimed by {current_worker}")

        clear_project_decide(conn, project_id)
        updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return project_meta_from_row(updated)


# ── Backward-compat: reason endpoints ──────────────────────────────────

@router.post("/projects/{project_id}/reason/claim", response_model=ProjectMeta)
def claim_project_reason(project_id: str, body: DecideClaimRequest):
    return claim_project_decide(project_id, body)


@router.post("/projects/{project_id}/reason/heartbeat", response_model=ProjectMeta)
def heartbeat_project_reason(project_id: str, body: HeartbeatRequest):
    return heartbeat_project_decide(project_id, body)


@router.post("/projects/{project_id}/reason/release", response_model=ProjectMeta)
def release_project_reason(project_id: str, body: HeartbeatRequest):
    return release_project_decide(project_id, body)


# ── Complete / Reopen ──────────────────────────────────────────────────

@router.post("/projects/{project_id}/complete", response_model=Goal)
def complete_project(project_id: str, body: CompleteRequest):
    """Legacy endpoint backed by real Goal completion, without a synthetic Step."""
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_decide_leases(conn, project_id)
        validate_facts_exist(conn, project_id, body.from_)

        # Find the first active top-level goal
        goal = conn.execute(
            "SELECT * FROM goals WHERE project_id = ? AND status = 'active' AND parent_goal_id IS NULL "
            "ORDER BY priority, created_at LIMIT 1",
            (project_id,),
        ).fetchone()
        if goal is None:
            raise HTTPException(409, "No active goal to complete")

        now = utcnow()
        conn.execute(
            "UPDATE goals SET status = 'completed', completed_at = ?, completion_description = ?, completed_by = ? "
            "WHERE id = ? AND project_id = ?",
            (now, body.description, body.worker, goal["id"], project_id),
        )

        for fid in body.from_:
            conn.execute(
                "INSERT INTO goal_sources (goal_id, project_id, fact_id) VALUES (?, ?, ?)",
                (goal["id"], project_id, fid),
            )

        if check_all_top_goals_completed(conn, project_id):
            conn.execute(
                "UPDATE projects SET status = 'completed', "
                "decide_worker = NULL, decide_trigger = NULL, "
                "decide_started_at = NULL, decide_last_heartbeat_at = NULL "
                "WHERE id = ?",
                (project_id,),
            )
            conn.execute(
                "UPDATE steps SET worker = NULL WHERE project_id = ? AND concluded_at IS NULL",
                (project_id,),
            )

        updated_goal = conn.execute(
            "SELECT * FROM goals WHERE id = ? AND project_id = ?",
            (goal["id"], project_id),
        ).fetchone()
        return goal_to_model(conn, updated_goal, project_id)


@router.post("/projects/{project_id}/reopen", response_model=ReopenResponse)
def reopen_project(project_id: str, body: ReopenRequest):
    with get_conn() as conn:
        expire_decide_leases(conn, project_id)
        check_project_completed(conn, project_id)

        now = utcnow()
        fact_id = next_fact_id(conn, project_id)
        step_id = next_step_id(conn, project_id)

        # Reset all completed goals to active
        completed_goals = conn.execute(
            "SELECT * FROM goals WHERE project_id = ? AND status = 'completed'",
            (project_id,),
        ).fetchall()
        source_rows = conn.execute(
            "SELECT DISTINCT fact_id FROM goal_sources WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        reopen_sources = [row["fact_id"] for row in source_rows] or ["origin"]
        for g in completed_goals:
            conn.execute(
                "UPDATE goals SET status = 'active', completed_at = NULL, completion_description = NULL, "
                "completed_by = NULL WHERE id = ? AND project_id = ?",
                (g["id"], project_id),
            )
            conn.execute(
                "DELETE FROM goal_sources WHERE goal_id = ? AND project_id = ?",
                (g["id"], project_id),
            )

        # Create fact recording the reopen
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            (fact_id, project_id, body.description),
        )
        conn.execute(
            "INSERT INTO steps (id, project_id, to_fact_id, description, goal_id, priority, "
            "creator, worker, last_heartbeat_at, created_at, concluded_at, abandoned) "
            "VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, 0)",
            (step_id, project_id, fact_id, "external_feedback", body.creator, body.creator, now, now, now),
        )
        for source_id in reopen_sources:
            conn.execute(
                "INSERT INTO step_sources (step_id, project_id, fact_id) VALUES (?, ?, ?)",
                (step_id, project_id, source_id),
            )

        clear_project_decide(conn, project_id)
        conn.execute(
            "UPDATE projects SET status = 'active' WHERE id = ?",
            (project_id,),
        )

        updated_project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        updated_step = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND project_id = ?",
            (step_id, project_id),
        ).fetchone()
        assert updated_project is not None
        assert updated_step is not None

        first_goal = completed_goals[0] if completed_goals else None
        return ReopenResponse(
            project=project_meta_from_row(updated_project),
            fact=Fact(id=fact_id, description=body.description),
            step=step_to_model(conn, updated_step, project_id),
            goal=Goal(
                id=first_goal["id"], description=first_goal["description"],
                parent_goal_id=first_goal["parent_goal_id"], status="active",
                priority=first_goal["priority"], created_at=first_goal["created_at"],
                completed_at=None,
            ) if first_goal else None,
        )
