from __future__ import annotations

import base64
from pathlib import Path

from cairn.egress_proxy import ProxyHandler, ScopeAuthorizer, proxy_credentials


def _basic(project_id: str, token: str) -> str:
    username, password = proxy_credentials(project_id, token)
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def test_scope_authorizer_enforces_project_endpoints(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "proj_040.project-context.yaml").write_text(
        """project:
  project_id: proj_040
  target_summary: |-
    http://10.102.35.135/ncycx/
    https://10.102.35.134:8443/
""",
        encoding="utf-8",
    )
    authorizer = ScopeAuthorizer(tmp_path, "proxy-secret", {("vsllm.com", 443)})

    assert authorizer.allowed("proj_040", "10.102.35.135", 80)
    assert authorizer.allowed("proj_040", "10.102.35.134", 8443)
    assert not authorizer.allowed("proj_040", "10.102.35.135", 8000)
    assert not authorizer.allowed("proj_040", "127.0.0.1", 8000)
    assert not authorizer.allowed("proj_040", "host.docker.internal", 80)
    assert not authorizer.allowed("proj_040", "10.102.35.200", 80)
    assert authorizer.allowed("proj_040", "vsllm.com", 443)


def test_scope_authorizer_uses_project_scoped_credentials(tmp_path: Path):
    authorizer = ScopeAuthorizer(tmp_path, "proxy-secret", set())

    assert authorizer.authenticate(_basic("proj_040", "proxy-secret")) == "proj_040"
    assert authorizer.authenticate(_basic("proj_039", "proxy-secret")) == "proj_039"
    assert authorizer.authenticate(_basic("proj_040", "wrong-secret")) is None
    assert authorizer.authenticate(None) is None


def test_startup_scope_only_allows_model_providers(tmp_path: Path):
    authorizer = ScopeAuthorizer(tmp_path, "proxy-secret", {("vsllm.com", 443)})

    assert authorizer.allowed("__startup__", "vsllm.com", 443)
    assert not authorizer.allowed("__startup__", "10.102.35.135", 80)


def test_response_stream_ignores_client_disconnect():
    class _Response:
        def read(self, _size: int) -> bytes:
            return b"response chunk"

    class _DisconnectedWriter:
        def write(self, _chunk: bytes) -> None:
            raise BrokenPipeError

    handler = ProxyHandler.__new__(ProxyHandler)
    handler.wfile = _DisconnectedWriter()

    handler._stream_response(_Response())
