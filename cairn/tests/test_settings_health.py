from __future__ import annotations

from cairn.server.routers import settings


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_dispatcher_health_snapshot_uses_internal_token(monkeypatch):
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TOKEN", "dispatcher-secret")
    requests_seen: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs):
        requests_seen.append((url, kwargs))
        if url.endswith("/internal/health"):
            return _Response(200, {"status": "ok"})
        return _Response(200, {"workers": []})

    monkeypatch.setattr(settings.requests, "get", fake_get)

    snapshot, error = settings._fetch_dispatcher_snapshot()

    assert error is None
    assert snapshot == {"workers": []}
    status_kwargs = next(kwargs for url, kwargs in requests_seen if url.endswith("/internal/status"))
    assert status_kwargs["headers"] == {"X-Cairn-Dispatcher-Token": "dispatcher-secret"}
