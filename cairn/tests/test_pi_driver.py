from __future__ import annotations

import json

import pytest

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.runtime.startup_healthcheck import _run_worker_healthcheck
from cairn.dispatcher.tasks.common import HealthcheckRun
from cairn.dispatcher.workers.adapters.claudecode import ClaudeCodeDriver
from cairn.dispatcher.workers.adapters.pi import PiDriver


def _worker(provider_api: str = "openai-completions") -> WorkerConfig:
    return WorkerConfig.model_validate(
        {
            "name": "pi-local",
            "type": "pi",
            "enabled": True,
            "task_types": ["bootstrap", "reason", "explore"],
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

    with pytest.raises(ValueError, match="No API provider registered"):
        PiDriver().extract_response_text(stdout, "")


def test_pi_driver_healthcheck_rejects_agent_error_with_zero_exit_code():
    stdout = _jsonl(
        {"type": "session", "id": "session-1"},
        _agent_end("", stop_reason="error", error_message="provider unavailable"),
    )

    assert PiDriver().healthcheck_error(0, stdout, "") == "provider unavailable"


def test_non_pi_driver_healthcheck_keeps_exit_code_semantics():
    driver = ClaudeCodeDriver()

    assert driver.healthcheck_error(0, "any output", "") is None
    assert driver.healthcheck_error(1, "", "request failed") == "request failed"


@pytest.mark.parametrize("response", ["pong", "pong.", "PONG!"])
def test_pi_driver_healthcheck_accepts_pong(response):
    stdout = _jsonl(_agent_end(response))

    assert PiDriver().healthcheck_error(0, stdout, "") is None


def test_startup_healthcheck_marks_pi_agent_error_unhealthy(monkeypatch):
    stdout = _jsonl(
        _agent_end("", stop_reason="error", error_message="No API provider registered for api: openai")
    )

    def fake_run_healthcheck(*args, **kwargs):  # noqa: ARG001
        return HealthcheckRun(
            result=ProcessResult(returncode=0, stdout=stdout, stderr=""),
            duration_ms=12,
        )

    monkeypatch.setattr(
        "cairn.dispatcher.runtime.startup_healthcheck.run_healthcheck",
        fake_run_healthcheck,
    )

    result = _run_worker_healthcheck(object(), "container", _worker(), 10)

    assert result.ok is False
    assert "No API provider registered" in result.response_preview
