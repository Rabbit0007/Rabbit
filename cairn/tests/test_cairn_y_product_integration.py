from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.finding_projection import sync_project_findings
from cairn.server.report_agent_service import _apply_enrichments, _load_project_material
from cairn.server.routers import findings, steps


def _client(temp_db) -> TestClient:
    app = FastAPI()
    app.include_router(steps.router)
    app.include_router(findings.router)
    return TestClient(app)


def _seed_project(*, with_step: bool = True) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES ('p1', 'Rabbit', 'active', '2026-01-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO facts VALUES ('origin', 'p1', 'initial context')")
        conn.execute(
            "INSERT INTO goals (id, project_id, description, status, priority, created_at) "
            "VALUES ('g001', 'p1', 'finish the search', 'active', 0, '2026-01-01T00:00:00Z')"
        )
        if with_step:
            conn.execute(
                "INSERT INTO steps (id, project_id, description, goal_id, creator, created_at) "
                "VALUES ('s001', 'p1', 'inspect one branch', 'g001', 'Decide', '2026-01-01T00:00:01Z')"
            )
            conn.execute("INSERT INTO step_sources VALUES ('s001', 'p1', 'origin')")


def test_execute_conclusion_atomically_creates_fact_finding_and_product_projection(temp_db):
    _seed_project()
    response = _client(temp_db).post(
        "/projects/p1/steps/s001/conclude",
        json={
            "worker": "worker-1",
            "description": "reproducible result",
            "finding": {
                "title": "confirmed issue",
                "description": "structured evidence",
                "severity": "high",
                "kind": "security_vulnerability",
                "data": {"proof": "packet-1"},
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["step"]["to"] == body["fact"]["id"]
    assert body["finding"]["fact_id"] == body["fact"]["id"]

    with db.get_conn() as conn:
        projected = conn.execute(
            "SELECT finding_id, fact_id, title FROM vulnerabilities WHERE project_id='p1'"
        ).fetchone()
    assert dict(projected) == {
        "finding_id": body["finding"]["id"],
        "fact_id": body["fact"]["id"],
        "title": "confirmed issue",
    }


def test_non_reportable_finding_never_enters_product_projection(temp_db):
    _seed_project(with_step=False)
    response = _client(temp_db).post(
        "/projects/p1/findings",
        json={
            "title": "useful observation",
            "description": "not a report item",
            "severity": "info",
            "kind": "observation",
            "fact_id": "origin",
        },
    )
    assert response.status_code == 201
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM vulnerabilities").fetchone()[0] == 0


def test_multiple_native_findings_can_reference_one_fact(temp_db):
    _seed_project(with_step=False)
    client = _client(temp_db)
    for title in ("first", "second"):
        response = client.post(
            "/projects/p1/findings",
            json={
                "title": title,
                "description": f"{title} evidence",
                "severity": "medium",
                "kind": "vulnerability",
                "fact_id": "origin",
            },
        )
        assert response.status_code == 201, response.text
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM vulnerabilities").fetchone()[0] == 2


def test_report_agent_only_enriches_existing_findings_and_projection_is_stable(temp_db):
    _seed_project(with_step=False)
    client = _client(temp_db)
    native = client.post(
        "/projects/p1/findings",
        json={
            "title": "native title",
            "description": "native evidence",
            "severity": "low",
            "kind": "security_vulnerability",
            "fact_id": "origin",
        },
    ).json()

    _apply_enrichments(
        "p1",
        [
            {"finding_id": "invented", "title": "must not exist"},
            {"finding_id": native["id"], "title": "polished title", "proof": "same evidence"},
        ],
    )
    with db.get_conn() as conn:
        sync_project_findings(conn, "p1")
        row = conn.execute(
            "SELECT title, severity FROM vulnerabilities WHERE finding_id = ?",
            (native["id"],),
        ).fetchone()
        finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        data = json.loads(
            conn.execute("SELECT data_json FROM findings WHERE id = ?", (native["id"],)).fetchone()[0]
        )
    assert finding_count == 1
    assert dict(row) == {"title": "polished title", "severity": "low"}
    assert data["report_enrichment"]["title"] == "polished title"


def test_fact_text_alone_is_not_sent_as_a_report_candidate(temp_db):
    _seed_project(with_step=False)
    material = _load_project_material("p1", "Rabbit")
    assert material["facts"]
    assert material["findings"] == []
