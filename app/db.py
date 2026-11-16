"""SQLite-tietokanta. Minimaalisesti tietoa: ei sähköposteja, ei oikeita nimiä."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("COFFEENOW_DB", "coffeenow.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,            -- vain hash, ei itse tokenia
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orgs (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    invite_code  TEXT NOT NULL UNIQUE,
    founder_id   INTEGER NOT NULL REFERENCES users(id),   -- ei CASCADEa: perustajaa ei voi poistaa vahingossa
    auto_manager INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id      TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_manager  INTEGER NOT NULL DEFAULT 0,
    joined_at   REAL NOT NULL,
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS machines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'unknown',
    cups        INTEGER,
    updated_at  REAL,
    created_at  REAL NOT NULL,
    org_id      TEXT REFERENCES orgs(id) ON DELETE CASCADE,  -- NULL = julkinen keitin
    creator_user_id     INTEGER,   -- luoja (jos kirjautunut): vain luoja voi liittää julkisen keittimen organisaatioon
    creator_device_hash TEXT       -- luojan laite-ID:n hash (sama tarkoitus kirjautumattomalle)
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
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- pakollinen yksityisille keittimille
    PRIMARY KEY (endpoint, machine_id)
);
CREATE INDEX IF NOT EXISTS idx_machines_org ON machines(org_id);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
"""

# Vanhan (v1) kannan päivitys: lisätään puuttuvat sarakkeet
MIGRATIONS = [
    ("machines", "org_id", "ALTER TABLE machines ADD COLUMN org_id TEXT REFERENCES orgs(id) ON DELETE CASCADE"),
    ("machines", "creator_user_id", "ALTER TABLE machines ADD COLUMN creator_user_id INTEGER"),
    ("machines", "creator_device_hash", "ALTER TABLE machines ADD COLUMN creator_device_hash TEXT"),
    ("subscriptions", "user_id", "ALTER TABLE subscriptions ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"),
]


def init() -> None:
    with connect() as con:
        for table, column, sql in MIGRATIONS:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    con.execute(sql)
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
