"""Serving-path latency benchmark for the FastAPI layer.

Measures in-process HTTP round-trip latency through the real application:
routing, dependency resolution, handler execution and JSON serialisation. The
database and cache layers are replaced with in-memory stubs so the figure
isolates the serving path rather than Postgres round-trips.

What this is and is not
-----------------------
This is a *framework and serialisation* measurement taken with ASGI transport in
the same process. It is NOT end-to-end production latency: there is no network
hop, no TLS, no uvicorn worker pool, no connection pooling and no real query
cost. Reported as "in-process, DB stubbed" wherever it is quoted, because a
latency number without its measurement conditions is not a measurement.

Usage:
    python scripts/benchmark_api.py [--reps 300] [--out docs/api_latency.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Row(dict):
    """Row object exposing both mapping and attribute access, as SQLAlchemy does."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @property
    def _mapping(self) -> dict:
        return dict(self)


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows

    def first(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return next(iter(self._rows[0].values())) if self._rows else None


def _canned_rows(n: int = 200) -> list[_Row]:
    now = datetime.now(UTC)
    return [
        _Row({
            "id": i, "ts": now - timedelta(hours=i), "symbol": "BTC",
            "open": 42000.0 + i, "high": 42500.0 + i, "low": 41500.0 + i,
            "close": 42100.0 + i, "volume": 1234.5, "quote_volume": 5.2e7,
            "trade_count": 4321, "interval": "1h",
            "signal": "HOLD", "strength": 5.0, "confidence": 0.61,
            "entry_price": 42100.0, "stop_loss": 41000.0, "take_profit": 43500.0,
            "sentiment_score": 0.12, "avg_sentiment": 0.10, "weighted_score": 0.11,
            "prediction": "UP", "created_at": now, "is_active": True,
            "metrics": {}, "metadata": {},
        })
        for i in range(n)
    ]


def install_stubs():
    """Replace the async DB session and Redis client with in-memory stubs."""
    from backend import database as dbmod

    rows = _canned_rows()

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def execute(self, *a, **k): return _Result(rows)
        async def commit(self): return None
        async def close(self): return None

    class _Redis:
        _store: dict[str, Any] = {}
        async def get(self, k): return self._store.get(k)
        async def set(self, k, v, **kw): self._store[k] = v
        async def ping(self): return True
        async def publish(self, *a, **k): return 1

    async def _execute_raw_sql(session, query, params=None):  # noqa: ARG001
        return _Result(rows)

    dbmod.db_manager.session_factory = lambda: _Session()
    dbmod.db_manager.redis_client = _Redis()
    dbmod.execute_raw_sql = _execute_raw_sql

    # Handlers that imported the symbol directly need patching in their module.
    import importlib
    for mod in ("backend.api.signals", "backend.api.coins", "backend.api.predictions",
                "backend.api.sentiment", "backend.api.health", "backend.api.backtest",
                "backend.api.model_ops", "backend.api.live_feed"):
        try:
            m = importlib.import_module(mod)
            if hasattr(m, "execute_raw_sql"):
                m.execute_raw_sql = _execute_raw_sql
            if hasattr(m, "db_manager"):
                m.db_manager = dbmod.db_manager
        except Exception:
            continue


def percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    def pct(p): return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]
    return {"n": len(s), "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99),
            "mean_ms": statistics.fmean(s), "min_ms": s[0], "max_ms": s[-1]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--paths", type=str, default="")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "api_latency.json")
    args = ap.parse_args()

    install_stubs()
    from fastapi.testclient import TestClient
    from backend.main import app

    # Neutralise startup/shutdown so no real connections are attempted.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop(_app):
        yield

    app.router.lifespan_context = _noop

    # Endpoints that read stored data and serialise it. Excluded: routes that
    # reach live external APIs or probe accelerators, whose latency is dominated
    # by the external call rather than the serving path.
    default_paths = [
        "/api/v1/health/", "/api/health", "/api/v1/system",
        "/api/v1/coins/", "/api/v1/signals/", "/api/market/snapshot",
        "/metrics", "/openapi.json",
    ]
    wanted = args.paths.split(",") if args.paths else default_paths
    available = {r.path for r in app.routes if hasattr(r, "methods") and "GET" in r.methods}
    concrete = [p for p in wanted if p in available]

    results, skipped = {}, []
    with TestClient(app) as client:
        for path in concrete:
            try:
                t0 = time.perf_counter()
                probe = client.get(path)
                probe_s = time.perf_counter() - t0
            except Exception as exc:
                skipped.append((path, f"raised {type(exc).__name__}"))
                continue
            if probe.status_code != 200:
                skipped.append((path, f"HTTP {probe.status_code}"))
                continue
            if probe_s > 0.5:
                skipped.append((path, f"slow probe {probe_s*1000:.0f} ms, likely external I/O"))
                continue
            for _ in range(20):           # warm up
                client.get(path)
            lat = []
            for _ in range(args.reps):
                t0 = time.perf_counter()
                client.get(path)
                lat.append((time.perf_counter() - t0) * 1000)
            results[path] = percentiles(lat)
            results[path]["bytes"] = len(probe.content)

    print(f"{'endpoint':<40}{'n':>5}{'p50':>9}{'p95':>9}{'p99':>9}{'bytes':>9}")
    print("-" * 81)
    for path, v in sorted(results.items(), key=lambda kv: kv[1]["p50_ms"]):
        print(f"{path:<40}{v['n']:>5}{v['p50_ms']:>9.3f}{v['p95_ms']:>9.3f}"
              f"{v['p99_ms']:>9.3f}{v['bytes']:>9}")

    if results:
        p50s = [v["p50_ms"] for v in results.values()]
        p95s = [v["p95_ms"] for v in results.values()]
        print(f"\nacross {len(results)} endpoints: median p50 = {statistics.median(p50s):.3f} ms, "
              f"median p95 = {statistics.median(p95s):.3f} ms")
        print(f"fastest p50 = {min(p50s):.3f} ms   slowest p95 = {max(p95s):.3f} ms")

    if skipped:
        print(f"\nskipped {len(skipped)} endpoints (need auth or live services):")
        for path, why in skipped[:12]:
            print(f"  {path:<44} {why}")

    payload = {
        "conditions": "in-process ASGI transport, database and cache stubbed, "
                      "no network hop, no TLS, no worker pool",
        "reps_per_endpoint": args.reps,
        "endpoints": results,
        "skipped": [{"path": p, "reason": w} for p, w in skipped],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
