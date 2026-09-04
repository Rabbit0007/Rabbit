from __future__ import annotations

import requests

from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.startup_healthcheck import (
    StartupHealthcheckResult,
    format_failure_summary,
)


def test_client_request_failure_returns_status_zero() -> None:
    class Session:
        def request(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

    client = CairnClient("http://server/")
    client._local.session = Session()

    result = client.create_step("proj_001", ["f001"], "investigate", "decider")

    assert result.status_code == 0
    assert result.text == "offline"


def test_client_attaches_internal_service_token_to_each_thread_session() -> None:
    client = CairnClient("http://server/", internal_token="shared-token")

    session = client._session()

    assert session.headers["X-Cairn-Internal-Token"] == "shared-token"
    client.close()


def test_startup_healthcheck_failure_summary_includes_worker_details() -> None:
    results = [
        StartupHealthcheckResult(
            worker_name="worker-a",
            ok=False,
            status=401,
            duration_ms=12,
            detail="unauthorized",
            endpoint="POST http://api/v1/messages",
        ),
        StartupHealthcheckResult(
            worker_name="worker-b",
            ok=True,
            status=200,
            duration_ms=8,
            detail="",
            endpoint="POST http://api/v1/messages",
        ),
    ]

    summary = format_failure_summary(results)

    assert summary == (
        "startup healthchecks failed for all workers: worker-a(http=401, detail=unauthorized)"
    )
