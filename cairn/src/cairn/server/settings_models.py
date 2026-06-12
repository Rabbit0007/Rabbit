from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SettingsHealthCheck(BaseModel):
    key: str
    label: str
    status: Literal["ok", "warning", "error"]
    summary: str
    detail: str | None = None


class SettingsAlert(BaseModel):
    level: Literal["info", "warning", "danger"]
    title: str
    detail: str | None = None


class SettingsHealthSummary(BaseModel):
    status: Literal["ok", "warning", "error"]
    server_reachable: bool = True
    dispatcher_reachable: bool = False
    active_projects: int = 0
    online_workers: int = 0
    offline_workers: int = 0


class SettingsHealthStats(BaseModel):
    users: int = 0
    projects: int = 0
    active_projects: int = 0
    vulnerabilities: int = 0
    export_records: int = 0
    audit_entries: int = 0
    notifications_unread: int = 0


class SettingsHealthResponse(BaseModel):
    generated_at: str
    summary: SettingsHealthSummary
    stats: SettingsHealthStats
    checks: list[SettingsHealthCheck] = Field(default_factory=list)
    alerts: list[SettingsAlert] = Field(default_factory=list)


class SettingsCleanupResult(BaseModel):
    ran_at: str
    deleted: dict[str, int] = Field(default_factory=dict)
    summary: str

