from __future__ import annotations

import json

import pytest

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.adapters.pi import PiDriver


def _worker(provider_api: str = "openai-completions") -> WorkerConfig:
    return WorkerConfig.model_validate(
        {
            "name": "pi-local",
            "type": "pi",
            "enabled": True,
            "task_types": ["decide", "execute"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "PI_MODEL": "deepseekv4",
                "PI_BASE_URL": "http://model.test/v1",
                "PI_API_KEY": "secret",
                "PI_PROVIDER_API": provider_api,
            },
        }
    )


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _agent_end(text: str = "pong.", *, stop_reason: str = "stop", error_message: str | None = None) -> dict:
    assistant = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stopReason": stop_reason,
    }
    if error_message is not None:
        assistant["errorMessage"] = error_message
    return {"type": "agent_end", "messages": [assistant]}


@pytest.mark.parametrize("alias", ["openai", "openai-chat-completions"])
def test_pi_provider_api_aliases_normalize_to_openai_completions(alias):
    worker = _worker(alias)
    assert worker.env["PI_PROVIDER_API"] == "openai-completions"


def test_pi_driver_extracts_assistant_text_from_agent_end():
    stdout = _jsonl(
        {"type": "session", "id": "session-1"},
        _agent_end('{"accepted": true, "data": {"description": "ok"}}'),
    )
    assert PiDriver().extract_response_text(stdout, "") == '{"accepted": true, "data": {"description": "ok"}}'


def test_pi_driver_surfaces_agent_error_instead_of_returning_jsonl():
    stdout = _jsonl(
        {"type": "session", "id": "session-1"},
        _agent_end("", stop_reason="error", error_message="No API provider registered for api: openai"),
    )
    with pytest.raises(RuntimeError, match="No API provider registered"):
        PiDriver().extract_response_text(stdout, "")


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_pi_in_process_healthcheck_reports_provider_failure(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(401, "provider unavailable")

    monkeypatch.setattr("cairn.dispatcher.workers.health.requests.post", fake_post)
    result = PiDriver().check_health(_worker(), timeout=10)

    assert result.ok is False
    assert result.status == 401
    assert result.detail == "provider unavailable"
    assert captured["url"] == "http://model.test/v1/chat/completions"
    assert captured["timeout"] == 10


def test_pi_in_process_healthcheck_accepts_success(monkeypatch):
    monkeypatch.setattr(
        "cairn.dispatcher.workers.health.requests.post",
        lambda *args, **kwargs: _Response(200),
    )
    result = PiDriver().check_health(_worker(), timeout=10)
    assert result.ok is True
    assert result.status == 200
