import sqlite3
import tempfile
from pathlib import Path

from cairn.server import db, product_db
from cairn.server.vulnerabilities_models import (
    Vulnerability,
    VulnerabilitySummary,
    VulnerabilityExportRequest,
)

results = []

# 1. Create schema on a fresh DB (core schema first so projects FK target exists).
tmpdir = tempfile.mkdtemp()
dbpath = Path(tmpdir) / "cairn_test.db"
db.configure(dbpath)
product_db.configure_product_db()
results.append("schema applied cleanly (idempotent re-run next)")
# idempotency
product_db.configure_product_db()
results.append("schema re-applied (idempotent) OK")

# 2. Inspect the vulnerabilities table structure.
with db.get_conn() as conn:
    cols = conn.execute("PRAGMA table_info(vulnerabilities)").fetchall()
    colnames = [c["name"] for c in cols]
    results.append(f"columns: {colnames}")
    idx = conn.execute("PRAGMA index_list(vulnerabilities)").fetchall()
    results.append(f"indexes: {[i['name'] for i in idx]}")
    fks = conn.execute("PRAGMA foreign_key_list(vulnerabilities)").fetchall()
    results.append(f"fks: {[(f['table'], f['from'], f['to'], f['on_delete']) for f in fks]}")

# 3. FK cascade + CHECK + UNIQUE behavior.
with db.get_conn() as conn:
    conn.execute(
        "INSERT INTO projects (id, title, status, created_at) VALUES (?,?,?,?)",
        ("p1", "Proj", "active", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vulnerabilities (id, project_id, fact_id, title, description, severity, discovered_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("v1", "p1", "f1", "SQLi", "SQL injection found", "critical", "2024-01-02T00:00:00Z"),
    )

# CHECK constraint rejects invalid severity
try:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO vulnerabilities (id, project_id, fact_id, title, description, severity, discovered_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("vbad", "p1", "fbad", "X", "x", "extreme", "2024-01-02T00:00:00Z"),
        )
    results.append("CHECK constraint FAILED to reject invalid severity")
except sqlite3.IntegrityError:
    results.append("CHECK constraint rejects invalid severity OK")

# UNIQUE(project_id, fact_id) rejects duplicate
try:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO vulnerabilities (id, project_id, fact_id, title, description, severity, discovered_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("v2", "p1", "f1", "dup", "dup", "low", "2024-01-02T00:00:00Z"),
        )
    results.append("UNIQUE(project_id, fact_id) FAILED to reject duplicate")
except sqlite3.IntegrityError:
    results.append("UNIQUE(project_id, fact_id) rejects duplicate OK")

# ON DELETE CASCADE removes vulnerabilities when project deleted
with db.get_conn() as conn:
    conn.execute("DELETE FROM projects WHERE id = ?", ("p1",))
with db.get_conn() as conn:
    remaining = conn.execute("SELECT COUNT(*) c FROM vulnerabilities WHERE project_id='p1'").fetchone()["c"]
    results.append(f"cascade delete leaves {remaining} vulnerabilities (expect 0)")

# 4. Pydantic models instantiate and validate.
v = Vulnerability(
    id="v1", project_id="p1", project_name="Proj", fact_id="f1",
    title="SQLi", description="SQL injection", severity="critical",
    discovered_at="2024-01-02T00:00:00Z",
)
results.append(f"Vulnerability OK severity={v.severity}")

s = VulnerabilitySummary(critical=1, high=2, medium=0, low=3)
results.append(f"VulnerabilitySummary OK total={s.critical+s.high+s.medium+s.low}")
results.append(f"VulnerabilitySummary defaults={VulnerabilitySummary().model_dump()}")

req = VulnerabilityExportRequest(format="csv", severity="high", project_id="p1")
results.append(f"VulnerabilityExportRequest OK format={req.format}")

# invalid severity on model
try:
    Vulnerability(
        id="v", project_id="p", project_name="n", fact_id="f",
        title="t", description="d", severity="nope", discovered_at="now",
    )
    results.append("Vulnerability FAILED to reject invalid severity")
except Exception:
    results.append("Vulnerability rejects invalid severity OK")

# invalid export format
try:
    VulnerabilityExportRequest(format="xml")
    results.append("VulnerabilityExportRequest FAILED to reject xml")
except Exception:
    results.append("VulnerabilityExportRequest rejects xml OK")

print("\n".join(results))
print("ALL_VERIFICATIONS_DONE")
