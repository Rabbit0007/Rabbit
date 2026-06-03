"""Unit tests for the vulnerability extraction service and router (task 4.6).

These tests are additive and exercise:

* :mod:`cairn.server.vulnerability_extraction` -- severity pattern matching and
  the ``scan_project_facts`` upsert/reconcile behaviour.
* :mod:`cairn.server.routers.vulnerabilities` -- the list, summary, export and
  refresh endpoints, including filter combinations and export edge cases.

The vulnerabilities router carries no built-in auth dependency (in the real app,
auth is applied via ``app.include_router(..., dependencies=[Depends(require_auth)])``
in ``app.py``). Mounting only the router in a dedicated test app therefore needs
no authentication, which keeps these tests focused on the router logic itself.

Test data (projects + facts) is created with direct inserts through
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
from cairn.server.vulnerability_extraction import (
    categorize_severity,
    extract_vulnerabilities,
    scan_all_projects,
    scan_project_facts,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def vuln_app(temp_db) -> FastAPI:
    """A minimal FastAPI app mounting only the vulnerabilities router.

    Depends on ``temp_db`` (from conftest) so the database is configured before
    the router's endpoints query it.
    """
    from cairn.server.routers import vulnerabilities

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


# Representative fact descriptions for each severity level. The strings are
# chosen to match exactly one severity category in the extraction patterns.
CRITICAL_DESC = "SQL injection found in the login form allowing data dump"
HIGH_DESC = "Reflected XSS in the search parameter of the results page"
MEDIUM_DESC = "Information disclosure via verbose API responses"
LOW_DESC = "Missing security header: X-Frame-Options not set"
BENIGN_DESC = "The homepage renders a static marketing banner"


# ---------------------------------------------------------------------------
# Extraction service: severity pattern matching (requirements 6.1, 6.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (CRITICAL_DESC, "critical"),
        ("Remote code execution via deserialization", "critical"),
        ("Authentication bypass on the admin panel", "critical"),
        (HIGH_DESC, "high"),
        ("Server-side request forgery against metadata endpoint", "high"),
        ("Path traversal allows reading /etc/passwd", "high"),
        (MEDIUM_DESC, "medium"),
        ("CSRF token missing on the settings form", "medium"),
        (LOW_DESC, "low"),
        ("Verbose error message leaks framework version", "low"),
    ],
)
def test_categorize_severity_matches_expected_level(description, expected):
    """Each pattern category resolves to its documented severity level."""
    assert categorize_severity(description) == expected


def test_categorize_severity_returns_none_for_benign_description():
    """A description with no security-relevant keyword yields no severity."""
    assert categorize_severity(BENIGN_DESC) is None


def test_categorize_severity_empty_string_returns_none():
    """An empty description never produces a severity."""
    assert categorize_severity("") is None


def test_categorize_severity_picks_highest_when_multiple_match():
    """A description touching multiple categories is classified at its most
    severe level (critical wins over a medium 'information disclosure')."""
    description = "SQL injection leading to information disclosure of all users"
    assert categorize_severity(description) == "critical"


def test_categorize_severity_case_insensitive():
    """Pattern matching ignores case."""
    assert categorize_severity("SQL INJECTION in the API") == "critical"


def test_extract_vulnerabilities_returns_single_with_title_and_severity():
    """A matching description yields exactly one vulnerability with a non-empty
    title and the correct severity."""
    extracted = extract_vulnerabilities(CRITICAL_DESC)
    assert len(extracted) == 1
    vuln = extracted[0]
    assert vuln.severity == "critical"
    assert vuln.title
    assert "SQL 注入" in vuln.description


def test_extract_vulnerabilities_empty_for_benign_description():
    """A non-matching description yields no vulnerabilities."""
    assert extract_vulnerabilities(BENIGN_DESC) == []


@pytest.mark.parametrize(
    "description",
    [
        (
            "S2-045 (CVE-2017-5638)：已测试 Content-Type header 注入检测，"
            "未触发（不脆弱），无命令执行。"
        ),
        (
            "SQL 注入测试：username 参数响应与正常请求完全一致，"
            "无 SQL 错误回显，无响应差异，无可利用的注入迹象。"
        ),
        "CVE-2023-46604 可能存在，应优先尝试利用。",
        (
            "Ghostcat（CVE-2020-1938）攻击路径验证完成，结论：不可利用。"
            "端口 8009 返回 HTTP 400，无法读取 /WEB-INF/web.xml。"
        ),
        (
            "DSS Web 漏洞（CNVD-2017-06001 SQLi）已测试，"
            "目标路径 /portal/attachment_downloadByUrlAtt.action 不存在，扫描未命中。"
        ),
        "攻击面极为有限：无表单可注入，无 API 可未授权访问，所有路径均需有效会话。",
        "结论：HTTPS 8443 未提供任何认证绕过、未授权端点或更宽松的会话管理机制。",
    ],
)
def test_extract_vulnerabilities_ignores_failed_or_speculative_findings(description):
    """Failed, non-applicable, and speculative tests are not report findings."""
    assert extract_vulnerabilities(description) == []


def test_extract_vulnerabilities_keeps_confirmed_unauthorized_api():
    """A concrete, positively validated unauthorized API remains reportable."""
    description = (
        "突破性发现：/config/realtime_getStatusJson.action 是未授权 JSON API，"
        "无需认证直接返回 JSON 系统状态数据。"
        "该端点未获取到凭证、源码或 RCE 路径。"
    )

    extracted = extract_vulnerabilities(description)

    assert len(extracted) == 1
    assert extracted[0].severity == "high"


# ---------------------------------------------------------------------------
# Extraction service: scan_project_facts upsert / reconcile (req 6.4, 6.5)
# ---------------------------------------------------------------------------


def _count_vulns(project_id: str | None = None) -> int:
    with db.get_conn() as conn:
        if project_id is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM vulnerabilities"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM vulnerabilities WHERE project_id = ?",
                (project_id,),
            ).fetchone()
    return int(row["n"])


def test_scan_project_facts_extracts_matching_facts(temp_db):
    """Scanning a project materializes one vulnerability per matching fact and
    skips benign facts."""
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)
    _insert_fact("f3", "p1", BENIGN_DESC)

    count = scan_project_facts("p1")

    assert count == 2
    assert _count_vulns("p1") == 2


def test_scan_project_facts_no_duplicates_on_rescan(temp_db):
    """Re-scanning the same facts upserts rather than inserting duplicates."""
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)

    first = scan_project_facts("p1")
    second = scan_project_facts("p1")

    assert first == 2
    assert second == 2
    assert _count_vulns("p1") == 2


def test_scan_project_facts_preserves_discovered_at_on_rescan(temp_db):
    """The original discovery time is stable across re-scans (upsert preserves
    discovered_at)."""
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)

    scan_project_facts("p1")
    with db.get_conn() as conn:
        first_ts = conn.execute(
            "SELECT discovered_at FROM vulnerabilities WHERE fact_id = 'f1'"
        ).fetchone()["discovered_at"]

    scan_project_facts("p1")
    with db.get_conn() as conn:
        second_ts = conn.execute(
            "SELECT discovered_at FROM vulnerabilities WHERE fact_id = 'f1'"
        ).fetchone()["discovered_at"]

    assert first_ts == second_ts


def test_scan_project_facts_removes_stale_vulnerabilities(temp_db):
    """A fact whose description no longer matches is removed from the table."""
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    scan_project_facts("p1")
    assert _count_vulns("p1") == 1

    # Update the fact so it no longer matches any severity pattern.
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE facts SET description = ? WHERE id = 'f1'",
            (BENIGN_DESC,),
        )

    scan_project_facts("p1")
    assert _count_vulns("p1") == 0


def test_scan_project_facts_ignores_bare_unsupported_claim(temp_db):
    """A one-line vulnerability claim without reproducible evidence is skipped."""
    _insert_project("p1", "Project One")
    _insert_fact("f1", "p1", "确认存在 SQL 注入漏洞。")

    assert scan_project_facts("p1") == 0
    assert _count_vulns("p1") == 0


def test_scan_project_facts_removes_explicitly_corrected_fact(temp_db):
    """Later append-only facts can explicitly retract an earlier false finding."""
    _insert_project("p1", "Project One")
    _insert_fact(
        "f1",
        "p1",
        "确认存在 Ghostcat CVE-2020-1938 本地文件包含漏洞，"
        "目标端口 8009 可读取 /WEB-INF/web.xml。",
    )
    _insert_fact(
        "f2",
        "p1",
        "修正此前事实 f1 中的错误结论：端口 8009 为 HTTP Connector，"
        "Ghostcat CVE-2020-1938 不可利用。",
    )

    assert scan_project_facts("p1") == 0
    assert _count_vulns("p1") == 0


def test_scan_all_projects_scans_every_project(temp_db):
    """scan_all_projects reconciles vulnerabilities across all projects."""
    _insert_project("p1", "Project One")
    _insert_project("p2", "Project Two")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p2", HIGH_DESC)
    _insert_fact("f3", "p2", MEDIUM_DESC)

    total = scan_all_projects()

    assert total == 3
    assert _count_vulns("p1") == 1
    assert _count_vulns("p2") == 2


# ---------------------------------------------------------------------------
# Shared fixture: a populated database for router tests
# ---------------------------------------------------------------------------


@pytest.fixture
def populated(temp_db):
    """Create two projects with a spread of severities and scan them.

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

    scan_all_projects()

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
    scan_project_facts("p1")

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["fact_id"] == "f014"
    assert body[0]["related_fact_ids"] == ["f014"]
    assert "最终确认事实为 f014" not in body[0]["description"]
    packet = body[0]["proof_packets"][0]
    assert "/invoker/readonly" in packet["request"]
    assert "Host: 127.0.0.1:60001" in packet["request"]
    assert "uid=0" not in packet["request"]
    assert "依据当前项目 p1" in packet["note"]


def test_proof_packet_reconstructs_sql_request_from_same_project_fact(client, temp_db):
    """SQL proof uses the recorded endpoint instead of a fixed lab template."""
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
    scan_project_facts("p1")

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})

    assert resp.status_code == 200
    packet = resp.json()[0]["proof_packets"][0]
    assert "GET /app/item?id=1" in packet["request"]
    assert "Host: 10.20.30.40" in packet["request"]
    assert "/sqli-labs/" not in packet["request"]
    assert "p1" in packet["note"]


def test_proof_packets_keep_parameters_scoped_to_each_endpoint(client, temp_db):
    """One fact's parameters are not copied onto a different endpoint."""
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
    scan_project_facts("p1")

    resp = client.get("/api/vulnerabilities", params={"project_id": "p1"})

    assert resp.status_code == 200
    packets = resp.json()[0]["proof_packets"]
    requests = {packet["title"]: packet["request"] for packet in packets}
    status_request = next(
        request for title, request in requests.items() if "getStatusJson" in title
    )
    keeper_request = next(
        request for title, request in requests.items() if "loginKeeper" in title
    )
    assert "statusName=systemDiskStatus" in status_request
    assert "statusName=systemDiskStatus" not in keeper_request


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
    assert "未记录真实请求/响应数据包。" in text
    assert "Beta" not in text


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


def test_refresh_rescans_and_returns_summary(client, temp_db):
    """POST /refresh re-scans facts and returns the updated summary."""
    _insert_project("p1", "Alpha")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    _insert_fact("f2", "p1", HIGH_DESC)

    # Nothing scanned yet.
    assert _count_vulns() == 0

    resp = client.post("/api/vulnerabilities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    assert _count_vulns() == 2


def test_refresh_picks_up_new_facts(client, temp_db):
    """A second refresh reflects facts added after the first scan."""
    _insert_project("p1", "Alpha")
    _insert_fact("f1", "p1", CRITICAL_DESC)
    client.post("/api/vulnerabilities/refresh")

    _insert_fact("f2", "p1", MEDIUM_DESC)
    resp = client.post("/api/vulnerabilities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"critical": 1, "high": 0, "medium": 1, "low": 0}
