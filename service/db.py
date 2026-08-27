from __future__ import annotations

"""
SQLite persistence layer.

This module owns:
- schema initialization (tickets + ticket_events)
- minimal helper functions for inserting tickets and events
- updating tickets after processing
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """Return an ISO-8601 timestamp in UTC."""

    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DB:
    """SQLite DB handle that produces connections with Row factory enabled."""

    path: Path

    def connect(self) -> sqlite3.Connection:
        """Create a SQLite connection usable across threads."""

        con = sqlite3.connect(str(self.path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con


def init_db(db: DB) -> None:
    """Create tables and indexes if they do not exist."""

    db.path.parent.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
              ticket_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              channel TEXT NOT NULL,
              submitter TEXT,
              title TEXT NOT NULL,
              description TEXT NOT NULL,
              topic TEXT,
              topic_confidence REAL,
              risk_level TEXT,
              route_action TEXT,
              status TEXT NOT NULL,
              receipt TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              ticket_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              level TEXT NOT NULL,
              module TEXT NOT NULL,
              message TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket_id_ts
            ON ticket_events(ticket_id, ts)
            """
        )
        con.commit()
    finally:
        con.close()


def insert_event(con: sqlite3.Connection, ticket_id: str, level: str, module: str, message: str) -> None:
    """Insert a ticket event row (used for SSE and audit trail)."""

    con.execute(
        "INSERT INTO ticket_events(ticket_id, ts, level, module, message) VALUES(?,?,?,?,?)",
        (ticket_id, now_iso(), level, module, message),
    )


def upsert_ticket(
    con: sqlite3.Connection,
    ticket_id: str,
    created_at: str,
    channel: str,
    submitter: str | None,
    title: str,
    description: str,
    status: str,
) -> None:
    """Insert a new ticket or update an existing one with the same id."""

    now = now_iso()
    con.execute(
        """
        INSERT INTO tickets(ticket_id, created_at, channel, submitter, title, description, status, updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(ticket_id) DO UPDATE SET
          channel=excluded.channel,
          submitter=excluded.submitter,
          title=excluded.title,
          description=excluded.description,
          status=excluded.status,
          updated_at=excluded.updated_at
        """,
        (ticket_id, created_at, channel, submitter, title, description, status, now),
    )


def update_ticket_processing(
    con: sqlite3.Connection,
    ticket_id: str,
    topic: str,
    confidence: float,
    risk_level: str,
    route_action: str,
    status: str,
    receipt: str | None,
) -> None:
    """Update processing fields after classification/routing."""

    now = now_iso()
    con.execute(
        """
        UPDATE tickets
        SET topic=?, topic_confidence=?, risk_level=?, route_action=?, status=?, receipt=?, updated_at=?
        WHERE ticket_id=?
        """,
        (topic, confidence, risk_level, route_action, status, receipt, now, ticket_id),
    )
