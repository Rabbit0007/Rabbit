"""Verification for task 7.1: templates schema + built-in template data."""
from __future__ import annotations

import sqlite3

from cairn.server import product_db
from cairn.server.templates_service import BUILTIN_TEMPLATES

lines: list[str] = []

# --- 1. Schema applies cleanly and creates the templates table -------------
conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
# users table is the FK target for templates; create a minimal stand-in so the
# FK reference resolves (sqlite only enforces FK with PRAGMA on, but the table
# must exist for the DDL referencing it to be meaningful).
conn.executescript(
    "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY);"
    "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY);"
)
conn.executescript(product_db.PRODUCT_SCHEMA)
# Idempotency: run again.
conn.executescript(product_db.PRODUCT_SCHEMA)

cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(templates)")}
lines.append(f"templates columns: {sorted(cols)}")
expected_cols = {"id", "user_id", "title", "origin", "goal", "hints_json", "created_at"}
lines.append(f"columns match expected: {set(cols) == expected_cols}")

# hints_json default
hints_default = cols["hints_json"]["dflt_value"]
lines.append(f"hints_json default: {hints_default}")

# index on user_id
idx = conn.execute("PRAGMA index_list(templates)").fetchall()
idx_names = [r["name"] for r in idx]
lines.append(f"indexes: {idx_names}")
has_user_idx = any("user" in n for n in idx_names)
lines.append(f"has user_id index: {has_user_idx}")

# FK to users ON DELETE CASCADE
fks = conn.execute("PRAGMA foreign_key_list(templates)").fetchall()
for fk in fks:
    lines.append(
        f"FK -> table={fk['table']} from={fk['from']} to={fk['to']} on_delete={fk['on_delete']}"
    )

# Insert/read round-trip works
conn.execute("INSERT INTO users (id) VALUES ('u1')")
conn.execute(
    "INSERT INTO templates (id, user_id, title, origin, goal, created_at) "
    "VALUES ('t1','u1','T','O','G','2024-01-01T00:00:00Z')"
)
row = conn.execute("SELECT hints_json FROM templates WHERE id='t1'").fetchone()
lines.append(f"inserted row default hints_json: {row['hints_json']!r}")

# --- 2. Built-in templates -------------------------------------------------
titles = [t["title"] for t in BUILTIN_TEMPLATES]
lines.append(f"builtin count: {len(BUILTIN_TEMPLATES)}")
lines.append(f"builtin titles: {titles}")
required = {
    "Web Application Assessment",
    "Internal Network Pentest",
    "External Network Pentest",
    "CTF Challenge",
}
lines.append(f"has required 4 templates: {required.issubset(set(titles))}")

ok = True
for t in BUILTIN_TEMPLATES:
    keys_ok = {"id", "title", "origin", "goal", "hints", "is_builtin", "user_id"} <= set(t)
    hints = t["hints"]
    hints_count_ok = 1 <= len(hints) <= 10
    hints_shape_ok = all(
        set(h) == {"content", "creator"} and h["creator"] == "template" and h["content"]
        for h in hints
    )
    fields_ok = bool(t["title"]) and bool(t["origin"]) and bool(t["goal"])
    builtin_ok = t["is_builtin"] is True and t["user_id"] is None
    entry_ok = keys_ok and hints_count_ok and hints_shape_ok and fields_ok and builtin_ok
    ok = ok and entry_ok
    lines.append(
        f"  {t['id']}: keys={keys_ok} hints={len(hints)}({hints_count_ok}) "
        f"shape={hints_shape_ok} fields={fields_ok} builtin={builtin_ok}"
    )

# unique ids
ids = [t["id"] for t in BUILTIN_TEMPLATES]
lines.append(f"unique ids: {len(set(ids)) == len(ids)}")
lines.append(f"ALL BUILTIN VALID: {ok}")

with open("_tmp_task71_result.txt", "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
