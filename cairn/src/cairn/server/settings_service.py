from __future__ import annotations

import sqlite3
from datetime import timedelta

from cairn.server.db import SETTINGS_DEFAULTS
from cairn.server.models import Settings

_SETTINGS_COLUMNS = tuple(SETTINGS_DEFAULTS.keys())
_SETTINGS_SELECT = ", ".join(_SETTINGS_COLUMNS)


def load_settings(conn: sqlite3.Connection) -> Settings:
    row = conn.execute(
        f"SELECT {_SETTINGS_SELECT} FROM settings WHERE rowid = 1"
    ).fetchone()
    data = dict(SETTINGS_DEFAULTS)
    if row is not None:
        for key in _SETTINGS_COLUMNS:
            if key in row.keys():
                data[key] = int(row[key])
    return Settings(**data)


def session_duration(conn: sqlite3.Connection) -> timedelta:
    return timedelta(hours=load_settings(conn).session_duration_hours)

