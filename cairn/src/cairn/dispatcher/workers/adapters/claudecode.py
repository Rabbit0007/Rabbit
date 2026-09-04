from __future__ import annotations

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.base import DriverResult, SeedSessionDriver
from cairn.dispatcher.workers.health import HealthResult, http_ping, proxies_from_env


ANTHROPIC_VERSION = "2023-06-01"


class ClaudeCodeDriver(SeedSessionDriver):
    type_name = "claudecode"

    def local_binary(self, worker: WorkerConfig | None = None) -> str | None:
        return "claude"

    def check_health(self, worker: WorkerConfig, *, timeout: float) -> HealthResult:
        env = worker.env
        return http_ping(
            f"{env['ANTHROPIC_BASE_URL']}/v1/messages",
            headers={
                "Authorization": f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json_body={
                "model": env["ANTHROPIC_MODEL"],
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=timeout,
            proxies=proxies_from_env(env),
        )

    def describe_health(self, worker: WorkerConfig) -> str:
        return f"POST {worker.env['ANTHROPIC_BASE_URL']}/v1/messages (model={worker.env['ANTHROPIC_MODEL']})"

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        assert session is not None
        return DriverResult(
            argv=[
                "claude",
                "--session-id",
                session,
                "--dangerously-skip-permissions",
                "-p",
                "--",
                prompt,
            ],
            session=session,
        )

    def build_decide(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        assert session is not None
        return DriverResult(
            argv=[
                "claude",
                "--session-id",
                session,
                "--dangerously-skip-permissions",
                "--tools",
                "Read",
                "-p",
                "--",
                prompt,
            ],
            session=session,
        )

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        return [
            "claude",
            "-r",
            session,
            "--dangerously-skip-permissions",
            "-p",
            "--",
            prompt,
        ]
