from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from datetime import datetime
import yaml

from cairn.server.db import get_conn
from cairn.server.services import (
    expire_decide_leases,
    expire_workers,
    get_project_or_404,
    parse_json_object,
)
from cairn.server.text_normalization import normalize_hint_content

router = APIRouter(tags=["export"])


def format_export_timestamp(value: str | None) -> str | None:
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_project_data(conn, project_id: str):
    expire_workers(conn, project_id)
    expire_decide_leases(conn, project_id)
    proj = get_project_or_404(conn, project_id)

    facts = conn.execute(
        "SELECT id, description FROM facts WHERE project_id = ?", (project_id,)
    ).fetchall()
    hints = conn.execute(
        "SELECT content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    steps = conn.execute(
        "SELECT * FROM steps WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    goals = conn.execute(
        "SELECT * FROM goals WHERE project_id = ? ORDER BY priority, created_at",
        (project_id,),
    ).fetchall()
    findings = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()

    sources_by_step = {}
    for s in steps:
        rows = conn.execute(
            "SELECT fact_id FROM step_sources WHERE step_id = ? AND project_id = ? ORDER BY rowid",
            (s["id"], project_id),
        ).fetchall()
        sources_by_step[s["id"]] = [r["fact_id"] for r in rows]

    sources_by_goal = {}
    for goal in goals:
        rows = conn.execute(
            "SELECT fact_id FROM goal_sources WHERE goal_id = ? AND project_id = ? ORDER BY rowid",
            (goal["id"], project_id),
        ).fetchall()
        sources_by_goal[goal["id"]] = [row["fact_id"] for row in rows]

    return proj, facts, hints, steps, goals, findings, sources_by_step, sources_by_goal


def _export_yaml(conn, project_id: str) -> str:
    proj, facts, hints, steps, goals, findings, sources_by_step, sources_by_goal = _load_project_data(conn, project_id)

    origin_desc = ""
    for f in facts:
        if f["id"] == "origin":
            origin_desc = f["description"]

    data: dict = {
        "project": {
            "title": proj["title"],
            "origin": origin_desc,
        }
    }

    if goals:
        data["goals"] = [
            {
                "id": g["id"],
                "description": g["description"],
                "parent_goal_id": g["parent_goal_id"],
                "status": g["status"],
                "priority": g["priority"],
                "created_at": format_export_timestamp(g["created_at"]),
                "completed_at": format_export_timestamp(g["completed_at"]),
                "completion_description": g["completion_description"],
                "completed_by": g["completed_by"],
                "from": sources_by_goal.get(g["id"], []),
            }
            for g in goals
        ]

    if hints:
        data["hints"] = [
            {
                "content": normalize_hint_content(h["content"]),
                "creator": h["creator"],
                "created_at": format_export_timestamp(h["created_at"]),
            }
            for h in hints
        ]

    data["facts"] = [{"id": f["id"], "description": f["description"]} for f in facts]

    step_list = []
    for s in steps:
        entry: dict = {
            "from": sources_by_step.get(s["id"], []),
            "to": s["to_fact_id"],
            "description": s["description"],
            "goal_id": s["goal_id"],
            "priority": s["priority"],
            "creator": s["creator"],
            "worker": s["worker"],
            "abandoned": bool(s["abandoned"]),
            "created_at": format_export_timestamp(s["created_at"]),
            "concluded_at": format_export_timestamp(s["concluded_at"]),
        }
        step_list.append(entry)

    if step_list:
        data["steps"] = step_list

    if findings:
        data["findings"] = [
            {
                "id": v["id"],
                "title": v["title"],
                "description": v["description"],
                "severity": v["severity"],
                "kind": v["kind"],
                "data": parse_json_object(v["data_json"]),
                "fact_id": v["fact_id"],
                "created_at": format_export_timestamp(v["created_at"]),
            }
            for v in findings
        ]

    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _export_timeline(conn, project_id: str) -> str:
    proj, facts, hints, steps, goals, findings, sources_by_step, sources_by_goal = _load_project_data(conn, project_id)

    facts_by_id = {f["id"]: f["description"] for f in facts}

    events: list[tuple[str, int, str]] = []  # (timestamp, order, text)
    order = 0

    origin_desc = facts_by_id.get("origin", "")
    goal_desc = ", ".join(g["description"] for g in goals if g["parent_goal_id"] is None)
    ts = format_export_timestamp(proj["created_at"]) or ""
    block = f"[{ts}] PROJECT CREATED\n  origin: {origin_desc}\n  goal: {goal_desc}"
    events.append((proj["created_at"] or "", order, block))
    order += 1

    for g in goals:
        ts = format_export_timestamp(g["created_at"]) or ""
        block = f"[{ts}] GOAL CREATED {g['id']}\n  {g['description']}"
        events.append((g["created_at"] or "", order, block))
        order += 1

    for h in hints:
        ts = format_export_timestamp(h["created_at"]) or ""
        block = f"[{ts}] HINT by {h['creator']}\n  {h['content']}"
        events.append((h["created_at"] or "", order, block))
        order += 1

    for s in steps:
        src = sources_by_step.get(s["id"], [])
        from_str = ", ".join(src)

        ts = format_export_timestamp(s["created_at"]) or ""
        meta = f"  from: {from_str}"
        if s["goal_id"]:
            meta += f"\n  goal: {s['goal_id']}"
        if s["priority"]:
            meta += f"\n  priority: {s['priority']}"
        if s["worker"] and not s["concluded_at"]:
            meta += f"\n  worker: {s['worker']} (in progress)"
        block = f"[{ts}] STEP DECLARED {s['id']} by {s['creator']}\n{meta}\n  {s['description']}"
        events.append((s["created_at"] or "", order, block))
        order += 1

        if not s["concluded_at"] or not s["to_fact_id"]:
            continue

        ts = format_export_timestamp(s["concluded_at"]) or ""
        actor = s["worker"] or s["creator"]
        fact_desc = facts_by_id.get(s["to_fact_id"], "")
        block = f"[{ts}] STEP CONCLUDED {s['id']} by {actor}\n  from: {from_str}\n  produced: {s['to_fact_id']}\n  {fact_desc}"
        events.append((s["concluded_at"] or "", order, block))
        order += 1

    # Goal completion is causally downstream of its evidence Facts. Assign it
    # after Step conclusion events so second-resolution timestamps still replay
    # in FGS order rather than showing a Goal complete before its evidence exists.
    for g in goals:
        if not g["completed_at"]:
            continue
        ts = format_export_timestamp(g["completed_at"]) or ""
        sources = ", ".join(sources_by_goal.get(g["id"], []))
        actor = g["completed_by"] or "unknown"
        evidence = g["completion_description"] or g["description"]
        block = f"[{ts}] GOAL COMPLETED {g['id']} by {actor}\n  from: {sources}\n  {evidence}"
        events.append((g["completed_at"] or "", order, block))
        order += 1

    for v in findings:
        ts = format_export_timestamp(v["created_at"]) or ""
        block = f"[{ts}] FINDING {v['kind']} [{v['severity']}] {v['title']}\n  {v['description']}"
        events.append((v["created_at"] or "", order, block))
        order += 1

    events.sort(key=lambda e: (e[0], e[1]))

    return "\n\n".join(e[2] for e in events) + "\n"


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, format: str = "yaml"):
    if format not in ("yaml", "timeline"):
        raise HTTPException(400, "Supported formats: yaml, timeline")

    with get_conn() as conn:
        if format == "timeline":
            text = _export_timeline(conn, project_id)
        else:
            text = _export_yaml(conn, project_id)

        return Response(content=text, media_type="text/plain")
