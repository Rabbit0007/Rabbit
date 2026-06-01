"""Observation script: capture UNFIXED baseline outputs for preservation tests.

Run from cairn/:
  uv run --with pytest --with httpx python _tmp_obs_preservation.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.pop("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", None)
os.environ.pop("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", None)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db, auth_db, product_db
from cairn.server.routers import workers

# isolated DB
db._db_path = None
tmp = tempfile.mkdtemp()
db.configure(Path(tmp) / "obs.db")
auth_db.configure_auth_db()
product_db.configure_product_db()

app = FastAPI()
app.include_router(workers.router)
client = TestClient(app, base_url="https://testserver")

LONG_TASK = "explore-target " * 20


def snapshot():
    return {
        "workers": [
            {"name": "alpha", "type": "claude", "status": "busy", "running": 1, "unhealthy": False},
            {"name": "beta", "type": "gpt", "status": "idle", "running": 0, "unhealthy": False},
            {"name": "gamma", "type": "mock", "status": "idle", "running": 0, "unhealthy": True},
            {"name": "delta", "type": "pi", "enabled": False, "status": "disabled", "running": 0, "unhealthy": False},
        ],
        "running_tasks": [{"worker_name": "alpha", "current_task": LONG_TASK}],
        "task_history": [
            {"worker_name": "alpha", "duration_seconds": 10.0},
            {"worker_name": "alpha", "duration_seconds": 25.0},
            {"worker_name": "beta", "duration_seconds": None},
        ],
        "heartbeats": {"alpha": {"last_heartbeat_seconds_ago": 3.5}},
    }


def worker_item(name="mock-1"):
    return {
        "name": name, "type": "mock", "enabled": True, "task_types": ["bootstrap"],
        "max_running": 1, "priority": 0, "env": {}, "secret_env_keys": [],
    }


def test_result_ok(ok=True):
    return {
        "worker_name": "mock-1", "ok": ok, "returncode": 0 if ok else 1, "duration_ms": 12,
        "http_status": None, "response_preview": "pong", "stderr_preview": "",
        "preview": "pong", "command": "python3 -c ...",
    }


def config_body():
    return {"workers": [worker_item("mock-1")]}


import requests


class FakeResp:
    def __init__(self, payload, status=200, json_raises=False):
        self._p = payload
        self.status_code = status
        self._jr = json_raises

    def json(self):
        if self._jr:
            raise ValueError("no json")
        return self._p


captured = {"get": [], "request": []}


def make_get(*, exc=None, response=None, latency=0.01):
    def fake_get(url, timeout=None):
        captured["get"].append(timeout)
        if exc is not None:
            raise exc
        if latency is not None and (timeout is None or latency > timeout):
            raise requests.Timeout("slow")
        return response
    return fake_get


def make_request(*, exc=None, response_for=None, latency=0.01):
    def fake_request(method, url, json=None, timeout=None):
        captured["request"].append(timeout)
        if exc is not None:
            raise exc
        if latency is not None and (timeout is None or latency > timeout):
            raise requests.Timeout("slow")
        return response_for(url)
    return fake_request


def show(label, resp):
    print(f"=== {label}: status={resp.status_code} body={json.dumps(resp.json())}")


# ---- STATUS success ----
workers.requests.get = make_get(response=FakeResp(snapshot()))
show("STATUS success", client.get("/api/workers"))
print("   captured get timeout:", captured["get"][-1])

# ---- STATUS unreachable ----
workers.requests.get = make_get(exc=requests.ConnectionError("refused"))
show("STATUS unreachable", client.get("/api/workers"))

# ---- STATUS non-200 ----
workers.requests.get = make_get(response=FakeResp({"workers": []}, status=500))
show("STATUS non200", client.get("/api/workers"))

# ---- STATUS slow timeout (latency 5 > 2.0) ----
captured["get"].clear()
workers.requests.get = make_get(response=FakeResp(snapshot()), latency=5.0)
show("STATUS slow->timeout", client.get("/api/workers"))
print("   captured get timeout:", captured["get"][-1])

# ---- STATUS timeout fallback with env ----
for val in ["", "abc", "-1", "0", "5.0"]:
    if val == "__unset__":
        os.environ.pop("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", None)
    else:
        os.environ["CAIRN_DISPATCHER_INTERNAL_TIMEOUT"] = val
    captured["get"].clear()
    workers.requests.get = make_get(response=FakeResp(snapshot()))
    client.get("/api/workers")
    print(f"   env={val!r} -> captured status timeout={captured['get'][-1]}")
os.environ.pop("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", None)


def resp_for(url):
    if url.endswith(workers.TEST_PATH):
        return FakeResp(test_result_ok(True))
    return FakeResp(config_body())


# ---- TEST success ----
captured["request"].clear()
workers.requests.request = make_request(response_for=resp_for)
show("TEST success", client.post("/api/workers/config/test", json={"worker": worker_item()}))
print("   captured request timeout:", captured["request"][-1])

# ---- TEST ok:false within timeout ----
workers.requests.request = make_request(response_for=lambda u: FakeResp(test_result_ok(False)))
show("TEST ok:false", client.post("/api/workers/config/test", json={"worker": worker_item()}))

# ---- TEST non-2xx ----
workers.requests.request = make_request(response_for=lambda u: FakeResp({"detail": "boom"}, status=500))
show("TEST non2xx", client.post("/api/workers/config/test", json={"worker": worker_item()}))

# ---- TEST unreachable ----
workers.requests.request = make_request(exc=requests.ConnectionError("refused"))
show("TEST unreachable", client.post("/api/workers/config/test", json={"worker": worker_item()}))

# ---- CONFIG_GET success ----
workers.requests.request = make_request(response_for=resp_for)
show("CONFIG_GET success", client.get("/api/workers/config"))

# ---- CONFIG_GET non-2xx ----
workers.requests.request = make_request(response_for=lambda u: FakeResp({"detail": "boom"}, status=500))
show("CONFIG_GET non2xx", client.get("/api/workers/config"))

# ---- CONFIG_GET unreachable ----
workers.requests.request = make_request(exc=requests.ConnectionError("refused"))
show("CONFIG_GET unreachable", client.get("/api/workers/config"))

# ---- CONFIG_PUT success ----
workers.requests.request = make_request(response_for=resp_for)
show("CONFIG_PUT success", client.put("/api/workers/config", json={"workers": [worker_item()]}))

# ---- CONFIG_PUT unreachable ----
workers.requests.request = make_request(exc=requests.ConnectionError("refused"))
show("CONFIG_PUT unreachable", client.put("/api/workers/config", json={"workers": [worker_item()]}))
