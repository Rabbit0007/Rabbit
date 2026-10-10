from __future__ import annotations

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db


def _insert_project(project_id: str, title: str = "Project One") -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            (project_id, title, "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES ('origin', ?, 'origin fact')",
            (project_id,),
        )
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES ('goal', ?, 'goal fact')",
            (project_id,),
        )


def _app() -> FastAPI:
    from cairn.server.routers import export, hints, projects

    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(hints.router)
    app.include_router(export.router)
    return app


def test_create_project_normalizes_inline_hint_content(temp_db):
    client = TestClient(_app())

    response = client.post(
        "/projects",
        json={
            "title": "Hint project",
            "origin": "origin fact",
            "goal": "goal fact",
            "hints": [
                {
                    "content": 'x;python3${IFS}-c${IFS}"..."${IFS}&amp;${IFS}#',
                    "creator": " admin ",
                }
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["hints"][0]["content"] == 'x;python3${IFS}-c${IFS}"..."${IFS}&${IFS}#'
    assert body["hints"][0]["creator"] == "admin"

    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT content, creator FROM hints WHERE project_id = ?",
            (body["project"]["id"],),
        ).fetchone()

    assert stored["content"] == 'x;python3${IFS}-c${IFS}"..."${IFS}&${IFS}#'
    assert stored["creator"] == "admin"


def test_existing_html_escaped_hint_is_normalized_on_project_and_export_reads(temp_db):
    _insert_project("p1")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO hints (id, project_id, content, creator, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "h1",
                "p1",
                "/mTM-align/ -&gt; randomString x &amp; y &lt; z",
                "worker",
                "2024-01-01T00:00:00Z",
            ),
        )

    client = TestClient(_app())

    project_response = client.get("/projects/p1")
    assert project_response.status_code == 200
    assert project_response.json()["hints"][0]["content"] == "/mTM-align/ -> randomString x & y < z"

    export_response = client.get("/projects/p1/export", params={"format": "yaml"})
    assert export_response.status_code == 200
    exported = yaml.safe_load(export_response.text)
    assert exported["hints"][0]["content"] == "/mTM-align/ -> randomString x & y < z"
