"""Pydantic models for the read-only project campaign view.

The view is derived from Cairn-Y's native Fact/Goal/Step/Finding state.  The
``intents`` fields are temporary response aliases for older Rabbit clients;
new clients use ``steps`` and ``open_steps``.
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
    goals: int = 0
    steps: int = 0
    open_steps: int = 0
    # Backward-compatible response fields.  They mirror the Step counts and
    # are not backed by a second Intent state model.
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
    open_steps: list[str] = Field(default_factory=list)
    # Backward-compatible alias for older frontend builds.
    open_intents: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
