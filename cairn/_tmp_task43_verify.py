"""Temporary verification harness for task 4.3 (vulnerabilities list/summary).

Spins up an isolated SQLite DB in a temp dir, applies the core + product
schemas, seeds projects and vulnerabilities, mounts ONLY the vulnerabilities
router on a minimal FastAPI app, and exercises the endpoints via TestClient.
Results are written to _tmp_task43_result.txt.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

results: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    results.append(f"[{status}] {name}" + (f" :: {detail}" if detail else ""))


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="cairn_task43_"))
    db_path = tmpdir / "cairn.db"

    from cairn.server import db, product_db

    db.configure(db_path)
    product_db.configure_product_db()

    # Seed two projects and a mix of vulnerabilities.
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            ("p1", "Alpha Project", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            ("p2", "Beta Project", "2024-01-02T00:00:00Z"),
        )
        seed = [
            ("v1", "p1", "f1", "SQLi in login", "desc", "critical", "2024-01-03T00:00:00Z"),
            ("v2", "p1", "f2", "Reflected XSS", "desc", "high", "2024-01-04T00:00:00Z"),
            ("v3", "p1", "f3", "Info disclosure", "desc", "medium", "2024-01-05T00:00:00Z"),
            ("v4", "p2", "f4", "RCE in upload", "desc", "critical", "2024-01-06T00:00:00Z"),
            ("v5", "p2", "f5", "Missing headers", "desc", "low", "2024-01-07T00:00:00Z"),
        ]
        for row in seed:
            conn.execute(
                "INSERT INTO vulnerabilities "
                "(id, project_id, fact_id, title, description, severity, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cairn.server.routers import vulnerabilities

    app = FastAPI()
    app.include_router(vulnerabilities.router)
    client = TestClient(app)

    # 1. No filters -> all 5, severity-ordered (critical first).
    r = client.get("/api/vulnerabilities")
    body = r.json()
    ids = [v["id"] for v in body]
    check("list no-filter status 200", r.status_code == 200, str(r.status_code))
    check("list no-filter returns all 5", len(body) == 5, str(len(body)))
    check(
        "list includes title/severity/project_name",
        all({"title", "severity", "project_name"} <= set(v) for v in body),
    )
    # critical entries should come before the rest.
    sev_order = [v["severity"] for v in body]
    check(
        "list severity-ordered (critical first)",
        sev_order[0] == "critical" and sev_order[1] == "critical",
        str(sev_order),
    )
    check(
        "project_name resolved via join",
        {v["id"]: v["project_name"] for v in body}.get("v1") == "Alpha Project",
        str({v["id"]: v["project_name"] for v in body}),
    )

    # 2. Severity filter only.
    r = client.get("/api/vulnerabilities", params={"severity": "critical"})
    body = r.json()
    check(
        "filter severity=critical -> v1,v4",
        sorted(v["id"] for v in body) == ["v1", "v4"],
        str([v["id"] for v in body]),
    )

    # 3. Project filter only.
    r = client.get("/api/vulnerabilities", params={"project_id": "p1"})
    body = r.json()
    check(
        "filter project_id=p1 -> v1,v2,v3",
        sorted(v["id"] for v in body) == ["v1", "v2", "v3"],
        str([v["id"] for v in body]),
    )

    # 4. AND logic: severity + project.
    r = client.get(
        "/api/vulnerabilities",
        params={"severity": "critical", "project_id": "p1"},
    )
    body = r.json()
    check(
        "AND filter severity=critical & project=p1 -> v1 only",
        [v["id"] for v in body] == ["v1"],
        str([v["id"] for v in body]),
    )

    # 5. AND logic with empty intersection (high severity in p2 -> none).
    r = client.get(
        "/api/vulnerabilities",
        params={"severity": "high", "project_id": "p2"},
    )
    body = r.json()
    check("AND filter empty intersection -> []", body == [], str(body))

    # 6. Invalid severity -> 422.
    r = client.get("/api/vulnerabilities", params={"severity": "bogus"})
    check("invalid severity -> 422", r.status_code == 422, str(r.status_code))

    # 7. Unknown project_id -> 404.
    r = client.get("/api/vulnerabilities", params={"project_id": "nope"})
    check("unknown project_id -> 404", r.status_code == 404, str(r.status_code))

    # 8. Summary counts.
    r = client.get("/api/vulnerabilities/summary")
    body = r.json()
    expected = {"critical": 2, "high": 1, "medium": 1, "low": 1}
    check("summary status 200", r.status_code == 200, str(r.status_code))
    check("summary counts correct", body == expected, str(body))

    # 9. Summary with zero vulnerabilities -> all zero.
    with db.get_conn() as conn:
        conn.execute("DELETE FROM vulnerabilities")
    r = client.get("/api/vulnerabilities/summary")
    body = r.json()
    check(
        "summary empty -> all zero",
        body == {"critical": 0, "high": 0, "medium": 0, "low": 0},
        str(body),
    )

    out = Path(__file__).with_name("_tmp_task43_result.txt")
    out.write_text("\n".join(results) + "\n")
    passed = sum(1 for line in results if line.startswith("[PASS]"))
    summary = f"\n{passed}/{len(results)} checks passed\n"
    out.write_text("\n".join(results) + summary)
    print("\n".join(results))
    print(summary)


if __name__ == "__main__":
    main()
