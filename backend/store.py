"""Durable case projections and append-only audit events, separate from checkpoints."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CaseStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(directory / "cases.sqlite3", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, run_id TEXT,
                stage_id TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_case ON runs(case_id);
        """)
        # Upgrade an earlier demo database without discarding current run data.
        for row in self.connection.execute("SELECT id,payload,updated_at FROM cases").fetchall():
            projection = json.loads(row["payload"])
            if projection.get("run_id"):
                self.connection.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?)",
                                        (projection["run_id"], row["id"], row["payload"], row["updated_at"]))
        self.connection.commit()

    def save(self, case: dict) -> None:
        # Events are append-only rows, never rewritten by checkpoint replay.
        projection = {key: value for key, value in case.items() if key != "events"}
        projection["updated_at"] = now()
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO cases VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (case["id"], json.dumps(projection, ensure_ascii=False), projection["updated_at"]),
            )
            if projection.get("run_id"):
                self.connection.execute(
                    "INSERT INTO runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                    (projection["run_id"], case["id"], json.dumps(projection, ensure_ascii=False), projection["updated_at"]),
                )

    def save_historical_run(self, case):
        """Record historical expert review without overwriting the active case."""
        projection = {key: value for key, value in case.items() if key != "events"}
        projection["updated_at"] = now()
        with self.lock, self.connection:
            self.connection.execute("UPDATE runs SET payload=?,updated_at=? WHERE id=? AND case_id=?",
                                    (json.dumps(projection, ensure_ascii=False), now(), case["run_id"], case["id"]))

    def get_run(self, case_id, run_id):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM runs WHERE id=? AND case_id=?", (run_id, case_id)).fetchone()
            if not row:
                raise KeyError(run_id)
            case = json.loads(row["payload"])
            case["events"] = [json.loads(item["payload"]) for item in self.connection.execute(
                "SELECT payload FROM events WHERE case_id=? AND run_id=? ORDER BY rowid", (case_id, run_id)
            ).fetchall()]
            return case

    def list_runs(self, case_id):
        self.get(case_id)
        with self.lock:
            rows = self.connection.execute("SELECT id FROM runs WHERE case_id=? ORDER BY rowid DESC", (case_id,)).fetchall()
            return [self.get_run(case_id, row["id"]) for row in rows]

    def all_runs(self):
        with self.lock:
            rows = self.connection.execute("SELECT case_id,id FROM runs ORDER BY rowid DESC").fetchall()
            return [self.get_run(row["case_id"], row["id"]) for row in rows]

    def get(self, case_id: str) -> dict:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise KeyError(case_id)
            case = json.loads(row["payload"])
            case["events"] = [json.loads(item["payload"]) for item in self.connection.execute(
                "SELECT payload FROM events WHERE case_id=? ORDER BY rowid", (case_id,)
            ).fetchall()]
            return case

    def list(self) -> list[dict]:
        with self.lock:
            ids = self.connection.execute("SELECT id FROM cases ORDER BY updated_at DESC, rowid DESC").fetchall()
            return [self.get(row["id"]) for row in ids]

    def event(self, case: dict, stage_id: str, title: str, detail: str, *, kind="stage", source="system", dedupe_key=None, data=None) -> dict:
        identifier = uuid4().hex
        payload = {
            "id": identifier, "run_id": case.get("run_id"), "stage_id": stage_id,
            "title": title, "detail": detail, "timestamp": now(), "kind": kind, "source": source,
            "data": data,
        }
        with self.lock, self.connection:
            key = dedupe_key or identifier
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
                (identifier, case["id"], case.get("run_id"), stage_id,
                 key, json.dumps(payload, ensure_ascii=False)),
            )
            if cursor.rowcount == 0:
                return json.loads(self.connection.execute("SELECT payload FROM events WHERE dedupe_key=?", (key,)).fetchone()["payload"])
        return payload

    def close(self):
        self.connection.close()
