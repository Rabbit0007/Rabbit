from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

LOG = logging.getLogger(__name__)

CONTEXT_ROOT_ENV_KEYS = (
    "CAIRN_PROJECT_CONTEXT_ROOT",
    "RABBIT_PROJECT_CONTEXT_ROOT",
)
DEFAULT_CONTEXT_FILENAME = "default.project-context.yaml"
PROJECTS_DIRNAME = "projects"


def resolve_context_root() -> Path | None:
    for key in CONTEXT_ROOT_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if raw:
            path = Path(raw)
            if path.is_dir():
                return path
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / ".rabbit" / "context"
        if candidate.is_dir():
            return candidate
    return None


def project_context_path(project_id: str, context_root: Path | None = None) -> Path | None:
    root = context_root or resolve_context_root()
    if root is None:
        return None
    return root / PROJECTS_DIRNAME / f"{project_id}.project-context.yaml"


def render_project_context_file(project_id: str, origin: str, goal: str) -> str:
    payload = {
        "schema_version": 1,
        "inherits": "../default.project-context.yaml",
        "project": {
            "project_id": project_id,
            "target_summary": origin,
            "goal": goal,
            "notes": "",
        },
        "override": {
            "authorization": {},
            "scope": {},
            "output": {},
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def ensure_project_context_file(project_id: str, origin: str, goal: str) -> Path | None:
    path = project_context_path(project_id)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path
        path.write_text(
            render_project_context_file(project_id=project_id, origin=origin, goal=goal),
            encoding="utf-8",
        )
        return path
    except OSError:
        LOG.exception("Failed to create project context file for %s at %s", project_id, path)
        return None
