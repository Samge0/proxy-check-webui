"""FastAPI 入口：厂商 CRUD、隧道测试运行、指标对比与明细导出。"""
from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, proxy, stats
from .config import DEFAULT_TARGET, HOST, PORT
from .runner import RunManager, recover_interrupted
from .db import now_str

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
manager = RunManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    recover_interrupted()
    yield


app = FastAPI(title="隧道代理IP质量测试", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_shell(request: Request, call_next):
    # 页面与静态资源禁缓存：本地工具改动频繁，启发式缓存会让浏览器拿旧 JS
    # 与新 HTML 错位（曾致批量删除勾选列不渲染）；ETag 保留，未变更仍走 304
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class VendorIn(BaseModel):
    name: str
    protocol: str = "http"
    host: str = ""
    port: int | None = None
    username: str = ""
    password: str = ""
    raw_url: str = ""
    notes: str = ""
    enabled: bool = True


class RunIn(BaseModel):
    name: str = ""
    vendor_ids: list[int]
    duration_minutes: float = 60.0
    interval_seconds: float = 5.0
    timeout_seconds: int = 10
    target_url: str = DEFAULT_TARGET


def _vendor_record(data: VendorIn) -> dict:
    record = {
        "name": data.name.strip(),
        "protocol": data.protocol,
        "host": data.host.strip(),
        "port": data.port,
        "username": data.username.strip(),
        "password": data.password,
        "raw_url": data.raw_url.strip(),
        "notes": data.notes.strip(),
        "enabled": 1 if data.enabled else 0,
    }
    return record


def _run_vendor_rows(run_id: int) -> list[dict]:
    sql = (
        "SELECT v.id, v.name, v.protocol FROM run_vendors rv "
        "JOIN vendors v ON v.id=rv.vendor_id WHERE rv.run_id=? ORDER BY v.id"
    )
    return db.query(sql, (run_id,))


class RunBatchDeleteIn(BaseModel):
    run_ids: list[int]


def _get_run_or_404(run_id: int) -> dict:
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise HTTPException(404, "任务不存在")
    return run


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/vendors")
def list_vendors() -> list[dict]:
    return db.query("SELECT * FROM vendors ORDER BY id")


@app.post("/api/vendors")
def create_vendor(data: VendorIn) -> dict:
    record = _vendor_record(data)
    if not record["name"]:
        raise HTTPException(400, "厂商名称不能为空")
    exists = db.query_one("SELECT id FROM vendors WHERE name=?", (record["name"],))
    if exists:
        raise HTTPException(400, "厂商名称已存在")
    record["created_at"] = now_str()
    record["updated_at"] = now_str()
    vendor_id = db.insert("vendors", record)
    return {"id": vendor_id}


@app.put("/api/vendors/{vendor_id}")
def update_vendor(vendor_id: int, data: VendorIn) -> dict:
    if db.query_one("SELECT id FROM vendors WHERE id=?", (vendor_id,)) is None:
        raise HTTPException(404, "厂商不存在")
    dup = db.query_one("SELECT id FROM vendors WHERE name=? AND id<>?", (data.name.strip(), vendor_id))
    if dup:
        raise HTTPException(400, "厂商名称已存在")
    record = _vendor_record(data)
    record["updated_at"] = now_str()
    db.update("vendors", record, "id=?", (vendor_id,))
    return {"ok": True}


@app.delete("/api/vendors/{vendor_id}")
def delete_vendor(vendor_id: int) -> dict:
    db.execute("DELETE FROM vendors WHERE id=?", (vendor_id,))
    db.execute("DELETE FROM run_vendors WHERE vendor_id=?", (vendor_id,))
    return {"ok": True}


@app.post("/api/vendors/{vendor_id}/test")
def test_vendor(vendor_id: int) -> dict:
    vendor = db.query_one("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    if vendor is None:
        raise HTTPException(404, "厂商不存在")
    return proxy.probe_ip(vendor, DEFAULT_TARGET, 12, proxy.FALLBACK_ENDPOINTS)


@app.post("/api/runs")
def create_run(data: RunIn) -> dict:
    ids = list(dict.fromkeys(data.vendor_ids))
    if not ids:
        raise HTTPException(400, "至少选择一个厂商")
    marks = ",".join("?" * len(ids))
    found = db.query(f"SELECT id FROM vendors WHERE id IN ({marks})", tuple(ids))
    if len(found) != len(ids):
        raise HTTPException(400, "存在无效的厂商 ID")
    interval = max(1.0, float(data.interval_seconds))
    duration = max(0.1, float(data.duration_minutes))
    target_url = data.target_url.strip() or DEFAULT_TARGET
    record = {
        "name": data.name.strip(),
        "target_url": target_url,
        "duration_minutes": duration,
        "interval_seconds": interval,
        "timeout_seconds": max(3, int(data.timeout_seconds)),
        "status": "running",
        "started_at": now_str(),
    }
    run_id = db.insert("runs", record)
    for vid in ids:
        db.execute("INSERT INTO run_vendors (run_id, vendor_id) VALUES (?, ?)", (run_id, vid))
    cfg = {
        "target_url": target_url,
        "duration_minutes": duration,
        "interval_seconds": interval,
        "timeout_seconds": record["timeout_seconds"],
        "fallbacks": proxy.FALLBACK_ENDPOINTS,
    }
    manager.start(run_id, ids, cfg)
    return {"id": run_id}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    runs = db.query("SELECT * FROM runs ORDER BY id DESC")
    vendors_by_run: dict[int, list[str]] = {}
    for rv in db.query(
        "SELECT rv.run_id, rv.vendor_id, v.name FROM run_vendors rv "
        "LEFT JOIN vendors v ON v.id=rv.vendor_id ORDER BY rv.run_id, rv.vendor_id"
    ):
        label = rv["name"] or f"#{rv['vendor_id']}(已删)"
        vendors_by_run.setdefault(rv["run_id"], []).append(label)
    counts = {
        row["run_id"]: row["n"]
        for row in db.query("SELECT run_id, COUNT(*) AS n FROM samples GROUP BY run_id")
    }
    for run in runs:
        run["vendors"] = vendors_by_run.get(run["id"], [])
        run["samples"] = counts.get(run["id"], 0)
        run["alive"] = manager.is_alive(run["id"])
    return runs


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    run = _get_run_or_404(run_id)
    live = []
    totals = {
        row["vendor_id"]: row
        for row in db.query(
            "SELECT vendor_id, COUNT(*) AS probes, COALESCE(SUM(ok),0) AS ok_n, "
            "COUNT(DISTINCT ip) AS uniq FROM samples WHERE run_id=? GROUP BY vendor_id",
            (run_id,),
        )
    }
    for v in _run_vendor_rows(run_id):
        agg = totals.get(v["id"], {})
        last = db.query_one(
            "SELECT ts, ok, ip, latency_ms, error FROM samples "
            "WHERE run_id=? AND vendor_id=? ORDER BY id DESC LIMIT 1",
            (run_id, v["id"]),
        ) or {}
        card = {
            "vendor_id": v["id"],
            "name": v["name"],
            "probes": agg.get("probes", 0),
            "ok_n": agg.get("ok_n", 0),
            "uniq": agg.get("uniq", 0),
            "last": last,
        }
        live.append(card)
    return {"run": run, "vendors": live, "alive": manager.is_alive(run_id)}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: int) -> dict:
    _get_run_or_404(run_id)
    if not manager.stop(run_id):
        raise HTTPException(400, "任务未在运行")
    return {"ok": True}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: int) -> dict:
    if manager.is_alive(run_id):
        raise HTTPException(400, "任务运行中，请先停止再删除")
    db.execute("DELETE FROM samples WHERE run_id=?", (run_id,))
    db.execute("DELETE FROM run_vendors WHERE run_id=?", (run_id,))
    db.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"ok": True}


@app.post("/api/runs/batch-delete")
def batch_delete_runs(data: RunBatchDeleteIn) -> dict:
    ids = list(dict.fromkeys(data.run_ids))
    if not ids:
        raise HTTPException(400, "未选择要删除的任务")
    alive = [rid for rid in ids if manager.is_alive(rid)]
    if alive:
        raise HTTPException(400, f"任务 {', '.join(map(str, alive))} 运行中，请先停止再删除")
    for rid in ids:
        db.execute("DELETE FROM samples WHERE run_id=?", (rid,))
        db.execute("DELETE FROM run_vendors WHERE run_id=?", (rid,))
        db.execute("DELETE FROM runs WHERE id=?", (rid,))
    return {"deleted": len(ids)}


@app.get("/api/runs/{run_id}/summary")
def run_summary(run_id: int) -> dict:
    run = _get_run_or_404(run_id)
    vendor_rows = _run_vendor_rows(run_id)
    ips_by_vendor: dict[int, list[str]] = {v["id"]: [] for v in vendor_rows}
    counts = {
        row["vendor_id"]: row
        for row in db.query(
            "SELECT vendor_id, COUNT(*) AS probes, COALESCE(SUM(ok),0) AS ok_n "
            "FROM samples WHERE run_id=? GROUP BY vendor_id",
            (run_id,),
        )
    }
    ok_rows = db.query("SELECT vendor_id, ip FROM samples WHERE run_id=? AND ok=1 ORDER BY id", (run_id,))
    for row in ok_rows:
        if row["vendor_id"] in ips_by_vendor:
            ips_by_vendor[row["vendor_id"]].append(row["ip"])
    vendors = []
    for v in vendor_rows:
        cnt = counts.get(v["id"], {})
        metrics = stats.vendor_metrics(ips_by_vendor[v["id"]])
        summary = {
            "vendor_id": v["id"],
            "name": v["name"],
            "probes": cnt.get("probes", 0),
            "ok_n": cnt.get("ok_n", 0),
        }
        summary.update(metrics)
        vendors.append(summary)
    matrix = []
    for i in range(len(vendor_rows)):
        for j in range(i + 1, len(vendor_rows)):
            pair = stats.pairwise_intersection(
                ips_by_vendor[vendor_rows[i]["id"]], ips_by_vendor[vendor_rows[j]["id"]]
            )
            entry = {"a": vendor_rows[i]["name"], "b": vendor_rows[j]["name"]}
            entry.update(pair)
            matrix.append(entry)
    return {"run": run, "vendors": vendors, "matrix": matrix, "alive": manager.is_alive(run_id)}


@app.get("/api/runs/{run_id}/samples")
def run_samples(run_id: int, vendor_id: int, limit: int = 500) -> list[dict]:
    _get_run_or_404(run_id)
    sql = (
        "SELECT ts, ok, ip, latency_ms, error, endpoint FROM samples "
        "WHERE run_id=? AND vendor_id=? ORDER BY id DESC LIMIT ?"
    )
    return db.query(sql, (run_id, vendor_id, min(max(1, limit), 5000)))


@app.get("/api/runs/{run_id}/export")
def export_samples(run_id: int, vendor_id: int) -> Response:
    _get_run_or_404(run_id)
    rows = db.query(
        "SELECT ts, ok, ip, latency_ms, error, endpoint FROM samples "
        "WHERE run_id=? AND vendor_id=? ORDER BY id",
        (run_id, vendor_id),
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time", "ok", "ip", "latency_ms", "error", "endpoint"])
    for row in rows:
        writer.writerow(
            [row["ts"], row["ok"], row["ip"] or "", row["latency_ms"] or "", row["error"] or "", row["endpoint"] or ""]
        )
    headers = {"Content-Disposition": f'attachment; filename="run{run_id}_vendor{vendor_id}.csv"'}
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
