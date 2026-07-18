from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from app.models import QueueRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_scan (
    date TEXT NOT NULL,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, device_id)
);
CREATE TABLE IF NOT EXISTS chat_queue (
    date TEXT NOT NULL,
    device_id TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date, device_id, chat_jid)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str):
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def has_scan(self, date: str, device: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM daily_scan WHERE date=? AND device_id=?", (date, device)
        )
        return cur.fetchone() is not None

    def mark_scan(self, date: str, device: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO daily_scan(date, device_id, status, created_at)"
            " VALUES (?, ?, 'done', ?)", (date, device, _now()))
        self.conn.commit()

    def enqueue(self, date: str, device: str, chat_jid: str, name: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO chat_queue"
            "(date, device_id, chat_jid, name, status, attempts, updated_at)"
            " VALUES (?, ?, ?, ?, 'pending', 0, ?)",
            (date, device, chat_jid, name, _now()))
        self.conn.commit()

    def next_batch(self, date: str, max_attempts: int, limit: int = 50) -> list[QueueRow]:
        cur = self.conn.execute(
            "SELECT date, device_id, chat_jid, name, status, attempts FROM chat_queue"
            " WHERE date=? AND (status='pending' OR (status='failed' AND attempts < ?))"
            " ORDER BY updated_at LIMIT ?", (date, max_attempts, limit))
        return [QueueRow(r["date"], r["device_id"], r["chat_jid"], r["name"],
                         r["status"], r["attempts"]) for r in cur.fetchall()]

    def mark_done(self, date: str, device: str, chat_jid: str) -> None:
        self.conn.execute(
            "UPDATE chat_queue SET status='done', updated_at=?"
            " WHERE date=? AND device_id=? AND chat_jid=?",
            (_now(), date, device, chat_jid))
        self.conn.commit()

    def mark_failed(self, date: str, device: str, chat_jid: str,
                    error: str, max_attempts: int) -> str:
        cur = self.conn.execute(
            "SELECT attempts FROM chat_queue WHERE date=? AND device_id=? AND chat_jid=?",
            (date, device, chat_jid))
        row = cur.fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        status = "dead" if attempts >= max_attempts else "failed"
        self.conn.execute(
            "UPDATE chat_queue SET status=?, attempts=?, last_error=?, updated_at=?"
            " WHERE date=? AND device_id=? AND chat_jid=?",
            (status, attempts, error[:1000], _now(), date, device, chat_jid))
        self.conn.commit()
        return status
