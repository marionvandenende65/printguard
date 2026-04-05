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
    created_at           TEXT DEFAULT (datetime('now'))
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Maak tabellen aan en voeg demo-account toe als het nog niet bestaat."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        conn.commit()

    _seed_demo()


def _seed_demo():
    """Demo-account voor testen. Wordt overgeslagen als het al bestaat."""
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
