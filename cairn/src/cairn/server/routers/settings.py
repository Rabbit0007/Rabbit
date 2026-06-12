from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter

from cairn.server.activity_service import record_audit, record_notification
from cairn.server.db import get_conn
from cairn.server.models import Settings
from cairn.server.settings_models import (
    SettingsAlert,
    SettingsCleanupResult,
    SettingsHealthCheck,
    SettingsHealthResponse,
    SettingsHealthStats,
    SettingsHealthSummary,
)
from cairn.server.settings_service import load_settings
from cairn.server.routers.workers import _internal_url, _status_timeout

router = APIRouter(tags=["settings"])

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.strftime(_TIMESTAMP_FORMAT)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _status_rank(value: str) -> int:
    return {"ok": 0, "warning": 1, "error": 2}.get(value, 2)


def _summary_status(checks: list[SettingsHealthCheck], alerts: list[SettingsAlert]) -> str:
    status = "ok"
    for check in checks:
        if _status_rank(check.status) > _status_rank(status):
            status = check.status
    for alert in alerts:
        level_status = "error" if alert.level == "danger" else "warning" if alert.level == "warning" else "ok"
        if _status_rank(level_status) > _status_rank(status):
            status = level_status
    return status


def _fetch_dispatcher_snapshot() -> tuple[dict | None, str | None]:
    try:
        health = requests.get(_internal_url("/internal/health"), timeout=_status_timeout())
        if health.status_code != 200:
            return None, f"调度器健康检查返回 {health.status_code}"
        response = requests.get(_internal_url("/internal/status"), timeout=_status_timeout())
        if response.status_code != 200:
            return None, f"调度器状态接口返回 {response.status_code}"
        payload = response.json()
        if not isinstance(payload, dict):
            return None, "调度器状态响应格式异常"
        return payload, None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)


def _worker_counts(snapshot: dict | None) -> tuple[int, int, int]:
    if not snapshot:
        return (0, 0, 0)
    online = 0
    offline = 0
    total = 0
    for item in snapshot.get("workers", []):
        if not isinstance(item, dict):
            continue
        total += 1
        status = str(item.get("status") or "")
        enabled = bool(item.get("enabled", True))
        if not enabled or status == "disabled":
            continue
        if status in {"idle", "busy"}:
            online += 1
        elif status in {"offline", "unhealthy"}:
            offline += 1
    return (total, online, offline)


def _idle_project_alerts(conn, threshold_hours: int) -> list[SettingsAlert]:
    rows = conn.execute(
        """
        SELECT
            p.id,
            p.title,
            p.created_at,
            p.reason_last_heartbeat_at,
            MAX(i.created_at) AS last_intent_created_at,
            MAX(i.concluded_at) AS last_intent_concluded_at,
            MAX(h.created_at) AS last_hint_created_at
        FROM projects p
        LEFT JOIN intents i ON i.project_id = p.id
        LEFT JOIN hints h ON h.project_id = p.id
        WHERE p.status = 'active'
        GROUP BY p.id, p.title, p.created_at, p.reason_last_heartbeat_at
        ORDER BY p.created_at
        """
    ).fetchall()
    alerts: list[SettingsAlert] = []
    now = _utcnow()
    for row in rows:
        latest = max(
            (
                ts
                for ts in (
                    row["created_at"],
                    row["reason_last_heartbeat_at"],
                    row["last_intent_created_at"],
                    row["last_intent_concluded_at"],
                    row["last_hint_created_at"],
                )
                if ts
            ),
            default=None,
        )
        latest_at = _parse_utc(latest)
        if latest_at is None:
            continue
        idle_hours = (now - latest_at).total_seconds() / 3600
        if idle_hours >= threshold_hours:
            alerts.append(
                SettingsAlert(
                    level="warning",
                    title=f"项目 {row['title']} 已长时间无进展",
                    detail=f"{row['id']} 最近活动距今约 {idle_hours:.1f} 小时，超过阈值 {threshold_hours} 小时。",
                )
            )
    return alerts


@router.get("/settings", response_model=Settings)
def get_settings() -> Settings:
    with get_conn() as conn:
        return load_settings(conn)


@router.put("/settings", response_model=Settings)
def update_settings(body: Settings) -> Settings:
    payload = body.model_dump()
    assignments = ", ".join(f"{key} = ?" for key in payload)
    values = tuple(payload[key] for key in payload)
    with get_conn() as conn:
        conn.execute(f"UPDATE settings SET {assignments} WHERE rowid = 1", values)
    record_audit("settings.update", "更新系统设置", detail=", ".join(f"{key}={value}" for key, value in payload.items()))
    record_notification("系统设置已更新", level="success", body="新的调度、安全与保留策略已保存。")
    return body


@router.get("/settings/health", response_model=SettingsHealthResponse)
def health() -> SettingsHealthResponse:
    checks: list[SettingsHealthCheck] = []
    alerts: list[SettingsAlert] = []

    with get_conn() as conn:
        settings = load_settings(conn)
        stats = SettingsHealthStats(
            users=int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]),
            projects=int(conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]),
            active_projects=int(conn.execute("SELECT COUNT(*) AS n FROM projects WHERE status = 'active'").fetchone()["n"]),
            vulnerabilities=int(conn.execute("SELECT COUNT(*) AS n FROM vulnerabilities").fetchone()["n"]),
            export_records=int(conn.execute("SELECT COUNT(*) AS n FROM export_records").fetchone()["n"]),
            audit_entries=int(conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]),
            notifications_unread=int(conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE read = 0").fetchone()["n"]),
        )
        alerts.extend(_idle_project_alerts(conn, settings.project_idle_alert_hours))

    checks.append(
        SettingsHealthCheck(
            key="server",
            label="API 服务",
            status="ok",
            summary="产品服务运行正常",
            detail="系统设置、项目和报告接口均由当前服务提供。",
        )
    )
    checks.append(
        SettingsHealthCheck(
            key="database",
            label="SQLite 数据库",
            status="ok",
            summary=f"项目 {stats.projects} / 漏洞 {stats.vulnerabilities}",
            detail=f"用户 {stats.users}，审计日志 {stats.audit_entries}，导出记录 {stats.export_records}。",
        )
    )

    dispatcher_snapshot, dispatcher_error = _fetch_dispatcher_snapshot()
    worker_total, worker_online, worker_offline = _worker_counts(dispatcher_snapshot)
    if dispatcher_snapshot is None:
        checks.append(
            SettingsHealthCheck(
                key="dispatcher",
                label="调度器联通性",
                status="error" if stats.active_projects > 0 else "warning",
                summary="调度器状态暂不可用",
                detail=dispatcher_error or "无法连接到调度器内部状态接口。",
            )
        )
        alerts.append(
            SettingsAlert(
                level="danger" if stats.active_projects > 0 else "warning",
                title="无法读取调度器状态",
                detail=dispatcher_error or "工作节点测试、实时状态和健康联动暂不可用。",
            )
        )
    else:
        runtime = dispatcher_snapshot.get("runtime") if isinstance(dispatcher_snapshot, dict) else {}
        checks.append(
            SettingsHealthCheck(
                key="dispatcher",
                label="调度器联通性",
                status="ok",
                summary=f"轮询间隔 {runtime.get('interval', '-')}s，运行任务 {runtime.get('running_task_count', 0)}",
                detail=f"最大并发 {runtime.get('max_workers', 0)}，运行项目 {runtime.get('running_project_count', 0)}。",
            )
        )
        worker_status = "ok" if worker_offline == 0 else "warning"
        checks.append(
            SettingsHealthCheck(
                key="workers",
                label="工作节点状态",
                status=worker_status,
                summary=f"在线 {worker_online} / 离线 {worker_offline} / 总数 {worker_total}",
                detail="在线节点可继续接收任务；离线节点需要在工作节点页进一步排查。",
            )
        )
        if stats.active_projects > 0 and worker_online == 0:
            alerts.append(
                SettingsAlert(
                    level="danger",
                    title="存在运行需求但没有在线 Worker",
                    detail=f"当前有 {stats.active_projects} 个活动项目，但在线 Worker 为 0。",
                )
            )
        elif worker_offline > 0:
            alerts.append(
                SettingsAlert(
                    level="warning",
                    title="部分 Worker 处于离线状态",
                    detail=f"当前在线 {worker_online} 个，离线 {worker_offline} 个。",
                )
            )

    checks.append(
        SettingsHealthCheck(
            key="auth",
            label="认证策略",
            status="ok",
            summary=f"{settings.max_failed_login_attempts} 次失败 / {settings.rate_limit_window_minutes} 分钟窗口",
            detail=f"Session {settings.session_duration_hours} 小时，验证码始终开启。",
        )
    )
    checks.append(
        SettingsHealthCheck(
            key="retention",
            label="保留与清理策略",
            status="ok",
            summary=f"日志 {settings.log_retention_days} 天，导出 {settings.export_retention_days} 天",
            detail=f"通知保留 {settings.notification_retention_days} 天，项目无进展告警阈值 {settings.project_idle_alert_hours} 小时。",
        )
    )

    summary = SettingsHealthSummary(
        status=_summary_status(checks, alerts),
        server_reachable=True,
        dispatcher_reachable=dispatcher_snapshot is not None,
        active_projects=stats.active_projects,
        online_workers=worker_online,
        offline_workers=worker_offline,
    )
    return SettingsHealthResponse(
        generated_at=_utcnow().isoformat(),
        summary=summary,
        stats=stats,
        checks=checks,
        alerts=alerts,
    )


@router.post("/settings/cleanup", response_model=SettingsCleanupResult)
def cleanup() -> SettingsCleanupResult:
    now = _utcnow()
    with get_conn() as conn:
        settings = load_settings(conn)
        log_cutoff = _format_utc(now - timedelta(days=settings.log_retention_days))
        export_cutoff = _format_utc(now - timedelta(days=settings.export_retention_days))
        notification_cutoff = _format_utc(now - timedelta(days=settings.notification_retention_days))

        deleted = {
            "audit_log": conn.execute(
                "DELETE FROM audit_log WHERE created_at < ?",
                (log_cutoff,),
            ).rowcount,
            "worker_task_history": conn.execute(
                "DELETE FROM worker_task_history WHERE COALESCE(completed_at, started_at) < ?",
                (log_cutoff,),
            ).rowcount,
            "login_attempts": conn.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?",
                (log_cutoff,),
            ).rowcount,
            "export_records": conn.execute(
                "DELETE FROM export_records WHERE created_at < ?",
                (export_cutoff,),
            ).rowcount,
            "notifications": conn.execute(
                "DELETE FROM notifications WHERE read = 1 AND created_at < ?",
                (notification_cutoff,),
            ).rowcount,
            "expired_sessions": conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?",
                (_format_utc(now),),
            ).rowcount,
        }

    total_deleted = sum(int(value or 0) for value in deleted.values())
    summary = f"本次共清理 {total_deleted} 条历史数据。"
    record_audit("settings.cleanup", summary, detail=", ".join(f"{key}={value}" for key, value in deleted.items()))
    record_notification("系统清理已完成", level="info", body=summary)
    return SettingsCleanupResult(
        ran_at=now.isoformat(),
        deleted={key: int(value or 0) for key, value in deleted.items()},
        summary=summary,
    )

