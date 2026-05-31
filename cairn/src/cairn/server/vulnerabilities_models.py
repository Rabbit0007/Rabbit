"""Pydantic models for the vulnerability report engine.

This module is intentionally a standalone module (``vulnerabilities_models.py``)
rather than a ``models/vulnerabilities.py`` package member. The existing
``cairn.server.models`` is a single module (``models.py``) that is imported across
the dispatcher and server (``from cairn.server.models import ...``). Introducing a
``models/`` package would shadow that module and break those imports, so these
models live in their own additive module instead -- mirroring the convention
established by ``auth_models.py``.

The field shapes follow design.md (New Pydantic Models section) and map onto the
``vulnerabilities`` table created in ``product_db.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The allowed severity levels, matching the ``CHECK`` constraint on the
# ``vulnerabilities.severity`` column in ``product_db.py``.
Severity = Literal["critical", "high", "medium", "low"]


class Vulnerability(BaseModel):
    """A single extracted vulnerability, enriched with its project name.

    Mirrors a row of the ``vulnerabilities`` table joined with ``projects`` to
    resolve ``project_name``.
    """

    id: str
    project_id: str
    project_name: str
    fact_id: str
    title: str
    description: str
    severity: Severity
    discovered_at: str
    source_intent_id: str | None = None
    source_intent_description: str | None = None
    source_worker: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    related_fact_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    process: list[dict[str, str]] = Field(default_factory=list)
    proof_packets: list[dict[str, str]] = Field(default_factory=list)


class VulnerabilitySummary(BaseModel):
    """Counts of vulnerabilities grouped by severity level."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class VulnerabilityExportRequest(BaseModel):
    """Parameters for a vulnerability export.

    ``format`` is constrained to the supported output formats so an unsupported
    value is rejected as a validation error (design.md error handling: "Supported
    formats: json, csv"). The optional ``severity`` and ``project_id`` fields carry
    the active filters so the export reflects exactly what the user is viewing.
    """

    format: Literal["json", "csv"]
    severity: Severity | None = None
    project_id: str | None = None
