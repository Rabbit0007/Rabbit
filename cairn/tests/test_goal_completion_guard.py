from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.dispatcher.contracts import validate_bootstrap_execute_payload


def _app() -> FastAPI:
    from cairn.server.routers import intents, projects

    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(intents.router)
    return app


def _create_project(client: TestClient, *, goal: str) -> str:
    response = client.post(
        "/projects",
        json={
            "title": "Completion guard",
            "origin": "https://license.jxjinlan.xyz/login",
            "goal": goal,
        },
    )
    assert response.status_code == 201
    return response.json()["project"]["id"]


def _conclude_fact(client: TestClient, project_id: str, description: str) -> str:
    intent_response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "Record confirmed evidence",
            "creator": "worker-1",
            "worker": "worker-1",
        },
    )
    assert intent_response.status_code == 201
    intent_id = intent_response.json()["id"]

    conclude_response = client.post(
        f"/projects/{project_id}/intents/{intent_id}/conclude",
        json={"worker": "worker-1", "description": description},
    )
    assert conclude_response.status_code == 200
    return conclude_response.json()["fact"]["id"]


def test_bootstrap_execute_payload_is_fact_only_even_if_model_outputs_complete():
    kind, data = validate_bootstrap_execute_payload(
        {
            "accepted": True,
            "data": {
                "fact": {
                    "description": "已确认 /api/admin/login 对畸形 JSON 返回 500。"
                },
                "complete": {
                    "description": "错误地声称该 500 已满足登录或服务器权限目标。"
                },
            },
        }
    )

    assert kind == "fact"
    assert data == {"fact_description": "已确认 /api/admin/login 对畸形 JSON 返回 500。"}


def test_login_or_server_goal_rejects_completion_from_generic_500_fact(temp_db):
    client = TestClient(_app())
    project_id = _create_project(client, goal="获取到服务器权限或者成功登录系统")
    fact_id = _conclude_fact(
        client,
        project_id,
        "已确认后台认证接口 /api/admin/login 对 username 数组稳定返回 500，但未取得登录态或服务器权限。",
    )

    response = client.post(
        f"/projects/{project_id}/complete",
        json={
            "from": [fact_id],
            "description": "当前已发现可触发后端异常的漏洞，足以证明可进一步利用。",
            "worker": "reason-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "goal_evidence_insufficient"
    project = client.get(f"/projects/{project_id}").json()["project"]
    assert project["status"] == "active"


def test_login_or_server_goal_rejects_unsatisfied_reason_completion(temp_db):
    client = TestClient(_app())
    project_id = _create_project(client, goal="获取服务器权限或者获取登录权限")
    fact_id = _conclude_fact(
        client,
        project_id,
        "登录接口 username 复合类型会触发稳定 500，但未获得任何有效登录凭据。",
    )

    response = client.post(
        f"/projects/{project_id}/complete",
        json={
            "from": [fact_id],
            "description": (
                "当前已系统验证登录接口、会话伪造、公开构建产物、同源受保护 API、"
                "同 IP 关联子域、主机级路由差异、弱口令复用、公开源码/历史快照/"
                "外部线索等多个方向。结果表明：未获得任何有效登录凭据，未发现可绕过"
                "认证的会话伪造、未授权 API、初始化/调试入口、公开源码或可利用的代理"
                "误配；登录接口虽存在 username 复合类型触发的稳定 500 类型校验缺陷，"
                "但已反复验证暂未形成可利用的认证绕过或服务器权限获取链。基于现有已"
                "确认事实，尚无证据证明已实现“获取服务器权限或者获取登录权限”，"
                "因此从任务判定角度，当前探索已足以证明 Goal 尚未达成且没有待完成的开放意图。"
            ),
            "worker": "reason-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "goal_evidence_insufficient"
    project = client.get(f"/projects/{project_id}").json()["project"]
    assert project["status"] == "active"


def test_server_goal_rejects_failed_whoami_as_completion_proof(temp_db):
    client = TestClient(_app())
    project_id = _create_project(client, goal="获取服务器权限")
    fact_id = _conclude_fact(
        client,
        project_id,
        "已尝试通过疑似 RCE 执行 whoami，但请求失败且未返回命令输出。",
    )

    response = client.post(
        f"/projects/{project_id}/complete",
        json={
            "from": [fact_id],
            "description": "已发现疑似命令执行入口，准备作为完成证据归档。",
            "worker": "reason-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "goal_evidence_insufficient"
    project = client.get(f"/projects/{project_id}").json()["project"]
    assert project["status"] == "active"


def test_login_or_server_goal_accepts_explicit_login_proof(temp_db):
    client = TestClient(_app())
    project_id = _create_project(client, goal="获取到服务器权限或者成功登录系统")
    fact_id = _conclude_fact(
        client,
        project_id,
        "已使用有效管理员凭据成功登录系统，访问 /admin 返回 200 并进入管理后台。",
    )

    response = client.post(
        f"/projects/{project_id}/complete",
        json={
            "from": [fact_id],
            "description": "已成功登录系统并进入管理后台，满足登录目标。",
            "worker": "reason-1",
        },
    )

    assert response.status_code == 200
    project = client.get(f"/projects/{project_id}").json()["project"]
    assert project["status"] == "completed"
