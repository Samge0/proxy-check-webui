"""测试运行管理：每厂商一条采样线程，到时自动停止，支持手动停止；进程重启后悬挂任务标记 interrupted。"""
from __future__ import annotations

import threading
import time

from . import db, proxy
from .db import now_str


class RunManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[int, list[threading.Thread]] = {}
        self._stops: dict[int, threading.Event] = {}
        self._manual_stops: set[int] = set()

    def start(self, run_id: int, vendor_ids: list[int], cfg: dict) -> None:
        stop = threading.Event()
        threads = [
            threading.Thread(target=self._worker, args=(run_id, vid, cfg, stop), daemon=True)
            for vid in vendor_ids
        ]
        for thread in threads:
            thread.start()
        monitor = threading.Thread(target=self._finalize, args=(run_id, threads, stop), daemon=True)
        monitor.start()
        with self._lock:
            self._threads[run_id] = threads
            self._stops[run_id] = stop

    def stop(self, run_id: int) -> bool:
        with self._lock:
            stop = self._stops.get(run_id)
            alive = any(t.is_alive() for t in self._threads.get(run_id, []))
        if stop is None or not alive:
            return False
        with self._lock:
            self._manual_stops.add(run_id)
        stop.set()
        return True

    def is_alive(self, run_id: int) -> bool:
        with self._lock:
            threads = self._threads.get(run_id, [])
        return any(t.is_alive() for t in threads)

    def _worker(self, run_id: int, vendor_id: int, cfg: dict, stop: threading.Event) -> None:
        vendor = db.query_one("SELECT * FROM vendors WHERE id=?", (vendor_id,))
        if vendor is None:
            return
        interval = cfg["interval_seconds"]
        deadline = time.monotonic() + cfg["duration_minutes"] * 60
        while not stop.is_set():
            started = time.monotonic()
            result = proxy.probe_ip(vendor, cfg["target_url"], cfg["timeout_seconds"], cfg["fallbacks"])
            record = {
                "run_id": run_id,
                "vendor_id": vendor_id,
                "ts": now_str(),
                "ok": result["ok"],
                "ip": result["ip"],
                "latency_ms": result["latency_ms"],
                "error": result["error"],
                "endpoint": result["endpoint"],
            }
            db.insert("samples", record)
            if started + interval >= deadline:
                break
            wake_at = min(started + interval, deadline)
            stop.wait(wake_at - time.monotonic())

    def _finalize(self, run_id: int, threads: list[threading.Thread], stop: threading.Event) -> None:
        for thread in threads:
            thread.join()
        with self._lock:
            manual = run_id in self._manual_stops
            self._threads.pop(run_id, None)
            self._stops.pop(run_id, None)
            self._manual_stops.discard(run_id)
        status = "stopped" if manual else "completed"
        db.execute("UPDATE runs SET status=?, ended_at=? WHERE id=?", (status, now_str(), run_id))


def recover_interrupted() -> None:
    rows = db.query("SELECT id FROM runs WHERE status='running'")
    for row in rows:
        db.execute("UPDATE runs SET status='interrupted', ended_at=? WHERE id=?", (now_str(), row["id"]))
