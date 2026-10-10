#!/usr/bin/env python3
"""
Cairn_Y Migration Script: Settings Field Renaming
==================================================
This script migrates the old settings field names to the new Cairn_Y terminology:
- intent_timeout -> step_timeout
- reason_timeout -> decide_timeout

Usage:
    python scripts/migrate_settings_fields.py [db_path]

If db_path is not provided, uses the default location.
"""

import sqlite3
import sys
from pathlib import Path


def migrate_settings(db_path: str):
    """Migrate settings fields from old names to new Cairn_Y names."""
    print(f"🔄 Migrating settings in database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Check if new columns exist
    cursor.execute("PRAGMA table_info(settings)")
    columns = {row["name"] for row in cursor.fetchall()}

    has_step_timeout = "step_timeout" in columns
    has_decide_timeout = "decide_timeout" in columns
    has_intent_timeout = "intent_timeout" in columns
    has_reason_timeout = "reason_timeout" in columns

    print(f"  Current columns: {', '.join(sorted(columns))}")

    # Add new columns if they don't exist
    if not has_step_timeout:
        print("  ➕ Adding step_timeout column...")
        cursor.execute("ALTER TABLE settings ADD COLUMN step_timeout INTEGER NOT NULL DEFAULT 15")

    if not has_decide_timeout:
        print("  ➕ Adding decide_timeout column...")
        cursor.execute("ALTER TABLE settings ADD COLUMN decide_timeout INTEGER NOT NULL DEFAULT 15")

    # Copy values from old columns to new columns if old columns exist
    if has_intent_timeout and has_step_timeout:
        print("  📋 Copying intent_timeout -> step_timeout...")
        cursor.execute("UPDATE settings SET step_timeout = intent_timeout")
        migrated = cursor.rowcount
        print(f"     Migrated {migrated} row(s)")

    if has_reason_timeout and has_decide_timeout:
        print("  📋 Copying reason_timeout -> decide_timeout...")
        cursor.execute("UPDATE settings SET decide_timeout = reason_timeout")
        migrated = cursor.rowcount
        print(f"     Migrated {migrated} row(s)")

    conn.commit()

    # Verify migration
    cursor.execute("SELECT step_timeout, decide_timeout FROM settings WHERE rowid = 1")
    row = cursor.fetchone()
    if row:
        print(f"  ✅ Verified: step_timeout={row[0]}, decide_timeout={row[1]}")

    conn.close()
    print("✅ Migration completed successfully!")
    print("\n⚠️  Note: Old columns (intent_timeout, reason_timeout) are kept for backward compatibility.")
    print("   They can be removed in a future version once all clients are updated.")


def main():
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Default database location
        default_db = Path.home() / ".cairn" / "cairn.db"
        db_path = str(default_db)

    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        print(f"   Please specify the correct database path.")
        sys.exit(1)

    try:
        migrate_settings(db_path)
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
