"""运行配置：从项目根 .env 读取，缺失时用默认值。"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# WEBUI_DB 可覆盖数据库位置（测试隔离 / Docker 挂载），默认项目 data/ 下
DB_PATH = Path(os.environ.get("WEBUI_DB", str(PROJECT_ROOT / "data" / "app.db")))


def _load_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

HOST = os.environ.get("WEBUI_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEBUI_PORT", "8821"))
DEFAULT_TARGET = "https://ifconfig.me/ip"
