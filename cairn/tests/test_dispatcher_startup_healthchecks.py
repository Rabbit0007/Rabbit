from __future__ import annotations

from types import SimpleNamespace

import pytest

from cairn.dispatcher.scheduler import loop as loop_module
from cairn.dispatcher.scheduler.loop import DispatcherLoop


def _dispatcher_with_internal_api(started: bool) -> DispatcherLoop:
    dispatcher = DispatcherLoop.__new__(DispatcherLoop)
    dispatcher.config = object()
    dispatcher.container_manager = object()
    dispatcher._internal_api_started = started
    dispatcher.worker_unhealthy_until = {}
    return dispatcher


def _failed_result():
    return SimpleNamespace(
        ok=False,
        worker_name="worker-1",
        http_status=None,
        returncode=124,
        response_preview="timed out",
        stderr_preview="",
    )


def test_startup_healthchecks_still_fail_without_internal_api(monkeypatch):
    dispatcher = _dispatcher_with_internal_api(started=False)
    monkeypatch.setattr(
        loop_module,
        "run_startup_healthchecks",
        lambda *_args, **_kwargs: [_failed_result()],
    )

    with pytest.raises(RuntimeError, match="startup healthchecks failed for all workers"):
        dispatcher._run_startup_healthchecks(show_commands=False)

    assert "worker-1" in dispatcher.worker_unhealthy_until


def test_startup_healthchecks_do_not_exit_when_internal_api_is_online(monkeypatch, caplog):
    dispatcher = _dispatcher_with_internal_api(started=True)
    monkeypatch.setattr(
        loop_module,
        "run_startup_healthchecks",
        lambda *_args, **_kwargs: [_failed_result()],
    )

    dispatcher._run_startup_healthchecks(show_commands=False)

    assert "dispatcher remains online" in caplog.text
    assert "worker-1" in dispatcher.worker_unhealthy_until
