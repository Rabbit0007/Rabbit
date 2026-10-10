from __future__ import annotations

from typing import Protocol, runtime_checkable

from cairn.dispatcher.runtime.process import ExecProcess


@runtime_checkable
class ExecutionBackend(Protocol):
    """The execution substrate for worker processes.

    Two implementations exist: ContainerManager (one Docker container per project) and
    LocalBackend (host subprocesses, one working directory per project). The scheduler,
    task runners and startup healthcheck only depend on this surface so the two backends
    are interchangeable behind a single ``runtime.execution`` switch.
    """

    def container_name(self, project_id: str) -> str: ...

    def ensure_running(self, project_id: str) -> str: ...

    def build_exec_process(
        self,
        container_name: str,
        env: dict[str, str],
        command: list[str],
        timeout_seconds: int | None = None,
        kill_after_seconds: int = 5,
    ) -> ExecProcess: ...

    def write_text_file(self, container_name: str, path: str, content: str) -> None: ...

    def needs_completed_cleanup(self, project_id: str) -> bool: ...

    def needs_stopped_cleanup(self, project_id: str) -> bool: ...

    def cleanup_completed(self, project_id: str) -> bool: ...

    def cleanup_stopped(self, project_id: str) -> bool: ...

    def close(self) -> None: ...
