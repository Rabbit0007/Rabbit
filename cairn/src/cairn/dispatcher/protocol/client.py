from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import os
import threading

from pydantic import TypeAdapter
import requests
from requests.adapters import HTTPAdapter

from cairn.server.models import ProjectDetail, ProjectSummary, Settings

LOG = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    def __init__(self, message: str, status_code: int, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


@dataclass(slots=True)
class ApiResult:
    status_code: int
    data: Any | None = None
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class CairnClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        internal_token: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._internal_token = (
            internal_token
            if internal_token is not None
            else os.environ.get("CAIRN_INTERNAL_TOKEN")
        )
        self._summary_adapter = TypeAdapter(list[ProjectSummary])
        self._local = threading.local()
        self._sessions: dict[int, requests.Session] = {}
        self._sessions_lock = threading.Lock()

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    # ── Project CRUD ───────────────────────────────────────────────────

    def list_projects(self) -> list[ProjectSummary]:
        response = self._session().get(self._url("/projects"), timeout=self._timeout)
        response.raise_for_status()
        return self._summary_adapter.validate_python(response.json())

    def get_project(self, project_id: str) -> ProjectDetail:
        response = self._session().get(self._url(f"/projects/{project_id}"), timeout=self._timeout)
        response.raise_for_status()
        return ProjectDetail.model_validate(response.json())

    def get_settings(self) -> Settings:
        response = self._session().get(self._url("/settings"), timeout=self._timeout)
        response.raise_for_status()
        return Settings.model_validate(response.json())

    def export_project(self, project_id: str) -> str:
        response = self._session().get(
            self._url(f"/projects/{project_id}/export"),
            params={"format": "yaml"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    # ── Decide (was Reason) ────────────────────────────────────────────

    def claim_decide(self, project_id: str, worker: str, trigger: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/decide/claim",
                                  json={"worker": worker, "trigger": trigger})

    def decide_heartbeat(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/decide/heartbeat",
                                  json={"worker": worker})

    def release_decide(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/decide/release",
                                  json={"worker": worker})

    # Backward compat
    claim_reason = claim_decide
    reason_heartbeat = decide_heartbeat
    release_reason = release_decide

    # ── Steps ──────────────────────────────────────────────────────────

    def create_step(self, project_id: str, from_ids: list[str], description: str,
                    creator: str, goal_id: str | None = None, priority: int = 0) -> ApiResult:
        body: dict = {"from": from_ids, "description": description, "creator": creator, "worker": None}
        if goal_id is not None:
            body["goal_id"] = goal_id
        if priority:
            body["priority"] = priority
        return self._request_json("POST", f"/projects/{project_id}/steps", json=body)

    def update_step(self, project_id: str, step_id: str, priority: int | None = None,
                    goal_id: str | None = None, abandoned: bool | None = None) -> ApiResult:
        body: dict = {}
        if priority is not None:
            body["priority"] = priority
        if goal_id is not None:
            body["goal_id"] = goal_id
        if abandoned is not None:
            body["abandoned"] = abandoned
        return self._request_json("PATCH", f"/projects/{project_id}/steps/{step_id}", json=body)

    def step_heartbeat(self, project_id: str, step_id: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/steps/{step_id}/heartbeat",
                                  json={"worker": worker})

    def release_step(self, project_id: str, step_id: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/steps/{step_id}/release",
                                  json={"worker": worker})

    def conclude_step(self, project_id: str, step_id: str, worker: str, description: str,
                      finding: dict | None = None) -> ApiResult:
        body: dict = {"worker": worker, "description": description}
        if finding is not None:
            body["finding"] = finding
        return self._request_json("POST", f"/projects/{project_id}/steps/{step_id}/conclude", json=body)

    # Backward compat
    heartbeat = step_heartbeat
    release = release_step
    conclude = conclude_step
    create_intent = create_step

    # ── Goals ──────────────────────────────────────────────────────────

    def create_goal(self, project_id: str, description: str, creator: str,
                    parent_goal_id: str | None = None, priority: int = 0) -> ApiResult:
        body: dict = {"description": description, "creator": creator}
        if parent_goal_id is not None:
            body["parent_goal_id"] = parent_goal_id
        if priority:
            body["priority"] = priority
        return self._request_json("POST", f"/projects/{project_id}/goals", json=body)

    def update_goal(self, project_id: str, goal_id: str,
                    priority: int | None = None) -> ApiResult:
        body: dict = {}
        if priority is not None:
            body["priority"] = priority
        return self._request_json("PATCH", f"/projects/{project_id}/goals/{goal_id}", json=body)

    def delete_goal(self, project_id: str, goal_id: str) -> ApiResult:
        return self._request_json("DELETE", f"/projects/{project_id}/goals/{goal_id}", json={})

    def complete_goal(self, project_id: str, goal_id: str, from_ids: list[str],
                      description: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/goals/{goal_id}/complete",
                                  json={"from": from_ids, "description": description, "worker": worker})

    # ── Legacy complete (backward compat) ──────────────────────────────

    def complete(self, project_id: str, from_ids: list[str], description: str, worker: str) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/complete",
                                  json={"from": from_ids, "description": description, "worker": worker})

    # ── HTTP helpers ───────────────────────────────────────────────────

    def _request_json(self, method: str, path: str, json: dict[str, Any]) -> ApiResult:
        try:
            response = self._session().request(
                method, self._url(path), json=json, timeout=self._timeout,
            )
        except requests.RequestException as exc:
            LOG.warning("请求失败 method=%s path=%s 错误=%s", method, path, exc)
            return ApiResult(status_code=0, text=str(exc))
        data: Any | None = None
        if response.headers.get("content-type", "").startswith("application/json"):
            data = response.json()
        return ApiResult(status_code=response.status_code, data=data, text=response.text)

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session

        session = requests.Session()
        if self._internal_token:
            session.headers["X-Cairn-Internal-Token"] = self._internal_token
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64, pool_block=False)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        self._local.session = session
        with self._sessions_lock:
            self._sessions[threading.get_ident()] = session
        return session
