import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

DEFAULT_DB_PATH = "booking_watcher.db"


class Watch(BaseModel):
    id: int
    chat_id: int
    mode: str  # "train", "bus", "both"
    source: str
    destination: str
    travel_date: str  # YYYY-MM-DD
    filters: dict[str, Any] = Field(default_factory=dict)
    poll_interval_sec: int = 600
    is_active: bool = True
    last_checked_at: Optional[str] = None
    last_alerted_at: Optional[str] = None
    created_at: str


class Snapshot(BaseModel):
    id: Optional[int] = None
    watch_id: int
    item_id: str  # e.g. TRAIN_12635_3A_GN or BUS_KPN_2100_0
    status_type: str  # AVAILABLE, RAC, WAITLIST, REGRET, NOT_AVAILABLE
    status_detail: str  # AVAILABLE 12, PQWL12/WL7
    available_seats: Optional[int] = None
    waitlist_number: Optional[int] = None
    fare: Optional[float] = None
    booking_url: Optional[str] = None
    updated_at: str
    raw_payload: Optional[str] = None


class Database:
    """
    SQLite wrapper with WAL mode for concurrency and clean snapshot tracking.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    filters_json TEXT DEFAULT '{}',
                    poll_interval_sec INTEGER DEFAULT 600,
                    is_active INTEGER DEFAULT 1,
                    last_checked_at TEXT,
                    last_alerted_at TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    status_type TEXT NOT NULL,
                    status_detail TEXT NOT NULL,
                    available_seats INTEGER,
                    waitlist_number INTEGER,
                    fare REAL,
                    booking_url TEXT,
                    updated_at TEXT DEFAULT (datetime('now')),
                    raw_payload TEXT,
                    UNIQUE(watch_id, item_id)
                );

                CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(is_active);
                CREATE INDEX IF NOT EXISTS idx_watches_chat_id ON watches(chat_id);
                CREATE INDEX IF NOT EXISTS idx_snapshots_watch_item ON snapshots(watch_id, item_id);
                """
            )

    def create_watch(
        self,
        chat_id: int,
        source: str,
        destination: str,
        travel_date: str,
        mode: str = "both",
        filters: Optional[dict[str, Any]] = None,
        poll_interval_sec: int = 600,
    ) -> Watch:
        filters = filters or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        filters_json = json.dumps(filters)

        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO watches (chat_id, mode, source, destination, travel_date, filters_json, poll_interval_sec, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (chat_id, mode.lower(), source, destination, travel_date, filters_json, poll_interval_sec, now_iso),
            )
            watch_id = cur.lastrowid

        return self.get_watch(watch_id)  # type: ignore

    def get_watch(self, watch_id: int) -> Optional[Watch]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
            if not row:
                return None
            return self._row_to_watch(row)

    def get_active_watches(self) -> list[Watch]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM watches WHERE is_active = 1 ORDER BY id ASC").fetchall()
            return [self._row_to_watch(r) for r in rows]

    def get_user_watches(self, chat_id: int, only_active: bool = False) -> list[Watch]:
        query = "SELECT * FROM watches WHERE chat_id = ?"
        params = [chat_id]
        if only_active:
            query += " AND is_active = 1"
        query += " ORDER BY id DESC"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_watch(r) for r in rows]

    def deactivate_watch(self, watch_id: int, chat_id: Optional[int] = None) -> bool:
        query = "UPDATE watches SET is_active = 0 WHERE id = ?"
        params: list[Any] = [watch_id]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)

        with self.get_connection() as conn:
            cur = conn.execute(query, params)
            return cur.rowcount > 0

    def delete_watch(self, watch_id: int, chat_id: Optional[int] = None) -> bool:
        query = "DELETE FROM watches WHERE id = ?"
        params: list[Any] = [watch_id]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)

        with self.get_connection() as conn:
            cur = conn.execute(query, params)
            return cur.rowcount > 0

    def update_watch_checked_time(self, watch_id: int):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute("UPDATE watches SET last_checked_at = ? WHERE id = ?", (now_iso, watch_id))

    def update_watch_alerted_time(self, watch_id: int):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute("UPDATE watches SET last_alerted_at = ? WHERE id = ?", (now_iso, watch_id))

    def get_latest_snapshots(self, watch_id: int) -> dict[str, Snapshot]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM snapshots WHERE watch_id = ?", (watch_id,)).fetchall()
            return {
                r["item_id"]: Snapshot(
                    id=r["id"],
                    watch_id=r["watch_id"],
                    item_id=r["item_id"],
                    status_type=r["status_type"],
                    status_detail=r["status_detail"],
                    available_seats=r["available_seats"],
                    waitlist_number=r["waitlist_number"],
                    fare=r["fare"],
                    booking_url=r["booking_url"],
                    updated_at=r["updated_at"],
                    raw_payload=r["raw_payload"],
                )
                for r in rows
            }

    def upsert_snapshots(self, watch_id: int, snapshots: list[Snapshot]):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            for s in snapshots:
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        watch_id, item_id, status_type, status_detail,
                        available_seats, waitlist_number, fare, booking_url,
                        updated_at, raw_payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(watch_id, item_id) DO UPDATE SET
                        status_type = excluded.status_type,
                        status_detail = excluded.status_detail,
                        available_seats = excluded.available_seats,
                        waitlist_number = excluded.waitlist_number,
                        fare = excluded.fare,
                        booking_url = excluded.booking_url,
                        updated_at = excluded.updated_at,
                        raw_payload = excluded.raw_payload
                    """,
                    (
                        watch_id,
                        s.item_id,
                        s.status_type,
                        s.status_detail,
                        s.available_seats,
                        s.waitlist_number,
                        s.fare,
                        s.booking_url,
                        now_iso,
                        s.raw_payload,
                    ),
                )

    @staticmethod
    def _row_to_watch(row: sqlite3.Row) -> Watch:
        filters = {}
        if row["filters_json"]:
            try:
                filters = json.loads(row["filters_json"])
            except Exception:
                filters = {}

        return Watch(
            id=row["id"],
            chat_id=row["chat_id"],
            mode=row["mode"],
            source=row["source"],
            destination=row["destination"],
            travel_date=row["travel_date"],
            filters=filters,
            poll_interval_sec=row["poll_interval_sec"],
            is_active=bool(row["is_active"]),
            last_checked_at=row["last_checked_at"],
            last_alerted_at=row["last_alerted_at"],
            created_at=row["created_at"],
        )
