from __future__ import annotations

import errno
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from cairn.dispatcher.config import DispatchConfig
from cairn.dispatcher.internal_api import SECRET_MASK, _write_dispatch_config, create_internal_app


def _config(workers: list[dict]) -> DispatchConfig:
    return DispatchConfig.model_validate(
        {
            "server": "http://server",
            "runtime": {
                "max_workers": 4,
                "max_running_projects": 2,
                "max_project_workers": 2,
                "interval": 1,
                "healthcheck_timeout": 1,
                "prompt_group": "mock",
            },
            "tasks": {
                "bootstrap": {"timeout": 1, "conclude_timeout": 1},
                "reason": {"timeout": 1, "max_intents": 3},
                "explore": {"timeout": 1, "conclude_timeout": 1},
            },
            "container": {
                "image": "cairn-agent:latest",
                "network_mode": "bridge",
                "completed_action": "remove",
            },
            "workers": workers,
        }
    )


class _Loop:
    def __init__(self, config_path, config: DispatchConfig):
        self.config_path = config_path
        self.config = config
        self.worker_unhealthy_until = {"removed": 123.0}
        self.worker_rejected_until = {("proj", "explore", "removed"): 123.0}
        self._config_lock = threading.RLock()
        self.container_manager = object()

    def apply_config(self, config: DispatchConfig) -> None:
        with self._config_lock:
            worker_names = {worker.name for worker in config.workers}
            self.config = config
            for name in list(self.worker_unhealthy_until):
                if name not in worker_names:
                    self.worker_unhealthy_until.pop(name, None)
            for key in list(self.worker_rejected_until):
                if key[2] not in worker_names:
                    self.worker_rejected_until.pop(key, None)


def _client(tmp_path, config: DispatchConfig) -> tuple[TestClient, _Loop]:
    path = tmp_path / "dispatch.yaml"
    path.write_text("workers: []\n", encoding="utf-8")
    loop = _Loop(path, config)
    return TestClient(create_internal_app(loop)), loop


def test_internal_worker_config_masks_secret_env_values(tmp_path):
    client, _loop = _client(
        tmp_path,
        _config(
            [
                {
                    "name": "claude-1",
                    "type": "claudecode",
                    "enabled": True,
                    "task_types": ["bootstrap", "reason", "explore"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {
                        "ANTHROPIC_MODEL": "model-a",
                        "ANTHROPIC_BASE_URL": "https://example.test",
                        "ANTHROPIC_AUTH_TOKEN": "secret-token",
                    },
                }
            ]
        ),
    )

    body = client.get("/internal/workers/config").json()
    worker = body["workers"][0]
    assert worker["env"]["ANTHROPIC_AUTH_TOKEN"] == SECRET_MASK
    assert "ANTHROPIC_AUTH_TOKEN" in worker["secret_env_keys"]
    assert "secret-token" not in str(body)


def test_internal_worker_config_preserves_masked_secret_on_update(tmp_path):
    client, loop = _client(
        tmp_path,
        _config(
            [
                {
                    "name": "claude-1",
                    "type": "claudecode",
                    "enabled": True,
                    "task_types": ["bootstrap", "reason", "explore"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {
                        "ANTHROPIC_MODEL": "model-a",
                        "ANTHROPIC_BASE_URL": "https://example.test",
                        "ANTHROPIC_AUTH_TOKEN": "secret-token",
                    },
                }
            ]
        ),
    )

    response = client.put(
        "/internal/workers/config",
        json={
            "workers": [
                {
                    "name": "claude-1",
                    "type": "claudecode",
                    "enabled": True,
                    "task_types": ["bootstrap", "reason"],
                    "max_running": 2,
                    "priority": 1,
                    "env": {
                        "ANTHROPIC_MODEL": "model-b",
                        "ANTHROPIC_BASE_URL": "https://example.test",
                        "ANTHROPIC_AUTH_TOKEN": SECRET_MASK,
                    },
                }
            ]
        },
    )

    assert response.status_code == 200
    worker = loop.config.workers[0]
    assert worker.max_running == 2
    assert worker.env["ANTHROPIC_MODEL"] == "model-b"
    assert worker.env["ANTHROPIC_AUTH_TOKEN"] == "secret-token"
    assert "removed" not in loop.worker_unhealthy_until
    assert loop.worker_rejected_until == {}


def test_internal_worker_config_validation_failure_does_not_apply(tmp_path):
    client, loop = _client(
        tmp_path,
        _config(
            [
                {
                    "name": "mock-1",
                    "type": "mock",
                    "enabled": True,
                    "task_types": ["bootstrap"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {},
                }
            ]
        ),
    )

    response = client.put(
        "/internal/workers/config",
        json={
            "workers": [
                {
                    "name": "bad-codex",
                    "type": "codex",
                    "enabled": True,
                    "task_types": ["explore"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {
                        "CODEX_MODEL": "gpt-test",
                        "CODEX_BASE_URL": "https://example.test/v1",
                    },
                }
            ]
        },
    )

    assert response.status_code == 422
    assert [worker.name for worker in loop.config.workers] == ["mock-1"]


def test_internal_worker_config_rejects_all_disabled_workers(tmp_path):
    client, loop = _client(
        tmp_path,
        _config(
            [
                {
                    "name": "mock-1",
                    "type": "mock",
                    "enabled": True,
                    "task_types": ["bootstrap"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {},
                }
            ]
        ),
    )

    response = client.put(
        "/internal/workers/config",
        json={
            "workers": [
                {
                    "name": "mock-1",
                    "type": "mock",
                    "enabled": False,
                    "task_types": ["bootstrap"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {},
                }
            ]
        },
    )

    assert response.status_code == 422
    assert loop.config.workers[0].enabled is True


def test_write_dispatch_config_falls_back_when_bind_mount_replace_is_busy(
    tmp_path, monkeypatch
):
    config = _config(
        [
            {
                "name": "mock-1",
                "type": "mock",
                "enabled": True,
                "task_types": ["bootstrap"],
                "max_running": 1,
                "priority": 0,
                "env": {},
            }
        ]
    )
    path = tmp_path / "dispatch.yaml"
    path.write_text("workers: []\n", encoding="utf-8")
    loop = _Loop(path, config)

    original_replace = Path.replace

    def busy_replace(self, target):
        if self.name == ".dispatch.yaml.tmp":
            raise OSError(errno.EBUSY, "Device or resource busy")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", busy_replace)

    _write_dispatch_config(loop, config)

    text = path.read_text(encoding="utf-8")
    assert "mock-1" in text
    assert not (tmp_path / ".dispatch.yaml.tmp").exists()
