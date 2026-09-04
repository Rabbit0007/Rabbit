from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.workers.registry import get_driver

LOG = logging.getLogger("runtime.startup")
DETAIL_PREVIEW_LIMIT = 60


@dataclass(slots=True)
class StartupHealthcheckResult:
    worker_name: str
    ok: bool
    status: int | None
    duration_ms: int
    detail: str
    endpoint: str


def run_startup_healthchecks(
    config: DispatchConfig,
    *,
    show_commands: bool = False,
) -> list[StartupHealthcheckResult]:
    workers = [worker for worker in config.workers if worker.enabled]
    parallelism = max(1, min(len(workers), config.runtime.max_workers, 8))
    LOG.info("[*] Startup healthcheck: workers=%s parallelism=%s", len(workers), parallelism)
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        future_map = {executor.submit(_check_worker, config, worker): worker.name for worker in workers}
        results: list[StartupHealthcheckResult] = []
        for future in as_completed(future_map):
            worker_name = future_map[future]
            try:
                results.append(future.result())
            except Exception:
                LOG.exception("startup healthcheck crashed worker=%s", worker_name)
                results.append(
                    StartupHealthcheckResult(worker_name, False, None, 0, "startup healthcheck crashed", "-")
                )

    results.sort(key=lambda result: result.worker_name)
    _log_report(results, show_commands=show_commands)
    return results


def format_failure_summary(results: list[StartupHealthcheckResult]) -> str:
    failed = [result for result in results if not result.ok]
    if not failed:
        return "startup healthchecks failed for all workers"
    details = []
    for result in failed:
        status = getattr(result, "status", None) or getattr(result, "http_status", None) or "-"
        detail = (
            getattr(result, "detail", None)
            or getattr(result, "response_preview", None)
            or getattr(result, "stderr_preview", None)
            or "-"
        )
        details.append(f"{result.worker_name}(http={status}, detail={detail})")
    return f"startup healthchecks failed for all workers: {', '.join(details)}"


def _check_worker(config: DispatchConfig, worker: WorkerConfig) -> StartupHealthcheckResult:
    driver = get_driver(worker.type, config.runtime.execution)
    started = time.perf_counter()
    result = driver.check_health(worker, timeout=config.runtime.healthcheck_timeout)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return StartupHealthcheckResult(
        worker_name=worker.name,
        ok=result.ok,
        status=result.status,
        duration_ms=duration_ms,
        detail=_preview(result.detail),
        endpoint=driver.describe_health(worker),
    )


def run_single_startup_healthcheck(
    config: DispatchConfig,
    _container_manager,
    worker: WorkerConfig,
) -> StartupHealthcheckResult:
    """Run the same in-process check used at startup for one draft Worker."""
    return _check_worker(config, worker)


def _log_report(results: list[StartupHealthcheckResult], *, show_commands: bool) -> None:
    if not results:
        LOG.warning("[!] Startup healthcheck: no workers configured")
        return
    worker_width = max(len("WORKER"), *(len(result.worker_name) for result in results))
    lines = ["[=] Startup healthcheck results"]
    lines.append(f"{'CHK':<5} {'WORKER':<{worker_width}} {'HTTP':<6} {'TIME_S':>8}  DETAIL")
    lines.append(f"{'-' * 5} {'-' * worker_width} {'-' * 6} {'-' * 8}  {'-' * 60}")
    healthy_count = 0
    for result in results:
        if result.ok:
            healthy_count += 1
        marker = "[+]" if result.ok else "[-]"
        duration_seconds = f"{result.duration_ms / 1000:.2f}"
        lines.append(
            f"{marker:<5} "
            f"{result.worker_name:<{worker_width}} "
            f"{(str(result.status) if result.status is not None else '-'):<6} "
            f"{duration_seconds:>8}  "
            f"{result.detail or '-'}"
        )
    lines.append(
        f"[=] Summary: total={len(results)} healthy={healthy_count} unhealthy={len(results) - healthy_count}"
    )
    if show_commands:
        lines.append("")
        lines.append("[=] Startup healthcheck endpoints")
        for result in results:
            lines.append(f"- {result.worker_name}: {result.endpoint}")
        lines.append("")
    LOG.info("\n%s\n", "\n".join(lines))


def _preview(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= DETAIL_PREVIEW_LIMIT:
        return compact
    return compact[:DETAIL_PREVIEW_LIMIT] + "..."
