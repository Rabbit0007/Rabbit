"""Pydantic models for project-level campaign synthesis.

This module shapes a read-only, project-scoped summary view that aggregates
existing facts, intents, hints, and extracted vulnerabilities into a concise
assessment. It does not introduce new persistence tables; the response is
derived on demand from the current project state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cairn.server.vulnerabilities_models import Severity


CampaignGoalStatus = Literal["achieved", "in_progress", "blocked"]
CampaignFindingSource = Literal["vulnerability", "fact", "hint"]
CampaignFindingConfidence = Literal["confirmed", "supported", "tentative"]


class CampaignCounts(BaseModel):
    facts: int = 0
    hints: int = 0
    intents: int = 0
    open_intents: int = 0
    vulnerabilities: int = 0
    high_value_vulnerabilities: int = 0


class CampaignFinding(BaseModel):
    source_type: CampaignFindingSource
    source_id: str
    title: str
    summary: str
    severity: Severity | None = None
    confidence: CampaignFindingConfidence = "supported"


class CampaignSynthesis(BaseModel):
    project_id: str
    project_name: str
    project_status: Literal["active", "stopped", "completed"]
    goal_status: CampaignGoalStatus
    origin: str
    goal: str
    lead: str
    summary: str
    counts: CampaignCounts
    top_findings: list[CampaignFinding] = Field(default_factory=list)
    open_intents: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
