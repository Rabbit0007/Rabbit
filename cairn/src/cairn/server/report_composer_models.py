"""Structured models for read-only vulnerability report composition.

These models shape a delivery-grade report draft derived from existing
vulnerability, fact, intent, and proof data. They do not introduce new
persistence tables and are generated on demand.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cairn.server.vulnerabilities_models import Severity, VulnerabilityStatus


ComposerSource = Literal["template", "model"]


class VulnerabilityProofPoint(BaseModel):
    label: str
    content: str


class VulnerabilityNarrativeReport(BaseModel):
    vulnerability_id: str
    project_id: str
    project_name: str
    title: str
    severity: Severity
    status: VulnerabilityStatus
    vulnerability_type: str
    generated_at: str
    composer_source: ComposerSource = "template"
    composer_model: str | None = None
    executive_summary: str
    attack_surface: list[str] = Field(default_factory=list)
    proof_points: list[VulnerabilityProofPoint] = Field(default_factory=list)
    vulnerability_proof: str
    impact: str
    root_cause: str
    evidence_highlights: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)
    operator_notes: list[str] = Field(default_factory=list)
