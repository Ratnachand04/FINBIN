from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from backend.database import db_manager

router = APIRouter(prefix="/api/v1/health", tags=["health"])


def _memory_stats() -> dict[str, Any]:
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return {
            "total": vm.total,
            "available": vm.available,
            "used": vm.used,
            "percent": vm.percent,
        }
    except Exception:
        return {"status": "unavailable"}


@router.get("/")
async def health_root() -> dict[str, Any]:
    db_ok = await db_manager.check_db_health()
    redis_ok = await db_manager.check_redis_health()
    disk = shutil.disk_usage(".")
    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": {
            "database": db_ok,
            "redis": redis_ok,
            "ollama": bool(__import__("os").environ.get("OLLAMA_BASE_URL")),
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": (disk.used / disk.total * 100) if disk.total else 0,
            },
            "memory": _memory_stats(),
        },
    }
