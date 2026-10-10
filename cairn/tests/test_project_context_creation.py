from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app() -> FastAPI:
    from cairn.server.routers import projects

    app = FastAPI()
    app.include_router(projects.router)
    return app


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_create_project_auto_generates_project_context_file(temp_db, tmp_path, monkeypatch):
    context_root = tmp_path / "context"
    context_root.mkdir(parents=True)
    monkeypatch.setenv("CAIRN_PROJECT_CONTEXT_ROOT", str(context_root))

    client = TestClient(_app())
    response = client.post(
        "/projects",
        json={
            "title": "Context project",
            "origin": "公网可达但属于授权测试环境",
            "goal": "验证攻击路径并形成报告",
        },
    )

    assert response.status_code == 201
    project_id = response.json()["project"]["id"]
    context_path = context_root / "projects" / f"{project_id}.project-context.yaml"

    assert context_path.exists()
    payload = _load_yaml(context_path)
    assert payload["schema_version"] == 1
    assert payload["inherits"] == "../default.project-context.yaml"
    assert payload["project"]["project_id"] == project_id
    assert payload["project"]["target_summary"] == "公网可达但属于授权测试环境"
    assert payload["project"]["goal"] == "验证攻击路径并形成报告"
    assert payload["project"]["notes"] == ""
    assert "project_name" not in payload["project"]
    assert payload["override"] == {
        "authorization": {},
        "scope": {},
        "output": {},
    }


def test_create_project_does_not_overwrite_existing_project_context_file(temp_db, tmp_path, monkeypatch):
    context_root = tmp_path / "context"
    context_path = context_root / "projects" / "proj_001.project-context.yaml"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "schema_version": 1,
        "project": {
            "project_id": "proj_001",
            "target_summary": "手工维护内容",
            "goal": "保持原值",
        },
    }
    context_path.write_text(
        yaml.safe_dump(original, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAIRN_PROJECT_CONTEXT_ROOT", str(context_root))

    client = TestClient(_app())
    response = client.post(
        "/projects",
        json={
            "title": "Existing context project",
            "origin": "新 origin",
            "goal": "新 goal",
        },
    )

    assert response.status_code == 201
    assert _load_yaml(context_path) == original


def test_create_project_still_succeeds_when_context_file_generation_fails(temp_db, tmp_path, monkeypatch):
    from cairn import project_context_files

    broken_root = tmp_path / "broken-context-root"
    broken_root.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(project_context_files, "resolve_context_root", lambda: broken_root)

    client = TestClient(_app())
    response = client.post(
        "/projects",
        json={
            "title": "Best effort context project",
            "origin": "origin fact",
            "goal": "goal fact",
        },
    )

    assert response.status_code == 201
    assert response.json()["project"]["id"] == "proj_001"
