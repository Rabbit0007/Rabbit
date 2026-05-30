"""Vulnerability report router.

This is an additive router exposing the ``/api/vulnerabilities`` endpoints. Task
4.3 implements the *list* and *summary* endpoints; task 4.4 adds the *export*
and *refresh* endpoints on this same router.

The router is read-only with respect to existing core tables: it reads from the
``vulnerabilities`` table (created by :mod:`cairn.server.product_db` and
populated by :mod:`cairn.server.vulnerability_extraction`) joined with the
``projects`` table to resolve each finding's ``project_name``.

Response shapes follow :mod:`cairn.server.vulnerabilities_models`.
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from cairn.server.db import get_conn
from cairn.server.vulnerabilities_models import (
    Severity,
    Vulnerability,
    VulnerabilitySummary,
)
from cairn.server.vulnerability_extraction import scan_all_projects

router = APIRouter(prefix="/api/vulnerabilities", tags=["vulnerabilities"])

# Display ordering for the report: most severe first, then most recently
# discovered, with the id as a final deterministic tiebreaker. Implemented as a
# SQL ``CASE`` so the ordering is applied in the database rather than in Python.
_SEVERITY_RANK_SQL = (
    "CASE v.severity "
    "WHEN 'critical' THEN 0 "
    "WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 3 "
    "ELSE 4 END"
)


@router.get("", response_model=list[Vulnerability])
def list_vulnerabilities(
    severity: Severity | None = Query(
        default=None,
        description="Optional severity filter (critical, high, medium, low).",
    ),
    project_id: str | None = Query(
        default=None,
        description="Optional project filter; restricts results to one project.",
    ),
) -> list[Vulnerability]:
    """List vulnerabilities, optionally filtered by severity and/or project.

    The ``severity`` and ``project_id`` query parameters are independent filters
    combined with AND logic (requirements 7.1, 7.2, 7.3): a vulnerability is
    included only when it satisfies *every* active filter. When neither filter
    is supplied the complete list is returned (requirement 7.5).

    ``severity`` is validated against the allowed levels by FastAPI, so an
    unsupported value yields a 422 validation error. When ``project_id`` refers
    to a project that does not exist, the request is rejected with a 404 rather
    than silently returning an empty list (design error handling: "Project not
    found (filter)"). A valid filter that simply matches nothing returns an
    empty list (requirement 7.4).

    Each result includes the finding's ``title``, ``severity`` and source
    ``project_name`` (requirement 6.3), resolved by joining ``vulnerabilities``
    with ``projects``.
    """
    clauses: list[str] = []
    params: list[str] = []

    if severity is not None:
        clauses.append("v.severity = ?")
        params.append(severity)

    if project_id is not None:
        clauses.append("v.project_id = ?")
        params.append(project_id)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    with get_conn() as conn:
        if project_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Project not found")

        rows = conn.execute(
            f"""
            SELECT
                v.id          AS id,
                v.project_id  AS project_id,
                p.title       AS project_name,
                v.fact_id     AS fact_id,
                v.title       AS title,
                v.description AS description,
                v.severity    AS severity,
                v.discovered_at AS discovered_at
            FROM vulnerabilities v
            JOIN projects p ON p.id = v.project_id
            {where_sql}
            ORDER BY {_SEVERITY_RANK_SQL}, v.discovered_at DESC, v.id
            """,
            params,
        ).fetchall()

    return [Vulnerability(**dict(row)) for row in rows]


@router.get("/summary", response_model=VulnerabilitySummary)
def vulnerabilities_summary() -> VulnerabilitySummary:
    """Return the total vulnerability counts grouped by severity level.

    Provides the per-severity totals shown on the report page (requirement
    6.3). When no vulnerabilities exist, every severity count is zero — the
    :class:`VulnerabilitySummary` field defaults guarantee a complete object
    with all four levels present (requirement 6.7).
    """
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS n FROM vulnerabilities GROUP BY severity"
        ).fetchall()

    for row in rows:
        # The severity CHECK constraint guarantees the value is one of the four
        # known levels, but guard defensively against unexpected rows.
        if row["severity"] in counts:
            counts[row["severity"]] = int(row["n"])

    return VulnerabilitySummary(**counts)


# Columns emitted, in order, for each vulnerability in a CSV export. Mirrors the
# fields required by requirement 8.2 (severity, title, description, project name,
# discovery date).
_CSV_COLUMNS = ("severity", "title", "description", "project_name", "discovered_at")

# Severity levels in display order, used to render the summary section so the
# per-level counts always appear in a stable, most-severe-first order.
_SUMMARY_ORDER = ("critical", "high", "medium", "low")


def _query_filtered_vulnerabilities(
    severity: str | None, project_id: str | None
) -> list[Vulnerability]:
    """Load vulnerabilities matching the active filters for export.

    This mirrors the filtering and ordering of :func:`list_vulnerabilities`
    (AND-combined ``severity`` / ``project_id`` filters, most-severe-first
    ordering) so an export reflects exactly what the user is viewing
    (requirement 8.1). It is a self-contained helper rather than a shared call
    into the list endpoint to keep that endpoint untouched.

    A ``project_id`` that does not exist yields a 404, consistent with the list
    endpoint; a valid filter that matches nothing yields an empty list, which
    the export layer renders as a summary-only file (requirement 8.5).
    """
    clauses: list[str] = []
    params: list[str] = []

    if severity is not None:
        clauses.append("v.severity = ?")
        params.append(severity)

    if project_id is not None:
        clauses.append("v.project_id = ?")
        params.append(project_id)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    with get_conn() as conn:
        if project_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Project not found")

        rows = conn.execute(
            f"""
            SELECT
                v.id          AS id,
                v.project_id  AS project_id,
                p.title       AS project_name,
                v.fact_id     AS fact_id,
                v.title       AS title,
                v.description AS description,
                v.severity    AS severity,
                v.discovered_at AS discovered_at
            FROM vulnerabilities v
            JOIN projects p ON p.id = v.project_id
            {where_sql}
            ORDER BY {_SEVERITY_RANK_SQL}, v.discovered_at DESC, v.id
            """,
            params,
        ).fetchall()

    return [Vulnerability(**dict(row)) for row in rows]


def _summarize(vulnerabilities: list[Vulnerability]) -> dict[str, int]:
    """Compute per-severity counts over an already-filtered result set.

    Counting the filtered rows (rather than re-querying the whole table)
    guarantees the summary totals sum to the number of exported vulnerabilities
    (requirement 8.3). When the list is empty every count is zero, producing the
    summary-only export of requirement 8.5.
    """
    counts = {level: 0 for level in _SUMMARY_ORDER}
    for vuln in vulnerabilities:
        if vuln.severity in counts:
            counts[vuln.severity] += 1
    return counts


def _render_json_export(vulnerabilities: list[Vulnerability]) -> str:
    """Render the JSON export body.

    The summary counts are placed in a top-level ``summary`` object and the
    findings (each carrying severity, description and project name, among the
    full set of fields) in a ``vulnerabilities`` array (requirements 8.1, 8.3).
    """
    payload = {
        "summary": _summarize(vulnerabilities),
        "vulnerabilities": [vuln.model_dump() for vuln in vulnerabilities],
    }
    return json.dumps(payload, indent=2)


def _render_csv_export(vulnerabilities: list[Vulnerability]) -> str:
    """Render the CSV export body.

    A summary section (per-severity counts) is written as header rows that
    precede the data rows (requirement 8.3), followed by a blank separator row,
    the column header, and one row per vulnerability with the severity, title,
    description, project name and discovery date columns (requirement 8.2).
    With zero vulnerabilities only the summary section and column header are
    emitted (requirement 8.5).
    """
    counts = _summarize(vulnerabilities)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Summary section as leading header rows.
    writer.writerow(["summary"])
    writer.writerow(["severity", "count"])
    for level in _SUMMARY_ORDER:
        writer.writerow([level, counts[level]])

    # Blank separator row between the summary section and the data table.
    writer.writerow([])

    # Data table: column header followed by one row per vulnerability.
    writer.writerow(list(_CSV_COLUMNS))
    for vuln in vulnerabilities:
        writer.writerow([getattr(vuln, column) for column in _CSV_COLUMNS])

    return buffer.getvalue()


@router.get("/export")
def export_vulnerabilities(
    format: str = Query(
        default="json",
        description="Export format; one of 'json' or 'csv'.",
    ),
    severity: str | None = Query(
        default=None,
        description="Optional severity filter (critical, high, medium, low).",
    ),
    project_id: str | None = Query(
        default=None,
        description="Optional project filter; restricts the export to one project.",
    ),
) -> Response:
    """Export vulnerabilities as a downloadable JSON or CSV file.

    The export respects the active ``severity`` and ``project_id`` filters so it
    contains exactly the vulnerabilities the user is currently viewing
    (requirement 8.1) and embeds a summary of per-severity totals (requirement
    8.3). JSON places the summary in a top-level ``summary`` object; CSV writes
    it as header rows ahead of the data rows.

    An unsupported ``format`` is rejected with a 422 naming the supported
    formats (requirement 8.4). When the filters match nothing, a valid file
    containing only the summary (all counts zero) is returned (requirement 8.5).

    ``severity`` is validated here (rather than via a ``Literal`` query type) so
    an unsupported severity yields the same shaped result as the list endpoint;
    an unknown severity simply matches nothing and produces a summary-only file.
    """
    normalized = format.lower()
    if normalized not in ("json", "csv"):
        raise HTTPException(status_code=422, detail="Supported formats: json, csv")

    vulnerabilities = _query_filtered_vulnerabilities(severity, project_id)

    if normalized == "json":
        body = _render_json_export(vulnerabilities)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="vulnerabilities.json"'
            },
        )

    body = _render_csv_export(vulnerabilities)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vulnerabilities.csv"'},
    )


@router.post("/refresh")
def refresh_vulnerabilities() -> VulnerabilitySummary:
    """Re-scan all project facts and return the refreshed per-severity summary.

    Delegates to the existing extraction service's
    :func:`~cairn.server.vulnerability_extraction.scan_all_projects`, which
    reconciles the ``vulnerabilities`` table against the current facts for every
    project (re-classifying matches and removing stale findings). The response
    is the updated summary so callers can reflect the new totals without a
    separate request to ``/summary``.
    """
    scan_all_projects()

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS n FROM vulnerabilities GROUP BY severity"
        ).fetchall()

    for row in rows:
        if row["severity"] in counts:
            counts[row["severity"]] = int(row["n"])

    return VulnerabilitySummary(**counts)
