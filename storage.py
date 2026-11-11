"""Хранилище на SQLite: состояние анкеты, заявки, настройки.

Файл базы лежит рядом с ботом: bot.db
Резервная копия — просто скопировать этот файл.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


class Storage:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    # ------------------------------------------------------------------ база
    def _init(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    chat_id    INTEGER PRIMARY KEY,
                    step       TEXT,
                    data       TEXT NOT NULL DEFAULT '{}',
                    photos     TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    chat_id    INTEGER,
                    username   TEXT,
                    occasion   TEXT,
                    whom       TEXT,
                    date       TEXT,
                    format     TEXT,
                    price      TEXT,
                    wishes     TEXT,
                    contact    TEXT,
                    photos     TEXT,
                    status     TEXT NOT NULL DEFAULT 'new'
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )

    # -------------------------------------------------------- состояние анкеты
    def get_state(self, chat_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT step, data, photos FROM state WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "step": row["step"],
            "data": json.loads(row["data"] or "{}"),
            "photos": json.loads(row["photos"] or "[]"),
        }

    def set_state(self, chat_id: int, step: str, data: dict, photos: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO state (chat_id, step, data, photos, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    step = excluded.step,
                    data = excluded.data,
                    photos = excluded.photos,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    step,
                    json.dumps(data, ensure_ascii=False),
                    json.dumps(photos),
                    _now(),
                ),
            )

    def clear_state(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM state WHERE chat_id = ?", (chat_id,))

    # ------------------------------------------------------------- заявки
    def save_order(self, chat_id: int, username: str, data: dict, photos: list[str]) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO orders
                    (created_at, chat_id, username, occasion, whom, date,
                     format, price, wishes, contact, photos)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    chat_id,
                    username,
                    data.get("occasion", ""),
                    data.get("whom", ""),
                    data.get("date", ""),
                    data.get("format", ""),
                    data.get("price", ""),
                    data.get("wishes", ""),
                    data.get("contact", ""),
                    json.dumps(photos),
                ),
            )
            return int(cur.lastrowid or 0)

    def count_orders(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()
        return int(row["n"])

    def new_orders(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM orders WHERE status = 'new' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- настройки
    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------- утилиты
    def close(self) -> None:
        with self._lock:
            self._conn.close()
