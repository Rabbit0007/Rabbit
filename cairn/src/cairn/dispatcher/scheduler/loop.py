from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests

from cairn.dispatcher.config import DispatchConfig, LocalConfig, WorkerConfig
from cairn.dispatcher.models import DecideCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.local_backend import LocalBackend
from cairn.dispatcher.runtime.startup_healthcheck import format_failure_summary, run_startup_healthchecks
from cairn.dispatcher.scheduler.worker_select import choose_worker
from cairn.dispatcher.workers.registry import get_driver
from cairn.dispatcher.tasks.decide import run_decide_task
from cairn.dispatcher.tasks.execute import run_execute_task
from cairn.server.models import ProjectDetail, ProjectSummary, Step

LOG = logging.getLogger(__name__)
UNHEALTHY_RETRY_AFTER_SECONDS = 5
REJECTED_RETRY_AFTER_SECONDS = 5
FAILED_RETRY_BASE_SECONDS = 5
FAILED_RETRY_MAX_SECONDS = 300


@dataclass(slots=True)
class WorkerSelection:
    worker: WorkerConfig | None
    blocked_busy: list[str]
    blocked_unhealthy: list[str]
    blocked_rejected: list[str]
    blocked_failed: list[str]
    blocked_task_type: list[str]


class DispatcherLoop:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = DispatchConfig.load(config_path)
        self.client = CairnClient(self.config.server)
        if self.config.runtime.execution == "local":
            self.container_manager = LocalBackend(self.config.local or LocalConfig())
        else:
            assert self.config.container is not None
            self.container_manager = ContainerManager(self.config.container)
        self.executor = ThreadPoolExecutor(max_workers=self.config.runtime.max_workers)
        self.cleanup_executor = ThreadPoolExecutor(max_workers=max(1, min(8, self.config.runtime.max_workers)))
        self.futures: dict[Future[str], RunningTask] = {}
        self.cleanup_futures: dict[Future[bool], tuple[str, str | None, str | None]] = {}
        self.decide_checkpoints: dict[str, DecideCheckpoint] = {}
        self.runtime_project_ids: set[str] = set()
        self.worker_unhealthy_until: dict[str, float] = {}
        self.worker_rejected_until: dict[tuple[str, str, str], float] = {}
        self.worker_failed_until: dict[tuple[str, str, str], float] = {}
        self.worker_failure_counts: dict[tuple[str, str, str], int] = {}
        self.task_history: deque[dict[str, object]] | None = None
        self._log_state: dict[str, tuple[int, str, tuple[object, ...]]] = {}
        self._cleanup_pending: set[str] = set()
        self._inactive_cleanup_done: dict[str, str] = {}
        self.project_cursor = 0
        self._settings_checked = False
        self._startup_healthchecks_checked = False

    def enable_internal_state_tracking(self, history_size: int = 200) -> None:
        """Enable the optional bounded history consumed by the internal API."""
        self.task_history = deque(maxlen=max(1, history_size))

    def _record_task_history(self, task: RunningTask, outcome: str) -> None:
        # A few embedders and focused tests construct the loop through
        # ``__new__`` and only initialise the scheduler state they exercise.
        # Internal-state tracking is optional, so a missing attribute must be
        # treated the same as tracking being disabled.
        if getattr(self, "task_history", None) is None:
            return
        completed = time.time()
        normalized = {
            "continue": "success",
            "cancelled": "released",
            "unhealthy": "failed",
        }.get(outcome, outcome)
        if normalized not in {"success", "failed", "rejected", "released"}:
            normalized = "failed"
        self.task_history.append(
            {
                "worker_name": task.worker_name,
                "project_id": task.project_id,
                "task_type": task.task_type,
                "step_id": task.step_id,
                "intent_id": task.step_id,  # response compatibility only
                "started_at": task.started_at,
                "completed_at": completed,
                "duration_seconds": round(max(0.0, completed - task.started_at), 3),
                "outcome": normalized,
            }
        )

    def close(self) -> None:
        if self.futures:
            LOG.info(
                "调度器关闭中 等待任务=%s 运行中项目=%s",
                len(self.futures),
                sorted({task.project_id for task in self.futures.values()}),
            )
        self.executor.shutdown(wait=True)
        self.cleanup_executor.shutdown(wait=True)
        self.container_manager.close()
        self.client.close()

    def run(self, once: bool = False) -> None:
        try:
            self.run_startup_healthchecks()
            while True:
                try:
                    if not self._settings_checked:
                        self._validate_server_settings()
                        self._settings_checked = True
                    self._reap_futures()
                    self._reap_cleanup_futures()
                    summaries = self.client.list_projects()
                    self._initialize_decide_checkpoints(summaries)
                    self._refresh_runtime_projects(summaries)
                    self._cancel_inactive_tasks(summaries)
                    self._queue_container_cleanups(summaries)
                    self._dispatch_available(summaries)
                except requests.RequestException as exc:
                    if once:
                        raise
                    LOG.warning(
                        "调度器请求失败 错误=%s 重试=%ss",
                        exc, self.config.runtime.interval,
                    )
                    time.sleep(self.config.runtime.interval)
                    continue
                if once:
                    break
                time.sleep(self.config.runtime.interval)
        finally:
            self.close()

    def run_startup_healthchecks_only(self) -> None:
        try:
            self.run_startup_healthchecks(show_commands=True, force=True)
        finally:
            self.close()

    def run_startup_healthchecks(self, *, show_commands: bool = False, force: bool = False) -> None:
        if self._startup_healthchecks_checked:
            return
        if self.config.runtime.execution == "local":
            self._run_local_binary_check()
            self._startup_healthchecks_checked = True
            return
        if not force and self.config.runtime.worker_healthcheck == "disabled":
            LOG.info("跳过启动健康检查 runtime.worker_healthcheck=disabled")
            self._startup_healthchecks_checked = True
            return
        self._run_startup_healthchecks(show_commands=show_commands)
        self._startup_healthchecks_checked = True

    def _run_local_binary_check(self) -> None:
        binaries: dict[str, list[str]] = {}
        for worker in self.config.workers:
            binary = get_driver(worker.type, "local").local_binary(worker)
            if binary is None:
                continue
            binaries.setdefault(binary, []).append(worker.name)
        if not binaries:
            return
        LOG.info("[*] 本地执行: 检查 %d 个工人 CLI", len(binaries))
        available: list[str] = []
        missing: list[str] = []
        for binary in sorted(binaries):
            workers = ", ".join(sorted(binaries[binary]))
            path, runnable = self._probe_local_cli(binary)
            if path is None:
                missing.append(binary)
                LOG.error("[-] %-8s 未找到 (workers: %s)", binary, workers)
            elif runnable:
                available.append(binary)
                LOG.info("[+] %-8s %s (workers: %s)", binary, path, workers)
            else:
                available.append(binary)
                LOG.warning("[!] %-8s %s 已找到但 `%s --help` 失败 (workers: %s)", binary, path, binary, workers)
        if not available:
            raise RuntimeError(
                "local execution: none of the configured worker CLIs are installed on PATH ("
                + ", ".join(sorted(binaries))
                + "). Install them and make sure each runs directly from your shell, then retry."
            )
        if missing:
            LOG.warning("[!] 缺失 CLI，对应工人无法运行: %s", ", ".join(sorted(missing)))
        LOG.warning(
            "[!] 本地模式直接运行主机 CLI: 确保 %s 可非交互运行，且已通过 CLI 配置或 worker env 配好模型凭据",
            ", ".join(sorted(available)),
        )

    @staticmethod
    def _probe_local_cli(binary: str) -> tuple[str | None, bool]:
        path = shutil.which(binary)
        if path is None:
            return None, False
        try:
            result = subprocess.run(
                [binary, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return path, False
        return path, result.returncode == 0

    # ── Dispatch main loop ─────────────────────────────────────────────

    def _dispatch_available(self, summaries: list[ProjectSummary]) -> None:
        if len(self.futures) >= self.config.runtime.max_workers:
            self._log_changed("dispatch/global", logging.INFO,
                              "跳过调度 max_workers 已满 运行任务=%s", len(self.futures))
            return
        active = [summary for summary in summaries if summary.status == "active"]
        if not active:
            self._log_changed("dispatch/global", logging.INFO, "跳过调度 没有活跃项目")
            return

        running_projects = self._ordered_projects(
            [summary for summary in active if summary.id in self.runtime_project_ids]
        )
        idle_projects = self._ordered_projects(
            [summary for summary in active if summary.id not in self.runtime_project_ids]
        )

        dispatched = True
        while dispatched and len(self.futures) < self.config.runtime.max_workers:
            dispatched = False
            for summary in running_projects:
                if self._try_dispatch_project(summary):
                    dispatched = True
                    if len(self.futures) >= self.config.runtime.max_workers:
                        return
            if dispatched:
                continue
            if self._running_project_count(active) >= self.config.runtime.max_running_projects:
                return
            for summary in idle_projects:
                if self._running_project_count(active) >= self.config.runtime.max_running_projects:
                    return
                if self._try_dispatch_project(summary):
                    dispatched = True
                    break

    def _ordered_projects(self, summaries: list[ProjectSummary]) -> list[ProjectSummary]:
        if not summaries:
            return []
        ids = [summary.id for summary in summaries]
        ids.sort()
        offset = self.project_cursor % len(ids)
        ordered_ids = ids[offset:] + ids[:offset]
        by_id = {summary.id: summary for summary in summaries}
        self.project_cursor += 1
        return [by_id[project_id] for project_id in ordered_ids]

    def _try_dispatch_project(self, summary: ProjectSummary) -> bool:
        skip_scope = f"project:{summary.id}:skip"
        container_name = self.container_manager.container_name(summary.id)
        if container_name in self._cleanup_pending:
            return False
        if self._project_running_task_count(summary.id) >= self.config.runtime.max_project_workers:
            return False

        project = self.client.get_project(summary.id)
        if project.project.status != "active":
            return False

        # Initial project → Decide
        if self._is_initial_project(project):
            if self._project_has_running_decide(project.project.id):
                return False
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_decide(project, export_yaml, "initial")

        # Graph changed → Decide
        if project.project.decide is None:
            decide_trigger = self._decide_trigger(project)
            if decide_trigger is not None:
                export_yaml = self.client.export_project(summary.id)
                return self._dispatch_decide(project, export_yaml, decide_trigger)

        # A Decide lease is a graph-mutation barrier: do not start new Execute
        # claims from the snapshot while Decide is still evaluating it.
        if project.project.decide is not None:
            return False

        # Unclaimed steps → Execute
        running_step_ids = self._project_running_execute_steps(summary.id)
        unclaimed_steps = [
            s for s in project.steps
            if s.to is None and s.worker is None
            and s.concluded_at is None
            and s.id not in running_step_ids
            and not s.abandoned
        ]
        if unclaimed_steps:
            # Sort by priority (desc) then created_at (asc)
            unclaimed_steps.sort(key=lambda s: (-s.priority, s.created_at))
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_execute(project, export_yaml, unclaimed_steps[0])

        return False

    # ── Initial project detection ──────────────────────────────────────

    def _is_initial_project(self, project: ProjectDetail) -> bool:
        fact_ids = {fact.id for fact in project.facts}
        if fact_ids != {"origin"} or len(project.facts) != 1:
            return False
        if not project.goals:
            return False
        if not project.steps:
            return True
        return False

    # ── Decide dispatch ────────────────────────────────────────────────

    def _dispatch_decide(self, project: ProjectDetail, export_yaml: str, trigger: str) -> bool:
        selection = self._select_worker(project.project.id, "decide")
        worker = selection.worker
        if worker is None:
            self._log_changed(
                f"project:{project.project.id}:worker:decide", logging.INFO,
                "no worker available for decide project=%s", project.project.id,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:decide")
        claim = self.client.claim_decide(project.project.id, worker.name, trigger)
        if claim.status_code in (403, 409):
            level = logging.INFO if claim.status_code == 403 else logging.WARNING
            LOG.log(level, "决策认领失败 项目=%s 工人=%s 状态=%s",
                    project.project.id, worker.name, claim.status_code)
            return False
        if not claim.ok:
            return False
        try:
            future = self.executor.submit(
                run_decide_task, self.config, self.client, self.container_manager,
                project, export_yaml, worker, cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("提交决策任务失败 项目=%s", project.project.id)
            self._best_effort_release_decide(project.project.id, worker.name)
            return False
        open_step_count = self._project_open_step_count(project)
        self.futures[future] = RunningTask(
            project.project.id, "decide", worker.name, cancellation,
            fact_count=len(project.facts), hint_count=len(project.hints),
            goal_count=len(project.goals), open_step_count=open_step_count,
            open_intent_count=open_step_count,
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("派发决策 项目=%s 工人=%s 触发=%s", project.project.id, worker.name, trigger)
        return True

    def _project_has_running_decide(self, project_id: str) -> bool:
        return any(
            task.project_id == project_id and task.task_type == "decide"
            for task in self.futures.values()
        )

    def _decide_trigger(self, project: ProjectDetail) -> str | None:
        open_step_count = self._project_open_step_count(project)
        checkpoint = self.decide_checkpoints.get(project.project.id)
        if checkpoint is None:
            return "initial"
        changes: list[str] = []
        if len(project.facts) > checkpoint.fact_count:
            changes.append(f"facts:{checkpoint.fact_count}->{len(project.facts)}")
        if len(project.hints) > checkpoint.hint_count:
            changes.append(f"hints:{checkpoint.hint_count}->{len(project.hints)}")
        if len(project.goals) > checkpoint.goal_count:
            changes.append(f"goals:{checkpoint.goal_count}->{len(project.goals)}")
        if checkpoint.open_step_count > 0 and open_step_count == 0:
            changes.append(f"open_steps:{checkpoint.open_step_count}->0")
        signature = self._graph_signature(project)
        if checkpoint.graph_signature is not None and signature != checkpoint.graph_signature:
            changes.append("graph_state")
        if not changes:
            return None
        return ",".join(changes)

    @staticmethod
    def _graph_signature(project: ProjectDetail) -> tuple[object, ...]:
        """Graph changes excluding ephemeral Worker claims and heartbeats."""
        goals = tuple(sorted(
            (goal.id, goal.description, goal.parent_goal_id, goal.status, goal.priority)
            for goal in project.goals
        ))
        steps = tuple(sorted(
            (step.id, tuple(step.from_), step.to, step.description, step.goal_id, step.priority, step.abandoned)
            for step in project.steps
        ))
        facts = tuple(sorted((fact.id, fact.description) for fact in project.facts))
        hints = tuple(sorted((hint.id, hint.content) for hint in project.hints))
        findings = tuple(sorted(
            (finding.id, finding.title, finding.description, finding.severity, finding.fact_id)
            for finding in project.findings
        ))
        return facts, goals, steps, hints, findings

    # ── Execute dispatch ───────────────────────────────────────────────

    def _dispatch_execute(self, project: ProjectDetail, export_yaml: str, step: Step) -> bool:
        selection = self._select_worker(project.project.id, "execute")
        worker = selection.worker
        if worker is None:
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:execute")
        claim = self.client.step_heartbeat(project.project.id, step.id, worker.name)
        if claim.status_code in (403, 409):
            return False
        if not claim.ok:
            return False
        try:
            future = self.executor.submit(
                run_execute_task, self.config, self.client, self.container_manager,
                project, export_yaml, step, worker, cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("提交执行任务失败 项目=%s 步骤=%s", project.project.id, step.id)
            self._best_effort_release(project.project.id, step.id, worker.name)
            return False
        self.futures[future] = RunningTask(
            project.project.id, "execute", worker.name, cancellation,
            step_id=step.id, intent_id=step.id,
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("派发执行 项目=%s 步骤=%s 工人=%s", project.project.id, step.id, worker.name)
        return True

    def _project_running_execute_steps(self, project_id: str) -> set[str]:
        return {
            task.step_id
            for task in self.futures.values()
            if task.project_id == project_id and task.task_type == "execute" and task.step_id is not None
        }

    # ── Worker selection ───────────────────────────────────────────────

    def _select_worker(self, project_id: str, task_type: str) -> WorkerSelection:
        now = time.time()
        candidates: list[WorkerConfig] = []
        blocked_busy: list[str] = []
        blocked_unhealthy: list[str] = []
        blocked_rejected: list[str] = []
        blocked_failed: list[str] = []
        blocked_task_type: list[str] = []
        running_counts = self._worker_counts()
        for worker in self.config.workers:
            if not worker.enabled:
                continue
            if task_type not in worker.task_types:
                blocked_task_type.append(worker.name)
                continue
            running = running_counts.get(worker.name, 0)
            if running >= worker.max_running:
                blocked_busy.append(f"{worker.name}({running}/{worker.max_running})")
                continue
            unhealthy_until = self.worker_unhealthy_until.get(worker.name, 0)
            if unhealthy_until > now:
                blocked_unhealthy.append(f"{worker.name}({unhealthy_until - now:.1f}s)")
                continue
            rejected_until = self.worker_rejected_until.get((project_id, task_type, worker.name), 0)
            if rejected_until > now:
                blocked_rejected.append(f"{worker.name}({rejected_until - now:.1f}s)")
                continue
            failed_until = self.worker_failed_until.get((project_id, task_type, worker.name), 0)
            if failed_until > now:
                blocked_failed.append(f"{worker.name}({failed_until - now:.1f}s)")
                continue
            candidates.append(worker)
        if not candidates:
            return WorkerSelection(worker=None, blocked_busy=blocked_busy, blocked_unhealthy=blocked_unhealthy,
                                   blocked_rejected=blocked_rejected, blocked_failed=blocked_failed,
                                   blocked_task_type=blocked_task_type)
        ordered = choose_worker(candidates, running_counts)
        return WorkerSelection(
            worker=ordered[0] if ordered else None,
            blocked_busy=blocked_busy, blocked_unhealthy=blocked_unhealthy,
            blocked_rejected=blocked_rejected, blocked_failed=blocked_failed,
            blocked_task_type=blocked_task_type,
        )

    def _worker_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.futures.values():
            counts[task.worker_name] = counts.get(task.worker_name, 0) + 1
        return counts

    # ── Project state helpers ──────────────────────────────────────────

    def _project_running_task_count(self, project_id: str) -> int:
        return sum(1 for task in self.futures.values() if task.project_id == project_id)

    def _running_project_count(self, summaries: list[ProjectSummary]) -> int:
        active_ids = {summary.id for summary in summaries if summary.status == "active"}
        return len(self.runtime_project_ids & active_ids)

    def _project_open_step_count(self, project: ProjectDetail) -> int:
        return sum(1 for s in project.steps if s.to is None and s.concluded_at is None and not s.abandoned)

    # ── Future reaping ─────────────────────────────────────────────────

    def _reap_futures(self) -> None:
        done = [future for future in self.futures if future.done()]
        for future in done:
            task = self.futures.pop(future)
            try:
                outcome = future.result()
                self._record_task_history(task, outcome)
                if outcome == "cancelled":
                    LOG.info("任务取消 项目=%s 任务=%s 工人=%s", task.project_id, task.task_type, task.worker_name)
                elif outcome not in ("success", "continue"):
                    LOG.warning("任务完成 项目=%s 任务=%s 工人=%s 结果=%s", task.project_id, task.task_type, task.worker_name, outcome)
                self._clear_project_log_state(task.project_id)
                if outcome == "unhealthy":
                    self.worker_unhealthy_until[task.worker_name] = time.time() + UNHEALTHY_RETRY_AFTER_SECONDS
                else:
                    self.worker_unhealthy_until.pop(task.worker_name, None)
                rejection_key = (task.project_id, task.task_type, task.worker_name)
                if outcome == "rejected":
                    self.worker_rejected_until[rejection_key] = time.time() + REJECTED_RETRY_AFTER_SECONDS
                else:
                    self.worker_rejected_until.pop(rejection_key, None)
                failure_key = (task.project_id, task.task_type, task.worker_name)
                if outcome == "failed":
                    failures = self.worker_failure_counts.get(failure_key, 0) + 1
                    delay = min(FAILED_RETRY_MAX_SECONDS, FAILED_RETRY_BASE_SECONDS * (2 ** (failures - 1)))
                    self.worker_failure_counts[failure_key] = failures
                    self.worker_failed_until[failure_key] = time.time() + delay
                    LOG.warning(
                        "任务失败退避 项目=%s 任务=%s 工人=%s 连续失败=%s 重试=%ss",
                        task.project_id, task.task_type, task.worker_name, failures, delay,
                    )
                else:
                    self.worker_failure_counts.pop(failure_key, None)
                    self.worker_failed_until.pop(failure_key, None)
                if task.task_type == "decide" and outcome == "continue":
                    self.decide_checkpoints.pop(task.project_id, None)
                elif task.task_type == "decide" and outcome == "success":
                    current = self.client.get_project(task.project_id)
                    if current.project.status == "active":
                        self.decide_checkpoints[task.project_id] = DecideCheckpoint(
                            fact_count=len(current.facts), hint_count=len(current.hints),
                            goal_count=len(current.goals), open_step_count=self._project_open_step_count(current),
                            graph_signature=self._graph_signature(current),
                        )
            except Exception:
                self._record_task_history(task, "failed")
                LOG.exception("任务崩溃 项目=%s 任务=%s 工人=%s", task.project_id, task.task_type, task.worker_name)
                failure_key = (task.project_id, task.task_type, task.worker_name)
                failures = self.worker_failure_counts.get(failure_key, 0) + 1
                delay = min(FAILED_RETRY_MAX_SECONDS, FAILED_RETRY_BASE_SECONDS * (2 ** (failures - 1)))
                self.worker_failure_counts[failure_key] = failures
                self.worker_failed_until[failure_key] = time.time() + delay

    # ── Container cleanup ──────────────────────────────────────────────

    def _cleanup_completed_containers(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "completed":
                continue
            if self._inactive_cleanup_done.get(summary.id) == summary.status:
                continue
            container_name = self.container_manager.container_name(summary.id)
            if container_name in self._cleanup_pending:
                continue
            if not self.container_manager.needs_completed_cleanup(summary.id):
                self._inactive_cleanup_done[summary.id] = summary.status
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_completed, summary.id)
            self.cleanup_futures[future] = (container_name, summary.id, summary.status)
            self._cleanup_pending.add(container_name)

    def _cleanup_stopped_containers(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "stopped":
                continue
            if self._inactive_cleanup_done.get(summary.id) == summary.status:
                continue
            container_name = self.container_manager.container_name(summary.id)
            if container_name in self._cleanup_pending:
                continue
            if not self.container_manager.needs_stopped_cleanup(summary.id):
                self._inactive_cleanup_done[summary.id] = summary.status
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_stopped, summary.id)
            self.cleanup_futures[future] = (container_name, summary.id, summary.status)
            self._cleanup_pending.add(container_name)

    def _queue_container_cleanups(self, summaries: list[ProjectSummary]) -> None:
        self._cleanup_completed_containers(summaries)
        self._cleanup_stopped_containers(summaries)

    def _reap_cleanup_futures(self) -> None:
        done = [future for future in self.cleanup_futures if future.done()]
        for future in done:
            name, project_id, target_status = self.cleanup_futures.pop(future)
            self._cleanup_pending.discard(name)
            try:
                success = future.result()
                if success and project_id is not None and target_status in ("completed", "stopped"):
                    self._inactive_cleanup_done[project_id] = target_status
                elif project_id is not None:
                    self._inactive_cleanup_done.pop(project_id, None)
            except Exception:
                if project_id is not None:
                    self._inactive_cleanup_done.pop(project_id, None)
                LOG.exception("容器清理失败 container=%s", name)

    def _refresh_runtime_projects(self, summaries: list[ProjectSummary]) -> None:
        active_ids = {summary.id for summary in summaries if summary.status == "active"}
        self.runtime_project_ids.intersection_update(active_ids)
        inactive_status_by_id = {summary.id: summary.status for summary in summaries if summary.status != "active"}
        for project_id, status in list(self._inactive_cleanup_done.items()):
            current_status = inactive_status_by_id.get(project_id)
            if current_status != status:
                self._inactive_cleanup_done.pop(project_id, None)

    def _cancel_inactive_tasks(self, summaries: list[ProjectSummary]) -> None:
        status_by_project = {summary.id: summary.status for summary in summaries}
        for task in self.futures.values():
            status = status_by_project.get(task.project_id, "deleted")
            if status != "active" and task.cancellation.cancel(status):
                LOG.info("取消非活跃项目任务 项目=%s 任务=%s 工人=%s 状态=%s",
                         task.project_id, task.task_type, task.worker_name, status)

    def _initialize_decide_checkpoints(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "active":
                continue
            if summary.id in self.decide_checkpoints:
                continue
            open_step_count = summary.working_step_count + summary.unclaimed_step_count
            if open_step_count == 0:
                continue
            self.decide_checkpoints[summary.id] = DecideCheckpoint(
                fact_count=summary.fact_count, hint_count=summary.hint_count,
                goal_count=summary.goal_count, open_step_count=open_step_count,
            )

    # ── Release helpers ────────────────────────────────────────────────

    def _best_effort_release(self, project_id: str, step_id: str, worker_name: str) -> None:
        response = self.client.release_step(project_id, step_id, worker_name)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("释放失败 项目=%s 步骤=%s 工人=%s 状态=%s", project_id, step_id, worker_name, response.status_code)

    def _best_effort_release_decide(self, project_id: str, worker_name: str) -> None:
        response = self.client.release_decide(project_id, worker_name)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("决策释放失败 项目=%s 工人=%s 状态=%s", project_id, worker_name, response.status_code)

    # ── Log state helpers ──────────────────────────────────────────────

    def _log_changed(self, scope: str, level: int, message: str, *args: object) -> None:
        state = (level, message, args)
        if self._log_state.get(scope) == state:
            return
        self._log_state[scope] = state
        LOG.log(level, message, *args)

    def _clear_log_state(self, scope: str) -> None:
        self._log_state.pop(scope, None)

    def _clear_project_log_state(self, project_id: str) -> None:
        prefix = f"project:{project_id}:"
        for scope in list(self._log_state):
            if scope.startswith(prefix):
                self._log_state.pop(scope, None)

    def _validate_server_settings(self) -> None:
        settings = self.client.get_settings()
        interval = self.config.runtime.interval
        for name, value in (("step_timeout", settings.step_timeout), ("decide_timeout", settings.decide_timeout)):
            if value <= interval:
                raise RuntimeError(f"server {name}={value}s must be greater than dispatcher interval={interval}s")
            if value < interval * 2:
                LOG.warning("服务器 %s 偏紧 %s=%s秒 interval=%s秒 心跳余量仅 %s秒", name, name, value, interval, value - interval)
                continue
            LOG.info("服务器设置验证通过 %s=%s秒 interval=%s秒", name, value, interval)

    def _run_startup_healthchecks(self, *, show_commands: bool) -> None:
        results = run_startup_healthchecks(self.config, show_commands=show_commands)
        if any(result.ok for result in results):
            return
        now = time.time()
        for result in results:
            if not result.ok:
                self.worker_unhealthy_until[result.worker_name] = now + UNHEALTHY_RETRY_AFTER_SECONDS
        message = format_failure_summary(results)
        if getattr(self, "_internal_api_started", False):
            LOG.warning("%s; dispatcher remains online for config repair", message)
            return
        raise RuntimeError(message)
