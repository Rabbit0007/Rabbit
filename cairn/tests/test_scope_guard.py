from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app() -> FastAPI:
    from cairn.server.routers import intents, projects

    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(intents.router)
    return app


def _create_scoped_project(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={
            "title": "Scoped project",
            "origin": "http://202.194.20.64/ 授权测试目标",
            "goal": "验证目标站点可达攻击路径并形成报告",
            "hints": [
                {
                    "content": "只渗透 202.194.20.64 这个 ip 地址，不要碰宿主机和 localhost。",
                    "creator": "admin",
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_scope_text_does_not_reject_intent_evidence_at_api_boundary(temp_db):
    client = TestClient(_app())
    project = _create_scoped_project(client)
    project_id = project["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "继续探测 127.0.0.1:3000 宿主机服务",
            "creator": "worker-1",
            "worker": None,
        },
    )

    assert response.status_code == 201


def test_scope_text_does_not_block_newly_reported_targets_at_api_boundary(temp_db):
    client = TestClient(_app())
    response = client.post(
        "/projects",
        json={
            "title": "Strict by default",
            "origin": "http://10.102.35.135/ http://10.102.35.134/",
            "goal": "验证声明目标",
        },
    )
    assert response.status_code == 201
    project_id = response.json()["project"]["id"]

    blocked = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "继续探测 127.0.0.1:8000 和 10.102.35.200",
            "creator": "worker-1",
            "worker": None,
        },
    )
    assert blocked.status_code == 201

    allowed = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "继续验证 10.102.35.135 的 HTTP 服务",
            "creator": "worker-1",
            "worker": None,
        },
    )
    assert allowed.status_code == 201


def test_scope_guard_preserves_conclusion_evidence(temp_db):
    client = TestClient(_app())
    project = _create_scoped_project(client)
    project_id = project["project"]["id"]

    intent_response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "检查 202.194.20.64:80 是否存在代理行为",
            "creator": "worker-1",
            "worker": "worker-1",
        },
    )
    assert intent_response.status_code == 201
    intent_id = intent_response.json()["id"]

    conclude_response = client.post(
        f"/projects/{project_id}/intents/{intent_id}/conclude",
        json={
            "worker": "worker-1",
            "description": "通过 202.194.20.64:80 可进一步访问 127.0.0.1:3000 宿主机服务。",
        },
    )

    assert conclude_response.status_code == 200
    blocked_fact = conclude_response.json()["fact"]
    fact_description = blocked_fact["description"]
    assert fact_description == "通过 202.194.20.64:80 可进一步访问 127.0.0.1:3000 宿主机服务。"

    follow_up_response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": [blocked_fact["id"]],
            "description": "继续顺着被阻断事实扩展。",
            "creator": "worker-1",
            "worker": None,
        },
    )
    assert follow_up_response.status_code == 201


def test_scope_text_does_not_reject_completion_evidence(temp_db):
    client = TestClient(_app())
    project = _create_scoped_project(client)
    project_id = project["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/complete",
        json={
            "from": ["origin"],
            "description": "已经通过 127.0.0.1:8080 宿主机接口完成利用。",
            "worker": "reason-1",
        },
    )

    assert response.status_code == 200
