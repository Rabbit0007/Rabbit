from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
import yaml

from cairn.server import db
from cairn.server.app import app
from conftest import configure_authenticated_test_client


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    headers = configure_authenticated_test_client(monkeypatch)
    with TestClient(app, headers=headers) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/projects",
        json={
            "title": "test",
            "origin": "starting point",
            "goal": "finish",
            "hints": [{"content": "initial clue", "creator": "human"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["bootstrap_enabled"] is True
    return response.json()["project"]["id"]


def _get_goal_id(client: TestClient, project_id: str) -> str:
    detail = client.get(f"/projects/{project_id}").json()
    return detail["goals"][0]["id"]


def test_project_workflow_create_conclude_complete_and_reopen(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)

    # Create a step
    response = client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "investigate", "creator": "decider", "goal_id": goal_id},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "s001"

    # Heartbeat / claim
    response = client.post(
        f"/projects/{project_id}/steps/s001/heartbeat",
        json={"worker": "executor"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "executor"

    # Conclude step → produces fact
    response = client.post(
        f"/projects/{project_id}/steps/s001/conclude",
        json={"worker": "executor", "description": "new fact"},
    )
    assert response.status_code == 200
    assert response.json()["fact"] == {"id": "f001", "description": "new fact"}

    # Complete goal
    response = client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["f001"], "description": "solved", "worker": "decider"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    # Project should be completed
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["project"]["status"] == "completed"

    # Reopen
    response = client.post(
        f"/projects/{project_id}/reopen",
        json={"description": "human correction", "creator": "human"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["status"] == "active"
    assert payload["fact"] == {"id": "f002", "description": "human correction"}


def test_stopping_project_releases_claims_and_decide_but_keeps_hints_writable(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/decide/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    response = client.put(f"/projects/{project_id}/status", json={"status": "stopped"})
    assert response.status_code == 200
    assert response.json()["decide"] is None

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["steps"][0]["worker"] is None
    assert client.post(
        f"/projects/{project_id}/hints",
        json={"content": "manual note", "creator": "human"},
    ).status_code == 201
    assert client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "blocked", "creator": "decider", "worker": None},
    ).status_code == 403


def test_step_creation_rejects_empty_from_and_description(client: TestClient) -> None:
    project_id = _create_project(client)
    assert client.post(
        f"/projects/{project_id}/steps",
        json={"from": [], "description": "test", "creator": "decider"},
    ).status_code == 422
    assert client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "", "creator": "decider"},
    ).status_code == 422


def test_settings_and_export_are_backed_by_the_same_database(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.get("/settings")
    assert response.status_code == 200
    assert response.json()["step_timeout"] == 15

    export = client.get(f"/projects/{project_id}/export?format=yaml")
    assert export.status_code == 200
    assert "origin" in export.text
    assert "finish" in export.text


def test_expired_step_and_decide_leases_can_be_reclaimed(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/decide/claim",
        json={"worker": "worker-b", "trigger": "initial"},
    )

    import time
    time.sleep(6.0)
    settings = client.get("/settings").json()
    settings.update({"step_timeout": 5, "decide_timeout": 5})
    response = client.put("/settings", json=settings)
    assert response.status_code == 200

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["steps"][0]["worker"] is None
    assert detail["project"]["decide"] is None


def test_project_list_includes_summary_counts(client: TestClient) -> None:
    project_id = _create_project(client)
    summaries = client.get("/projects").json()
    assert len(summaries) == 1
    s = summaries[0]
    assert s["id"] == project_id
    assert s["fact_count"] == 1  # only origin
    assert s["goal_count"] == 1
    assert s["step_count"] == 0
    assert s["finding_count"] == 0
    assert s["hint_count"] == 1


def test_project_detail_includes_goals(client: TestClient) -> None:
    project_id = _create_project(client)
    detail = client.get(f"/projects/{project_id}").json()
    assert len(detail["goals"]) == 1
    assert detail["goals"][0]["description"] == "finish"
    assert detail["goals"][0]["status"] == "active"
    assert len(detail["facts"]) == 1  # origin only, no goal fact
    assert detail["facts"][0]["id"] == "origin"


def test_create_sub_goal(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)
    response = client.post(
        f"/projects/{project_id}/goals",
        json={"description": "sub task", "parent_goal_id": goal_id, "creator": "decider"},
    )
    assert response.status_code == 201
    assert response.json()["parent_goal_id"] == goal_id


def test_finding_create_and_list(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.post(
        f"/projects/{project_id}/findings",
        json={"title": "SQLi in login", "description": "Found SQL injection", "severity": "high"},
    )
    assert response.status_code == 201
    assert response.json()["severity"] == "high"

    findings = client.get(f"/projects/{project_id}/findings").json()
    assert len(findings) == 1
    assert findings[0]["title"] == "SQLi in login"


def test_goal_completion_rejects_unknown_source_fact(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)
    response = client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["missing"], "description": "invalid", "worker": "decider"},
    )
    assert response.status_code == 404


def test_finding_rejects_unknown_fact_reference(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.post(
        f"/projects/{project_id}/findings",
        json={
            "title": "orphan finding",
            "description": "invalid reference",
            "severity": "info",
            "fact_id": "missing",
        },
    )
    assert response.status_code == 404


def test_goal_completion_keeps_fact_evidence_without_synthetic_step(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)

    response = client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["origin"], "description": "origin proves completion", "worker": "decider"},
    )
    assert response.status_code == 200
    assert response.json()["from"] == ["origin"]
    assert response.json()["completion_description"] == "origin proves completion"
    assert response.json()["completed_by"] == "decider"

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["steps"] == []
    assert detail["goals"][0]["from"] == ["origin"]
    assert client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["origin"], "description": "duplicate", "worker": "decider"},
    ).status_code == 403  # project is already terminal


def test_project_completes_only_after_root_and_sub_goals_are_satisfied(client: TestClient) -> None:
    project_id = _create_project(client)
    root_goal_id = _get_goal_id(client, project_id)
    sub_goal = client.post(
        f"/projects/{project_id}/goals",
        json={"description": "phase", "parent_goal_id": root_goal_id, "creator": "decider"},
    ).json()

    response = client.post(
        f"/projects/{project_id}/goals/{root_goal_id}/complete",
        json={"from": ["origin"], "description": "root met", "worker": "decider"},
    )
    assert response.status_code == 200
    assert client.get(f"/projects/{project_id}").json()["project"]["status"] == "active"

    response = client.post(
        f"/projects/{project_id}/goals/{sub_goal['id']}/complete",
        json={"from": ["origin"], "description": "phase met", "worker": "decider"},
    )
    assert response.status_code == 200
    assert client.get(f"/projects/{project_id}").json()["project"]["status"] == "completed"


def test_sub_goal_deletion_preserves_referenced_graph_state(client: TestClient) -> None:
    project_id = _create_project(client)
    root_goal_id = _get_goal_id(client, project_id)
    sub_goal_id = client.post(
        f"/projects/{project_id}/goals",
        json={"description": "temporary", "parent_goal_id": root_goal_id, "creator": "decider"},
    ).json()["id"]

    assert client.delete(f"/projects/{project_id}/goals/{root_goal_id}").status_code == 409
    assert client.delete(f"/projects/{project_id}/goals/{sub_goal_id}").status_code == 204

    linked_goal_id = client.post(
        f"/projects/{project_id}/goals",
        json={"description": "linked", "parent_goal_id": root_goal_id, "creator": "decider"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "work", "goal_id": linked_goal_id, "creator": "decider"},
    )
    assert client.delete(f"/projects/{project_id}/goals/{linked_goal_id}").status_code == 409


def test_goal_patch_cannot_bypass_evidence_completion(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)
    response = client.patch(
        f"/projects/{project_id}/goals/{goal_id}",
        json={"status": "completed"},
    )
    assert response.status_code == 422


def test_duplicate_fact_sources_are_rejected_before_database_write(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)

    assert client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin", "origin"], "description": "duplicate", "creator": "decider"},
    ).status_code == 422
    assert client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["origin", "origin"], "description": "duplicate", "worker": "decider"},
    ).status_code == 422

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["steps"] == []
    assert detail["goals"][0]["status"] == "active"


def test_claimed_step_cannot_be_mutated_by_decide(client: TestClient) -> None:
    project_id = _create_project(client)
    step = client.post(
        f"/projects/{project_id}/steps",
        json={
            "from": ["origin"],
            "description": "running work",
            "creator": "decider",
            "worker": "executor",
        },
    ).json()

    response = client.patch(
        f"/projects/{project_id}/steps/{step['id']}",
        json={"abandoned": True},
    )
    assert response.status_code == 409
    assert client.get(f"/projects/{project_id}").json()["steps"][0]["abandoned"] is False


def test_completed_goal_cannot_receive_new_sub_goal(client: TestClient) -> None:
    project_id = _create_project(client)
    root_goal_id = _get_goal_id(client, project_id)
    client.post(
        f"/projects/{project_id}/goals",
        json={"description": "keep project active", "parent_goal_id": root_goal_id, "creator": "decider"},
    )
    client.post(
        f"/projects/{project_id}/goals/{root_goal_id}/complete",
        json={"from": ["origin"], "description": "root met", "worker": "decider"},
    )

    response = client.post(
        f"/projects/{project_id}/goals",
        json={"description": "invalid child", "parent_goal_id": root_goal_id, "creator": "decider"},
    )
    assert response.status_code == 409


def test_legacy_complete_endpoint_records_goal_evidence_without_synthetic_step(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/complete",
        json={"from": ["origin"], "description": "legacy evidence", "worker": "decider"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["from"] == ["origin"]
    assert client.get(f"/projects/{project_id}").json()["steps"] == []


def test_reopen_feedback_step_has_fact_sources(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)
    client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": ["origin"], "description": "initial completion", "worker": "decider"},
    )

    response = client.post(
        f"/projects/{project_id}/reopen",
        json={"description": "new external evidence", "creator": "human"},
    )
    assert response.status_code == 200
    assert response.json()["step"]["from"] == ["origin"]
    assert response.json()["step"]["to"] == response.json()["fact"]["id"]


def test_finding_legacy_json_is_safely_normalized_in_detail_list_and_export(client: TestClient) -> None:
    project_id = _create_project(client)
    finding_id = client.post(
        f"/projects/{project_id}/findings",
        json={"title": "legacy", "description": "legacy payload", "data": {"valid": True}},
    ).json()["id"]
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE findings SET data_json = ? WHERE id = ? AND project_id = ?",
            ("not-json", finding_id, project_id),
        )

    assert client.get(f"/projects/{project_id}").json()["findings"][0]["data"] == {}
    assert client.get(f"/projects/{project_id}/findings").json()[0]["data"] == {}
    exported = yaml.safe_load(client.get(f"/projects/{project_id}/export?format=yaml").text)
    assert exported["findings"][0]["data"] == {}


def test_goal_export_contains_evidence_and_timeline_follows_fact_production(client: TestClient) -> None:
    project_id = _create_project(client)
    goal_id = _get_goal_id(client, project_id)
    step_id = client.post(
        f"/projects/{project_id}/steps",
        json={"from": ["origin"], "description": "produce proof", "creator": "decider"},
    ).json()["id"]
    fact_id = client.post(
        f"/projects/{project_id}/steps/{step_id}/conclude",
        json={"worker": "executor", "description": "proof"},
    ).json()["fact"]["id"]
    client.post(
        f"/projects/{project_id}/goals/{goal_id}/complete",
        json={"from": [fact_id], "description": "proof satisfies goal", "worker": "decider"},
    )

    exported = yaml.safe_load(client.get(f"/projects/{project_id}/export?format=yaml").text)
    assert exported["goals"][0]["from"] == [fact_id]
    timeline = client.get(f"/projects/{project_id}/export?format=timeline").text
    assert timeline.index(f"STEP CONCLUDED {step_id}") < timeline.index(f"GOAL COMPLETED {goal_id}")
    assert f"from: {fact_id}" in timeline
