"""Immutable test imports and durable version-diagnosis records."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .store import now
from .version_metrics import stable_key


SUMMARY_FIELDS = ("id", "title", "protocol", "fio", "window", "metrics")


def snapshot_summary(snapshot):
    """Navigation metadata never contains per-thread or per-CPU observations."""
    return {key: snapshot[key] for key in SUMMARY_FIELDS}


class VersionStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(directory / "version_diagnosis.sqlite3", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS version_batches (
                id TEXT PRIMARY KEY, environment_key TEXT NOT NULL,
                tested_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_version_batches_environment
                ON version_batches(environment_key, tested_at);
            CREATE TABLE IF NOT EXISTS version_batch_catalog (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS version_scenario_catalog (
                batch_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
                source TEXT NOT NULL, environment_id TEXT NOT NULL,
                tested_at REAL NOT NULL, window_started_at REAL NOT NULL,
                fio_key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(batch_id, scenario_id)
            );
            CREATE INDEX IF NOT EXISTS idx_version_scenario_pair
                ON version_scenario_catalog(source, environment_id, fio_key,
                                            tested_at DESC, window_started_at DESC,
                                            batch_id DESC, scenario_id DESC);
            CREATE TABLE IF NOT EXISTS version_comparisons (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_version_comparison_lookup
                ON version_comparisons(batch_id, scenario_id, created_at);
            CREATE TABLE IF NOT EXISTS version_events (
                id TEXT PRIMARY KEY, comparison_id TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS version_feedback (
                id TEXT PRIMARY KEY, comparison_id TEXT NOT NULL,
                suggestion_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        self.connection.commit()
        self._backfill_catalog()

    def _catalog_batch(self, batch):
        metadata = {key: value for key, value in batch.items() if key != "scenarios"}
        self.connection.execute("INSERT INTO version_batch_catalog VALUES(?,?)",
                                (batch["id"], json.dumps(metadata, ensure_ascii=False)))
        for sample in batch["scenarios"]:
            self.connection.execute("INSERT INTO version_scenario_catalog VALUES(?,?,?,?,?,?,?,?)",
                                    (batch["id"], sample["id"], batch["source"], batch["environment"]["id"],
                                     datetime.fromisoformat(batch["created_at"]).timestamp(),
                                     datetime.fromisoformat(sample["window"]["started_at"]).timestamp(),
                                     stable_key({"protocol": sample["protocol"], "fio": sample["fio"]}),
                                     json.dumps(snapshot_summary(sample), ensure_ascii=False)))

    def _backfill_catalog(self):
        """Build missing projections once; original evidence remains untouched."""
        with self.lock, self.connection:
            rows = self.connection.execute("""
                SELECT b.payload FROM version_batches b
                LEFT JOIN version_batch_catalog c ON c.id=b.id WHERE c.id IS NULL
            """).fetchall()
            for row in rows:
                self._catalog_batch(json.loads(row["payload"]))

    def import_batch(self, batch: dict):
        with self.lock, self.connection:
            if self.connection.execute("SELECT 1 FROM version_batches WHERE id=?", (batch["id"],)).fetchone():
                raise ValueError("测试批次 ID 已存在；历史批次不能覆盖。")
            for existing in self.catalog():
                if existing["environment"]["id"] == batch["environment"]["id"] and existing["environment"] != batch["environment"]:
                    raise ValueError("同一环境 ID 的档案已变化；请使用新的 environment.id，避免混合环境。")
            self.connection.execute("INSERT INTO version_batches VALUES(?,?,?,?)",
                                    (batch["id"], batch["source"] + ":" + batch["environment"]["id"], batch["created_at"], json.dumps(batch, ensure_ascii=False)))
            self._catalog_batch(batch)
        return batch

    def catalog(self):
        with self.lock:
            batches = [json.loads(row["payload"]) for row in self.connection.execute("""
                SELECT c.payload FROM version_batch_catalog c
                JOIN version_batches b ON b.id=c.id ORDER BY b.tested_at DESC,b.id DESC
            """).fetchall()]
            by_id = {batch["id"]: batch for batch in batches}
            for batch in batches:
                batch["scenarios"] = []
            # rowid preserves the supplied scenario order, including legacy data.
            for row in self.connection.execute("SELECT batch_id,payload FROM version_scenario_catalog ORDER BY rowid").fetchall():
                by_id[row["batch_id"]]["scenarios"].append(json.loads(row["payload"]))
            return batches

    def get_metadata(self, batch_id):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM version_batch_catalog WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise KeyError(batch_id)
            return json.loads(row["payload"])

    def scenario_summaries(self, batch_id):
        with self.lock:
            self.get_metadata(batch_id)
            return [json.loads(row["payload"]) for row in self.connection.execute(
                "SELECT payload FROM version_scenario_catalog WHERE batch_id=? ORDER BY rowid", (batch_id,)).fetchall()]

    def pair_metadata(self, batch_id, scenario_id):
        """Find the latest comparable prior model without parsing raw captures."""
        with self.lock:
            batch = self.get_metadata(batch_id)
            row = self.connection.execute("SELECT * FROM version_scenario_catalog WHERE batch_id=? AND scenario_id=?", (batch_id, scenario_id)).fetchone()
            if not row:
                raise KeyError(scenario_id)
            current = json.loads(row["payload"])
            previous_row = self.connection.execute("""
                SELECT batch_id,payload FROM version_scenario_catalog
                WHERE source=? AND environment_id=? AND fio_key=? AND tested_at<?
                ORDER BY tested_at DESC,window_started_at DESC,batch_id DESC,scenario_id DESC LIMIT 1
            """, (row["source"], row["environment_id"], row["fio_key"], row["tested_at"])).fetchone()
            previous_batch = self.get_metadata(previous_row["batch_id"]) if previous_row else None
            previous = json.loads(previous_row["payload"]) if previous_row else None
            return batch, current, previous_batch, previous

    def get_snapshot(self, batch_id, scenario_id):
        with self.lock:
            row = self.connection.execute("""
                SELECT sample.value AS payload FROM version_batches b,
                json_each(b.payload,'$.scenarios') sample
                WHERE b.id=? AND json_extract(sample.value,'$.id')=?
            """, (batch_id, scenario_id)).fetchone()
            if not row:
                raise KeyError(scenario_id)
            return json.loads(row["payload"])

    def list_batches(self):
        with self.lock:
            return [json.loads(row["payload"]) for row in self.connection.execute("SELECT payload FROM version_batches ORDER BY tested_at DESC,id DESC").fetchall()]

    def get_batch(self, batch_id):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM version_batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise KeyError(batch_id)
            return json.loads(row["payload"])

    def save_comparison(self, comparison):
        projection = {key: value for key, value in comparison.items() if key != "events"}
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO version_comparisons VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                    (comparison["id"], comparison["current_batch"]["id"], comparison["current"]["id"], now(), json.dumps(projection, ensure_ascii=False)))

    def get_comparison(self, comparison_id, include_samples=True):
        with self.lock:
            projection = "payload" if include_samples else "json_remove(payload,'$.current.sample_windows','$.current.cpu_inventory','$.previous.sample_windows','$.previous.cpu_inventory')"
            row = self.connection.execute(f"SELECT {projection} AS payload FROM version_comparisons WHERE id=?", (comparison_id,)).fetchone()
            if not row:
                raise KeyError(comparison_id)
            comparison = json.loads(row["payload"])
            if comparison.get("analysis"):
                comparison["analysis"]["events"] = [json.loads(event["payload"]) for event in self.connection.execute("SELECT payload FROM version_events WHERE comparison_id=? ORDER BY rowid", (comparison_id,)).fetchall()]
            return comparison

    def latest_comparison(self, batch_id, scenario_id):
        with self.lock:
            row = self.connection.execute("SELECT id FROM version_comparisons WHERE batch_id=? AND scenario_id=? ORDER BY rowid DESC LIMIT 1", (batch_id, scenario_id)).fetchone()
            return self.get_comparison(row["id"]) if row else None

    def comparisons(self, batch_id=None):
        with self.lock:
            rows = self.connection.execute("SELECT id FROM version_comparisons WHERE batch_id=? ORDER BY rowid DESC", (batch_id,)).fetchall() if batch_id else self.connection.execute("SELECT id FROM version_comparisons ORDER BY rowid DESC").fetchall()
            return [self.get_comparison(row["id"]) for row in rows]

    def event(self, comparison_id, stage_id, title, detail, source, data=None):
        event = {"id": "VE-" + uuid4().hex, "comparison_id": comparison_id, "stage_id": stage_id,
                 "title": title, "detail": detail, "source": source, "timestamp": now(), "data": data}
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO version_events VALUES(?,?,?,?)", (event["id"], comparison_id, event["timestamp"], json.dumps(event, ensure_ascii=False)))
        return event

    def append_feedback(self, comparison_id, suggestion_id, review):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO version_feedback VALUES(?,?,?,?,?)",
                                    (uuid4().hex, comparison_id, suggestion_id, review["timestamp"], json.dumps(review, ensure_ascii=False)))

    def close(self):
        self.connection.close()
