from __future__ import annotations

import shutil
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
import httpx

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
    ollama_ok = False
    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            ollama_ok = response.status_code == 200
    except Exception:
        ollama_ok = False

    disk = shutil.disk_usage(".")
    return {
        "status": "ok" if db_ok and redis_ok and ollama_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": {
            "database": db_ok,
            "redis": redis_ok,
            "ollama": ollama_ok,
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": (disk.used / disk.total * 100) if disk.total else 0,
            },
            "memory": _memory_stats(),
        },
    }
