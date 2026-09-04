"""Product tests for native Finding projection and vulnerability reports.

Findings are the source of truth.  These tests seed explicit Cairn-Y Findings
and exercise the read-only vulnerability projection, filters, exports and
refresh behaviour.

The vulnerabilities router carries no built-in auth dependency (in the real app,
auth is applied via ``app.include_router(..., dependencies=[Depends(require_auth)])``
in ``app.py``). Mounting only the router in a dedicated test app therefore needs
no authentication, which keeps these tests focused on the router logic itself.

Test data (projects + facts + findings) is created with direct inserts through
``cairn.server.db.get_conn()``. The shared ``temp_db`` fixture from
``conftest.py`` provides a fresh, isolated SQLite database per test (core +
auth + product schemas configured).

Covers requirements 6.1-6.7, 7.1-7.6, 8.1-8.6.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.finding_projection import sync_project_findings

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def vuln_app(temp_db, monkeypatch) -> FastAPI:
    """A minimal FastAPI app mounting only the vulnerabilities router.

    Depends on ``temp_db`` (from conftest) so the database is configured before
    the router's endpoints query it.
    """
    from cairn.server.routers import vulnerabilities

    monkeypatch.setattr(
        "cairn.server.report_composer_service._resolve_report_composer_profile",
        lambda: None,
    )

    app = FastAPI()
    app.include_router(vulnerabilities.router)
    return app


@pytest.fixture
def client(vuln_app) -> TestClient:
    """TestClient for the vulnerabilities router."""
    return TestClient(vuln_app)


def _insert_project(project_id: str, title: str) -> None:
    """Insert a project row directly into the DB."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) "
            "VALUES (?, ?, 'active', ?)",
            (project_id, title, "2024-01-01T00:00:00Z"),
        )


def _insert_fact(fact_id: str, project_id: str, description: str) -> None:
    """Insert a fact row directly into the DB."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            (fact_id, project_id, description),
        )


# Representative descriptions used as explicit Finding content.  Their text has
# no classification side effects; severity is supplied by Execute in the Finding.
CRITICAL_DESC = "SQL injection found in the login form allowing data dump"
HIGH_DESC = "Reflected XSS in the search parameter of the results page"
MEDIUM_DESC = "Information disclosure via verbose API responses"
LOW_DESC = "Missing security header: X-Frame-Options not set"
BENIGN_DESC = "The homepage renders a static marketing banner"


def _insert_finding(
    finding_id: str,
    project_id: str,
    fact_id: str,
    description: str,
    severity: str,
    *,
    title: str | None = None,
    data: dict | None = None,
) -> None:
    """Insert one Execute-owned Finding and refresh its product projection."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO findings "
            "(id, project_id, title, description, severity, kind, data_json, fact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'security_vulnerability', ?, ?, ?)",
            (
                finding_id,
                project_id,
                title or f"Finding {finding_id}",
                description,
                severity,
                json.dumps(data or {}, ensure_ascii=False),
                fact_id,
                "2024-01-01T00:00:01Z",
            ),
        )
        sync_project_findings(conn, project_id)


def _count_vulns(project_id: str | None = None) -> int:
    with db.get_conn() as conn:
        if project_id is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM vulnerabilities").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM vulnerabilities WHERE project_id = ?",
                (project_id,),
            ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Shared fixture: a populated database for router tests
# ---------------------------------------------------------------------------


@pytest.fixture
def populated(temp_db):
    """Create two projects with a spread of explicit Finding severities.

    Project ``p1`` (Alpha): critical + high + medium + benign.
    Project ``p2`` (Beta):  high + low.

    Returns a small dict describing the expected per-project / per-severity
    layout for assertions.
    """
    _insert_project("p1", "Alpha")
    _insert_project("p2", "Beta")

    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)
    _insert_fact("f3", "p1", MEDIUM_DESC)
    _insert_fact("f4", "p1", BENIGN_DESC)

    _insert_fact("f5", "p2", HIGH_DESC)
    _insert_fact("f6", "p2", LOW_DESC)

    _insert_finding("finding-1", "p1", "f1", CRITICAL_DESC, "critical")
    _insert_finding("finding-2", "p1", "f2", HIGH_DESC, "high")
    _insert_finding("finding-3", "p1", "f3", MEDIUM_DESC, "medium")
    _insert_finding("finding-5", "p2", "f5", HIGH_DESC, "high")
    _insert_finding("finding-6", "p2", "f6", LOW_DESC, "low")

    return {
        "total": 5,
        "by_severity": {"critical": 1, "high": 2, "medium": 1, "low": 1},
        "p1_total": 3,
        "p2_total": 2,
    }


# ---------------------------------------------------------------------------
# List endpoint: filters (requirements 6.3, 7.1-7.6)
# ---------------------------------------------------------------------------


def test_list_no_filters_returns_all(client, populated):
    """With no filters, every vulnerability is returned (req 7.5)."""
    resp = client.get("/api/vulnerabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == populated["total"]
    # Each item carries title, severity and resolved project name (req 6.3).
    for item in body:
        assert item["title"]
        assert item["severity"] in {"critical", "high", "medium", "low"}
        assert item["project_name"] in {"Alpha", "Beta"}


def test_list_severity_filter(client, populated):
    """Filtering by severity returns only that level (req 7.1)."""
    resp = client.get("/api/vulnerabilities", params={"severity": "high"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == populated["by_severity"]["high"]
    assert all(item["severity"] == "high" for item in body)


def test_list_project_filter(client, populated):
    """Filtering by project returns only that project's findings (req 7.2)."""
    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == populated["p1_total"]
    assert all(item["project_name"] == "Alpha" for item in body)


def test_list_both_filters_and_logic(client, populated):
    """Severity AND project filters combine (req 7.3): only p2's high finding."""
    resp = client.get(
        "/api/vulnerabilities",
        params={"severity": "high", "project_id": "p2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["severity"] == "high"
    assert body[0]["project_name"] == "Beta"


def test_list_filter_matches_nothing_returns_empty(client, populated):
    """A valid filter that matches nothing returns an empty list (req 7.4)."""
    # p2 has no critical findings.
    resp = client.get(
        "/api/vulnerabilities",
        params={"severity": "critical", "project_id": "p2"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_invalid_severity_returns_422(client, populated):
    """An unsupported severity value is rejected by validation (req 7.6)."""
    resp = client.get("/api/vulnerabilities", params={"severity": "bogus"})
    assert resp.status_code == 422


def test_list_unknown_project_returns_404(client, populated):
    """A project_id that does not exist yields a 404 rather than empty list."""
    resp = client.get("/api/vulnerabilities", params={"project_id": "nope"})
    assert resp.status_code == 404


def test_list_ordered_most_severe_first(client, populated):
    """Results are ordered most-severe-first."""
    resp = client.get("/api/vulnerabilities")
    assert resp.status_code == 200
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    severities = [rank[item["severity"]] for item in resp.json()]
    assert severities == sorted(severities)


def test_list_merges_same_cve_and_keeps_final_confirmation(client, temp_db):
    """Report output excludes an unconfirmed earlier attempt for the same CVE."""
    _insert_project("p1", "JBoss Test")
    _insert_fact(
        "f002",
        "p1",
        "CVE-2017-12149 JBoss 反序列化/远程命令执行风险；"
        "相关端点：/invoker/readonly；使用 whoami 作为命令执行证明；"
        "该阶段尚未拿到最终命令执行结果。",
    )
    _insert_fact(
        "f014",
        "p1",
        "CVE-2017-12149 JBoss 远程命令执行已成功验证；"
        "目标 http://127.0.0.1:60001；相关端点：/invoker/readonly；"
        "利用链涉及 ysoserial 载荷；CommonsCollections 载荷被用于验证；"
        "whoami output: root。id output: uid=0(root)。",
    )
    _insert_finding(
        "finding-cve-2017-12149",
        "p1",
        "f014",
        "CVE-2017-12149 JBoss 远程命令执行已成功验证；whoami output: root；id output: uid=0(root)。",
        "critical",
        title="CVE-2017-12149 远程命令执行",
    )

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["fact_id"] == "f014"
    assert body[0]["related_fact_ids"] == ["f014"]
    assert "最终确认事实为 f014" not in body[0]["description"]
    # A narrative fact is not silently presented as an original HTTP packet.
    assert body[0]["proof_packets"] == []


def test_proof_packet_is_not_reconstructed_from_narrative_fact(client, temp_db):
    """Narrative evidence must not be mislabeled as an original packet."""
    _insert_project("p1", "SQL Test")
    _insert_fact(
        "origin",
        "p1",
        "http://10.20.30.40/",
    )
    _insert_fact(
        "f001",
        "p1",
        "确认存在 SQL 注入漏洞：GET /app/item?id=1 请求中，"
        "id=1' UNION SELECT version(),user()--+ 可回显 MySQL 版本和 root@localhost 用户。",
    )
    _insert_finding(
        "finding-sqli",
        "p1",
        "f001",
        "GET /app/item?id=1 的 SQL 注入已确认，可回显数据库版本与当前用户。",
        "critical",
        title="SQL 注入",
    )

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})

    assert resp.status_code == 200
    assert resp.json()[0]["proof_packets"] == []


def test_multiple_narrative_endpoints_do_not_create_fake_packets(client, temp_db):
    """Several endpoint mentions still do not constitute an original packet."""
    _insert_project("p1", "API Test")
    _insert_fact("origin", "p1", "http://10.20.30.40/")
    _insert_fact(
        "f001",
        "p1",
        "发现两个未授权 JSON API：\n"
        "- /config/realtime_getStatusJson.action 接受 POST 请求，"
        "statusName=systemDiskStatus 无需认证直接返回 JSON 系统状态数据。\n"
        "- /config/realtime_loginKeeper.action 使用 GET/POST 均返回 true，无需认证。",
    )
    _insert_finding(
        "finding-api",
        "p1",
        "f001",
        "两个 JSON API 已确认无需认证即可读取状态数据。",
        "high",
        title="未授权 API",
    )

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})

    assert resp.status_code == 200
    assert resp.json()[0]["proof_packets"] == []


def test_batch_status_update_marks_multiple_merged_vulnerabilities(client, temp_db):
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)
    _insert_finding("finding-critical", "p1", "f1", CRITICAL_DESC, "critical")
    _insert_finding("finding-high", "p1", "f2", HIGH_DESC, "high")

    listed = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()
    ids = [item["id"] for item in listed]

    resp = client.post("/api/vulnerabilities/batch/status", json={"ids": ids, "status": "ignored"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["status"] == "ignored"
    refreshed = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()
    assert {item["status"] for item in refreshed} == {"ignored"}


def test_batch_status_update_reports_missing_ids_but_updates_existing(client, temp_db):
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_finding("finding-critical", "p1", "f1", CRITICAL_DESC, "critical")

    listed = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()
    vuln_id = listed[0]["id"]

    resp = client.post(
        "/api/vulnerabilities/batch/status",
        json={"ids": [vuln_id, "missing-id"], "status": "ignored"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["missing_ids"] == ["missing-id"]
    refreshed = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()
    assert refreshed[0]["status"] == "ignored"


# ---------------------------------------------------------------------------
# Summary endpoint (requirements 6.3, 6.7)
# ---------------------------------------------------------------------------


def test_summary_counts_grouped_by_severity(client, populated):
    """The summary returns per-severity counts matching the data."""
    resp = client.get("/api/vulnerabilities/summary")
    assert resp.status_code == 200
    assert resp.json() == populated["by_severity"]


def test_summary_all_zero_when_empty(client, temp_db):
    """With no vulnerabilities every severity count is zero (req 6.7)."""
    resp = client.get("/api/vulnerabilities/summary")
    assert resp.status_code == 200
    assert resp.json() == {"critical": 0, "high": 0, "medium": 0, "low": 0}


# ---------------------------------------------------------------------------
# Export endpoint: JSON (requirements 8.1, 8.2, 8.3)
# ---------------------------------------------------------------------------


def test_export_json_content_and_summary(client, populated):
    """JSON export contains a summary object and the full findings array."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "json"})
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert "vulnerabilities.json" in resp.headers["content-disposition"]

    payload = json.loads(resp.content)
    assert payload["summary"] == populated["by_severity"]
    assert len(payload["vulnerabilities"]) == populated["total"]
    # The summary totals sum to the number of exported findings (req 8.3).
    assert sum(payload["summary"].values()) == len(payload["vulnerabilities"])


def test_export_json_respects_filters(client, populated):
    """JSON export honours the active severity/project filters (req 8.1)."""
    resp = client.get(
        "/api/vulnerabilities/export",
        params={"format": "json", "project_id": "p1"},
    )
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert len(payload["vulnerabilities"]) == populated["p1_total"]
    assert sum(payload["summary"].values()) == populated["p1_total"]
    assert all(
        v["project_name"] == "Alpha" for v in payload["vulnerabilities"]
    )


def test_export_json_supports_single_vulnerability_scope(client, populated):
    """A vulnerability_id export contains only that merged finding."""
    target = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()[0]
    resp = client.get(
        "/api/vulnerabilities/export",
        params={"format": "json", "vulnerability_id": target["id"]},
    )
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert len(payload["vulnerabilities"]) == 1
    assert payload["vulnerabilities"][0]["id"] == target["id"]
    assert sum(payload["summary"].values()) == 1


def test_export_unknown_vulnerability_returns_404(client, populated):
    """An unknown vulnerability_id is rejected instead of exporting everything."""
    resp = client.get(
        "/api/vulnerabilities/export",
        params={"format": "json", "vulnerability_id": "missing"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Export endpoint: CSV (requirements 8.2, 8.3)
# ---------------------------------------------------------------------------


def test_export_csv_content_and_summary(client, populated):
    """CSV export contains a summary section followed by a data table."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "csv"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "vulnerabilities.csv" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))

    # Summary section leads the file.
    assert rows[0] == ["summary"]
    assert rows[1] == ["severity", "count"]
    summary_counts = {rows[i][0]: int(rows[i][1]) for i in range(2, 6)}
    assert summary_counts == populated["by_severity"]

    # The data header appears after the summary; data rows follow.
    header_index = rows.index(
        [
            "severity",
            "title",
            "description",
            "project_name",
            "discovered_at",
            "fact_id",
            "related_fact_ids",
            "evidence",
            "proof_packets",
        ]
    )
    data_rows = [r for r in rows[header_index + 1 :] if r]
    assert len(data_rows) == populated["total"]


def test_export_csv_respects_filters(client, populated):
    """CSV export honours the active filters (req 8.1)."""
    resp = client.get(
        "/api/vulnerabilities/export",
        params={"format": "csv", "severity": "high"},
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    header_index = rows.index(
        [
            "severity",
            "title",
            "description",
            "project_name",
            "discovered_at",
            "fact_id",
            "related_fact_ids",
            "evidence",
            "proof_packets",
        ]
    )
    data_rows = [r for r in rows[header_index + 1 :] if r]
    assert len(data_rows) == populated["by_severity"]["high"]
    assert all(r[0] == "high" for r in data_rows)


def test_export_markdown_content_and_scope(client, populated):
    """Markdown export is a readable report and honours project scope."""
    resp = client.get(
        "/api/vulnerabilities/export",
        params={"format": "md", "project_id": "p1"},
    )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "p1.md" in resp.headers["content-disposition"]
    text = resp.text
    assert text.startswith("# Alpha - 渗透测试漏洞报告")
    assert "## 报告概览" in text
    assert "## 漏洞清单" in text
    assert "## 项目：Alpha（`p1`）" in text
    assert "#### 漏洞概述" in text
    assert "#### 漏洞证明" in text
    assert "#### 影响结论" in text
    assert "#### 成因分析" in text
    assert "#### 修复建议" in text
    assert "未记录真实请求/响应数据包。" in text
    assert "Beta" not in text


def test_vulnerability_report_endpoint_returns_structured_template_report(client, temp_db):
    _insert_project("p1", "SQL Test")
    _insert_fact("origin", "p1", "http://10.20.30.40/")
    _insert_fact(
        "f001",
        "p1",
        "确认存在 SQL 注入漏洞：GET /app/item?id=1 请求中，"
        "id=1' UNION SELECT version(),user()--+ 可回显 MySQL 版本和 root@localhost 用户。",
    )
    _insert_finding(
        "finding-sqli",
        "p1",
        "f001",
        "确认 GET /app/item?id=1 存在 SQL 注入，可回显 MySQL 版本和 root@localhost 用户。",
        "critical",
        title="SQL 注入",
        data={"location": "GET /app/item?id=1", "proof": "回显 MySQL 版本与当前用户"},
    )

    vuln = client.get("/api/vulnerabilities", params={"project_id": "p1"}).json()[0]
    resp = client.get(f"/api/vulnerabilities/{vuln['id']}/report")

    assert resp.status_code == 200
    body = resp.json()
    assert body["composer_source"] == "template"
    assert body["project_id"] == "p1"
    assert body["vulnerability_type"] == "SQL 注入"
    assert body["attack_surface"]
    assert any(point["label"] == "命中接口" for point in body["proof_points"])
    assert "关键输入" in body["vulnerability_proof"] or "关键参数" in body["vulnerability_proof"]
    assert body["remediation"]


def test_export_pdf_content(client, populated):
    """PDF export returns a downloadable PDF report."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "pdf"})
    assert resp.status_code == 200
    assert "application/pdf" in resp.headers["content-type"]
    assert "vulnerabilities.pdf" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


def test_export_docx_content(client, populated):
    """Word export returns a downloadable docx report."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "docx"})
    assert resp.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in resp.headers["content-type"]
    )
    assert "vulnerabilities.docx" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"PK")


# ---------------------------------------------------------------------------
# Export endpoint: edge cases (requirements 8.4, 8.5)
# ---------------------------------------------------------------------------


def test_export_unsupported_format_returns_422(client, populated):
    """An unsupported export format is rejected with 422 (req 8.4)."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "xlsx"})
    assert resp.status_code == 422


def test_export_json_zero_results_valid_file(client, temp_db):
    """With no vulnerabilities, JSON export is a valid summary-only file (req 8.5)."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "json"})
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["summary"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    assert payload["vulnerabilities"] == []


def test_export_csv_zero_results_valid_file(client, temp_db):
    """With no vulnerabilities, CSV export is a valid summary-only file (req 8.5)."""
    resp = client.get("/api/vulnerabilities/export", params={"format": "csv"})
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["summary"]
    summary_counts = {rows[i][0]: int(rows[i][1]) for i in range(2, 6)}
    assert summary_counts == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    # Column header is still present; no data rows follow it.
    header_index = rows.index(
        [
            "severity",
            "title",
            "description",
            "project_name",
            "discovered_at",
            "fact_id",
            "related_fact_ids",
            "evidence",
            "proof_packets",
        ]
    )
    data_rows = [r for r in rows[header_index + 1 :] if r]
    assert data_rows == []


def test_export_default_format_is_json(client, populated):
    """Omitting the format parameter defaults to JSON."""
    resp = client.get("/api/vulnerabilities/export")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Refresh endpoint (requirement 6.4, 6.5)
# ---------------------------------------------------------------------------


def test_refresh_wakes_report_agent_without_rule_scanning(client, temp_db, monkeypatch):
    """Refresh queues the model agent and never falls back to keyword rules."""
    _insert_project("p1", "Alpha")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)

    # Nothing scanned yet.
    assert _count_vulns() == 0

    called = []
    monkeypatch.setattr("cairn.server.routers.vulnerabilities.request_report_sync", lambda: called.append(True))
    resp = client.post("/api/vulnerabilities/refresh")
    assert resp.status_code == 200
    assert called == [True]
    assert resp.json() == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    assert _count_vulns() == 0


def test_refresh_does_not_materialize_new_facts_with_default_rules(client, temp_db):
    """New facts remain for the independent model agent to analyze."""
    _insert_project("p1", "Alpha")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    client.post("/api/vulnerabilities/refresh")

    _insert_fact("f2", "p1", MEDIUM_DESC)
    resp = client.post("/api/vulnerabilities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"critical": 0, "high": 0, "medium": 0, "low": 0}
