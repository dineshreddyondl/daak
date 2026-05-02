"""
Database connection helper.

Reads DATABASE_URL from environment (Railway sets this automatically).
Falls back to local .env file for development.

Usage:
    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pincodes_master")
            print(cur.fetchone())
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_database_url() -> str:
    """Read DATABASE_URL from env. Raises clearly if missing."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set.\n"
            "  - On Railway: it's set automatically when you add a Postgres plugin.\n"
            "  - Locally: copy .env.example to .env and paste your Railway connection string."
        )
    # Railway sometimes provides a postgres:// URL; psycopg2 wants postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


@contextmanager
def get_conn(dict_cursor: bool = False) -> Iterator[PgConnection]:
    """Context manager for a Postgres connection.

    Auto-commits on successful exit, rolls back on exception, always closes.

    Args:
        dict_cursor: if True, cursors return dicts instead of tuples
    """
    conn = psycopg2.connect(get_database_url())
    if dict_cursor:
        conn.cursor_factory = RealDictCursor
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> bool:
    """Return True if we can connect and run a trivial query."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
    except Exception as e:
        print(f"[ping] failed: {e}")
        return False


if __name__ == "__main__":
    # Run `python db.py` to test the connection
    if ping():
        print("✅ Database connection OK")
    else:
        print("❌ Database connection failed")
        raise SystemExit(1)
