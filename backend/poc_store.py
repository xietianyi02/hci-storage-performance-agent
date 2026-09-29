"""POC-only persistence; trials and analysis snapshots are append-only."""

import json
from pathlib import Path
import sqlite3
from threading import RLock


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class PocStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(directory / "poc.sqlite3", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS poc_parameters (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS poc_campaigns (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS poc_trials (id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS poc_trials_campaign ON poc_trials(campaign_id);
            CREATE TABLE IF NOT EXISTS poc_analyses (id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,trial_count INTEGER NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS poc_feedback (analysis_id TEXT NOT NULL,payload TEXT NOT NULL);
        """)
        self.connection.commit()

    def parameters(self):
        with self.lock:
            return [json.loads(row["payload"]) for row in self.connection.execute("SELECT payload FROM poc_parameters ORDER BY id")]

    def register_parameter(self, definition):
        with self.lock, self.connection:
            existing = self.connection.execute("SELECT payload FROM poc_parameters WHERE id=?", (definition["id"],)).fetchone()
            if existing:
                if json.loads(existing["payload"]) == definition:
                    return definition
                raise ValueError("参数 ID 已存在；使用新的参数 ID 保留历史定义及实验口径。")
            self.connection.execute("INSERT INTO poc_parameters VALUES(?,?)", (definition["id"], encode(definition)))
        return definition

    def campaigns(self):
        with self.lock:
            return [json.loads(row["payload"]) for row in self.connection.execute("SELECT payload FROM poc_campaigns ORDER BY rowid DESC")]

    def campaign(self, identifier):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM poc_campaigns WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            return json.loads(row["payload"])

    def create_campaign(self, payload):
        with self.lock, self.connection:
            if self.connection.execute("SELECT 1 FROM poc_campaigns WHERE id=?", (payload["id"],)).fetchone():
                raise ValueError("POC 实验 ID 已存在；固定环境与基线不可覆盖。")
            self.connection.execute("INSERT INTO poc_campaigns VALUES(?,?)", (payload["id"], encode(payload)))

    def trials(self, campaign_id):
        with self.lock:
            return [json.loads(row["payload"]) for row in self.connection.execute("SELECT payload FROM poc_trials WHERE campaign_id=? ORDER BY rowid", (campaign_id,))]

    def add_trial(self, campaign_id, payload):
        with self.lock, self.connection:
            if self.connection.execute("SELECT 1 FROM poc_trials WHERE id=?", (payload["id"],)).fetchone():
                raise ValueError("POC 测试轮次 ID 已存在；原始观测不可覆盖。")
            self.connection.execute("INSERT INTO poc_trials VALUES(?,?,?)", (payload["id"], campaign_id, encode(payload)))

    def analyses(self, campaign_id):
        with self.lock:
            records = []
            for row in self.connection.execute("SELECT payload FROM poc_analyses WHERE campaign_id=? ORDER BY rowid", (campaign_id,)):
                analysis = json.loads(row["payload"])
                history = [json.loads(review["payload"]) for review in self.connection.execute("SELECT payload FROM poc_feedback WHERE analysis_id=? ORDER BY rowid", (analysis["id"],))]
                if analysis["recommendation"] is not None:
                    analysis["recommendation"]["review_history"] = history
                    if history:
                        analysis["recommendation"].update(verdict=history[-1]["verdict"], note=history[-1]["note"])
                records.append(analysis)
            return records

    def add_analysis(self, campaign_id, trial_count, analysis):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO poc_analyses VALUES(?,?,?,?)", (analysis["id"], campaign_id, trial_count, encode(analysis)))

    def feedback(self, analysis_id, payload):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO poc_feedback VALUES(?,?)", (analysis_id, encode(payload)))

    def close(self):
        self.connection.close()
