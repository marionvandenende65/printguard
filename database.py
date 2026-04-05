"""
PrintGuard — SQLite database setup
"""

import sqlite3, os
import bcrypt
import datetime

DB_PATH = os.getenv("DATABASE_PATH", "printguard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    email                TEXT UNIQUE NOT NULL,
    password_hash        TEXT NOT NULL,
    name                 TEXT,
    plan                 TEXT DEFAULT 'starter',
    billing              TEXT DEFAULT 'monthly',
    member_since         TEXT NOT NULL,
    uploads_this_period  INTEGER DEFAULT 0,
    period_key           TEXT,
    referral_code        TEXT,
    referred_by          TEXT,
    cancelled            INTEGER DEFAULT 0,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reset_tokens (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS certificates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    cert_id    TEXT NOT NULL UNIQUE,
    title      TEXT,
    file_hash  TEXT,
    zip_blob   BLOB,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS referrals (
    code       TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    uses       INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS demo_usage (
    ip        TEXT PRIMARY KEY,
    last_used TEXT NOT NULL
);
"""

# Migraties voor bestaande databases (kolommen toevoegen zonder data te verliezen)
_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN referral_code TEXT",
    "ALTER TABLE users ADD COLUMN referred_by TEXT",
    "ALTER TABLE users ADD COLUMN cancelled INTEGER DEFAULT 0",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Maak tabellen aan, voer migraties uit, voeg demo-account toe."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        conn.commit()

    _run_migrations()
    _seed_demo()


def _run_migrations():
    with get_db() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass  # kolom bestaat al


def _seed_demo():
    """Demo-account voor testen."""
    demo_email = "demo@printguardtool.com"
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE email = ?", (demo_email,)
        ).fetchone()
        if exists:
            return

        pw_hash = bcrypt.hashpw(b"demo123", bcrypt.gensalt()).decode()
        conn.execute(
            """INSERT INTO users
               (email, password_hash, name, plan, billing, member_since, uploads_this_period, period_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (demo_email, pw_hash, "Demo", "demo", "monthly",
             "2026-03-15", 0, "2026-03-15"),
        )
        conn.commit()
