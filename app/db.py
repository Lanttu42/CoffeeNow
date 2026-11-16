"""SQLite-tietokanta. Ei henkilötietoja: vain keittimet, anonyymit laite-ID:t ja push-tilaukset."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("COFFEENOW_DB", "coffeenow.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'unknown',
    cups        INTEGER,
    updated_at  REAL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wishes (
    machine_id  TEXT NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (machine_id, device_id)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    endpoint    TEXT NOT NULL,
    machine_id  TEXT NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (endpoint, machine_id)
);
"""


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()
