from __future__ import annotations

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.runtime import containers
from cairn.dispatcher.runtime.containers import ContainerManager


class _FakeDockerClient:
    def __init__(self, names: list[str]):
        self.containers = self
        self._containers = [_FakeContainer(name) for name in names]

    def list(self, all: bool = False):
        return self._containers

    def close(self) -> None:
        return None


class _FakeContainer:
    def __init__(self, name: str):
        self.name = name


def test_container_manager_uses_configured_prefixes(monkeypatch):
    monkeypatch.setattr(
        containers.docker,
        "from_env",
        lambda: _FakeDockerClient(
            [
                "rabbit-audit-dispatch-proj_1",
                "rabbit-pentest-dispatch-proj_2",
                "cairn-dispatch-proj_3",
            ]
        ),
    )
    manager = ContainerManager(
        ContainerConfig(
            image="worker:latest",
            network_mode="rabbit-pentest-net",
            completed_action="stop",
            name_prefix="rabbit-pentest-dispatch-",
            startup_name_prefix="rabbit-pentest-startup-healthcheck-",
        )
    )

    assert manager.container_name("proj/2") == "rabbit-pentest-dispatch-proj-2"
    assert manager.managed_container_names() == ["rabbit-pentest-dispatch-proj_2"]
