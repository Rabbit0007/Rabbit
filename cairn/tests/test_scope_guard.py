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


def test_scope_guard_rejects_out_of_scope_intent_creation(temp_db):
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

    assert response.status_code == 422
    assert "scope_violation" in response.text


def test_scope_guard_converts_out_of_scope_conclusion_into_blocked_fact(temp_db):
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
    assert "范围策略阻断" in fact_description
    assert "127.0.0.1" not in fact_description
    assert "宿主机服务" not in fact_description

    follow_up_response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": [blocked_fact["id"]],
            "description": "继续顺着被阻断事实扩展。",
            "creator": "worker-1",
            "worker": None,
        },
    )
    assert follow_up_response.status_code == 422
    assert "scope_blocked_source_fact" in follow_up_response.text


def test_scope_guard_rejects_out_of_scope_completion(temp_db):
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

    assert response.status_code == 422
    assert "scope_violation" in response.text
