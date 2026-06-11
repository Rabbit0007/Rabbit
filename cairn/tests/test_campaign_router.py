from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db


@pytest.fixture
def campaign_app(temp_db) -> FastAPI:
    from cairn.server.routers import campaign

    app = FastAPI()
    app.include_router(campaign.router)
    return app


@pytest.fixture
def client(campaign_app) -> TestClient:
    return TestClient(campaign_app)


def _insert_project(project_id: str, title: str, status: str = "active") -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            (project_id, title, status, "2024-01-01T00:00:00Z"),
        )


def _insert_fact(project_id: str, fact_id: str, description: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            (fact_id, project_id, description),
        )


def _insert_hint(project_id: str, hint_id: str, content: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO hints (id, project_id, content, creator, created_at) VALUES (?, ?, ?, 'tester', ?)",
            (hint_id, project_id, content, "2024-01-01T00:00:00Z"),
        )


def _insert_intent(project_id: str, intent_id: str, description: str, concluded_at: str | None = None) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO intents
                (id, project_id, to_fact_id, description, creator, worker, last_heartbeat_at, created_at, concluded_at)
            VALUES (?, ?, NULL, ?, 'tester', NULL, NULL, ?, ?)
            """,
            (intent_id, project_id, description, "2024-01-01T00:00:00Z", concluded_at),
        )


def _insert_vulnerability(project_id: str, vuln_id: str, fact_id: str, title: str, description: str, severity: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO vulnerabilities
                (id, project_id, fact_id, title, description, severity, discovered_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed')
            """,
            (vuln_id, project_id, fact_id, title, description, severity, "2024-01-01T00:00:00Z"),
        )


def test_campaign_synthesis_aggregates_project_state(client):
    _insert_project("p1", "Project One")
    _insert_fact("p1", "origin", "http://example.test")
    _insert_fact("p1", "goal", "验证目标是否已达成")
    _insert_fact(
        "p1",
        "f001",
        "已确认跨作业未授权读取真实存在：访问 /output/jobA/../jobB/seq.fasta 返回 200，正文为 test data。",
    )
    _insert_fact(
        "p1",
        "f002",
        "继续验证另一条候选路径，但未观察到任何真实执行证据，未发现 uid=、www-data、whoami 输出。",
    )
    _insert_hint("p1", "h001", "Replay script keeps x &amp; y in raw request.")
    _insert_intent("p1", "i001", "继续核对 output 越权读取边界", concluded_at=None)
    _insert_vulnerability(
        "p1",
        "v001",
        "f001",
        "跨作业未授权读取",
        "已确认跨作业未授权读取真实存在，且可直接读取他人结果文件。",
        "high",
    )

    response = client.get("/api/projects/p1/campaign")
    assert response.status_code == 200
    body = response.json()

    assert body["project_id"] == "p1"
    assert body["goal_status"] == "in_progress"
    assert body["counts"]["facts"] == 4
    assert body["counts"]["hints"] == 1
    assert body["counts"]["open_intents"] == 1
    assert body["counts"]["vulnerabilities"] == 1
    assert body["top_findings"][0]["source_type"] == "vulnerability"
    assert "跨作业未授权读取" in body["lead"]
    assert body["open_intents"] == ["继续核对 output 越权读取边界"]
    assert body["blockers"]
    assert any("原文" in step for step in body["next_steps"])


def test_campaign_synthesis_marks_completed_project_as_achieved(client):
    _insert_project("p2", "Project Two", status="completed")
    _insert_fact("p2", "origin", "http://example.test")
    _insert_fact("p2", "goal", "获取系统权限")
    _insert_fact("p2", "f001", "已获得 www-data 交互结果，whoami 输出为 www-data。")

    response = client.get("/api/projects/p2/campaign")
    assert response.status_code == 200
    body = response.json()

    assert body["goal_status"] == "achieved"
    assert body["next_steps"]
