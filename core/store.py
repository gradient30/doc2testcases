# -*- coding: utf-8 -*-
"""SQLite 持久化：任务状态（断点重试的基础）与设置（API Key 等）。

线程安全：FastAPI 事件循环与工作线程共用一个连接，靠一把锁串行化。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    status     TEXT NOT NULL,             -- queued/running/done/failed/canceled/interrupted
    params     TEXT NOT NULL DEFAULT '{}',
    docs       TEXT NOT NULL DEFAULT '[]',
    render     TEXT NOT NULL DEFAULT '{}',
    error      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4.6",
    "style": "openai",
    "rounds": 2,
}


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------ jobs
    def create_job(self, job_id: str, params: dict) -> None:
        now = int(time.time() * 1000)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (id, created_at, updated_at, version, status, params)"
                " VALUES (?, ?, ?, 1, 'queued', ?)",
                (job_id, now, now, json.dumps(params, ensure_ascii=False)))

    def update_job(self, job_id: str, *, status: str | None = None,
                   docs: list | None = None, render: dict | None = None,
                   error: str | None = None) -> None:
        sets, vals = ["updated_at = ?", "version = version + 1"], [int(time.time() * 1000)]
        if status is not None:
            sets.append("status = ?"); vals.append(status)
        if docs is not None:
            sets.append("docs = ?"); vals.append(json.dumps(docs, ensure_ascii=False))
        if render is not None:
            sets.append("render = ?"); vals.append(json.dumps(render, ensure_ascii=False))
        if error is not None:
            sets.append("error = ?"); vals.append(error)
        vals.append(job_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)

    def _row_to_job(self, row: sqlite3.Row) -> dict:
        return {"id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                "version": row["version"], "status": row["status"],
                "params": json.loads(row["params"]), "docs": json.loads(row["docs"]),
                "render": json.loads(row["render"]), "error": row["error"]}

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def recover_on_startup(self) -> list[str]:
        """进程重启后，把遗留的 queued/running 任务标记为 interrupted（可断点重试）。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'interrupted',"
                " error = COALESCE(NULLIF(error, ''), '进程重启中断，可重试续跑'),"
                " updated_at = ?, version = version + 1"
                " WHERE status IN ('queued', 'running')", (int(time.time() * 1000),))
            self._conn.commit()
            return [r[0] for r in self._conn.execute(
                "SELECT id FROM jobs WHERE status = 'interrupted'").fetchall()]

    # ------------------------------------------------------------ settings
    def settings_all(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        merged = dict(DEFAULT_SETTINGS)
        merged.update({r["key"]: r["value"] for r in rows})
        # rounds 存成 int
        try:
            merged["rounds"] = int(merged.get("rounds", 2))
        except (TypeError, ValueError):
            merged["rounds"] = 2
        return merged

    def settings_save(self, partial: dict) -> dict:
        for k, v in partial.items():
            if k in DEFAULT_SETTINGS and v is not None and str(v).strip() != "":
                with self._lock, self._conn:
                    self._conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (k, str(v).strip()))
        return self.settings_all()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
