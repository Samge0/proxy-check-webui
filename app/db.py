"""SQLite 访问层：每次操作独立短连接（采样写入频率低，避免跨线程共享连接），WAL 支持运行中并发读。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    protocol TEXT NOT NULL DEFAULT 'http',
    host TEXT NOT NULL DEFAULT '',
    port INTEGER,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    raw_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    target_url TEXT NOT NULL,
    duration_minutes REAL NOT NULL,
    interval_seconds REAL NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 10,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS run_vendors (
    run_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    PRIMARY KEY (run_id, vendor_id)
);
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL,
    ip TEXT,
    latency_ms INTEGER,
    error TEXT,
    endpoint TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_run ON samples(run_id, vendor_id);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def query(sql: str, args: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def query_one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    conn = connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid or cur.rowcount
    finally:
        conn.close()


def insert(table: str, record: dict) -> int:
    cols = list(record)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    return execute(sql, tuple(record[c] for c in cols))


def update(table: str, record: dict, where: str, args: tuple = ()) -> None:
    sets = ", ".join(f"{col}=?" for col in record)
    sql = f"UPDATE {table} SET {sets} WHERE {where}"
    execute(sql, tuple(record.values()) + args)
