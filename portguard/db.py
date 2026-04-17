"""
portguard/db.py — SQLAlchemy engine factory for PortGuard.

Resolves DATABASE_URL to a SQLAlchemy Engine.  All database modules
(AuthDB, PatternDB, DashboardAnalytics) call get_engine() to obtain
their connection; this single entry-point makes the PostgreSQL / SQLite
choice in one place.

DATABASE_URL logic
------------------
  postgres://...   or   postgresql://...  →  shared PostgreSQL engine
  (not set)                               →  SQLite at sqlite_path

Render provides "postgres://..." URLs; SQLAlchemy requires "postgresql://".
The factory normalises this automatically.

SQLite pragmas
--------------
WAL mode, foreign keys ON, and synchronous=NORMAL are applied via a
"connect" event listener so every new connection gets them without the
caller needing PRAGMA statements in their DDL.

SQL dialect adapter
-------------------
adapt_stmt(stmt, dialect) converts a single SQL statement for the target
dialect:
  - Skips PRAGMA lines (set via event listener for SQLite; unsupported on PG)
  - Converts INTEGER PRIMARY KEY AUTOINCREMENT  →  BIGSERIAL PRIMARY KEY
  - Converts INSERT OR IGNORE INTO  →  INSERT INTO … ON CONFLICT DO NOTHING
"""

from __future__ import annotations

import logging
import os
import re

from sqlalchemy import create_engine, event, text  # noqa: F401  (text re-exported)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Shared PostgreSQL engine — one pool reused by every caller.
_pg_engine: Engine | None = None


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def _get_pg_url() -> str | None:
    """Return a normalised postgresql:// URL from DATABASE_URL, or None."""
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://"):
        # Render emits the legacy "postgres://" scheme
        url = url.replace("postgres://", "postgresql://", 1)
    return url if url.startswith("postgresql://") else None


def get_engine(sqlite_path: str) -> tuple[Engine, str]:
    """Return ``(engine, dialect_name)`` for the current environment.

    Parameters
    ----------
    sqlite_path:
        Path to the SQLite database file.  Ignored when DATABASE_URL is set
        to a PostgreSQL URL (both auth and pattern data share one PG database).

    Returns
    -------
    (engine, dialect_name)
        *dialect_name* is ``"postgresql"`` or ``"sqlite"``.
    """
    global _pg_engine

    pg_url = _get_pg_url()
    if pg_url:
        if _pg_engine is None:
            _pg_engine = create_engine(
                pg_url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
            )
            logger.info("Database engine: PostgreSQL (shared pool)")
        return _pg_engine, "postgresql"

    # ---- SQLite fallback ----
    engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    logger.info("Database engine: SQLite at %s", sqlite_path)
    return engine, "sqlite"


# ---------------------------------------------------------------------------
# SQL dialect adapter
# ---------------------------------------------------------------------------


def adapt_stmt(stmt: str, dialect: str) -> str:
    """Adapt a single SQL statement for *dialect*.

    Parameters
    ----------
    stmt:
        One SQL statement (no trailing semicolon required).
    dialect:
        ``"sqlite"`` (pass-through) or ``"postgresql"`` (apply adaptations).

    Returns
    -------
    str
        Adapted statement, or ``""`` if the statement should be skipped.
    """
    # Determine the effective SQL keyword by stripping single-line comments.
    effective = re.sub(r"--[^\n]*", "", stmt).strip()
    if not effective:
        return ""

    # Always skip PRAGMA — handled by the connect event for SQLite;
    # not valid SQL in PostgreSQL.
    if effective.upper().startswith("PRAGMA"):
        return ""

    if dialect == "sqlite":
        return stmt

    # ---- PostgreSQL adaptations ----

    # AUTOINCREMENT → BIGSERIAL  (INTEGER PRIMARY KEY AUTOINCREMENT is SQLite-only)
    stmt = stmt.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        "BIGSERIAL PRIMARY KEY",
    )

    # INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", stmt, re.IGNORECASE):
        stmt = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\b",
            "INSERT",
            stmt,
            flags=re.IGNORECASE,
        )
        stmt = stmt.rstrip() + "\nON CONFLICT DO NOTHING"

    return stmt


def split_migration_sql(sql: str, dialect: str) -> list[str]:
    """Split a multi-statement SQL string and adapt each statement.

    Statements are separated by semicolons.  Comment-only and empty
    segments are silently dropped.  Each remaining statement is passed
    through :func:`adapt_stmt` before being returned.

    Parameters
    ----------
    sql:
        Raw SQL string that may contain multiple ``CREATE TABLE``,
        ``CREATE INDEX``, ``ALTER TABLE``, or DML statements separated
        by ``;``.
    dialect:
        ``"sqlite"`` or ``"postgresql"``.

    Returns
    -------
    list[str]
        Non-empty, dialect-adapted SQL statements ready to execute.
    """
    result: list[str] = []
    for raw in sql.split(";"):
        stripped = raw.strip()
        if not stripped:
            continue
        # Skip segments that contain only comments (no real SQL)
        if not re.sub(r"--[^\n]*", "", stripped).strip():
            continue
        adapted = adapt_stmt(stripped, dialect)
        if adapted:
            result.append(adapted)
    return result
