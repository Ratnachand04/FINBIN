from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]+)['\"]?"),
]


def _mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _mask_sensitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    if isinstance(value, str):
        masked = value
        for pattern in SENSITIVE_PATTERNS:
            masked = pattern.sub(lambda m: f"{m.group(1)}=***", masked)
        return masked
    return value


def _json_sink(record: dict[str, Any]) -> str:
    payload = {
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "extra": _mask_sensitive(record["extra"]),
    }
    return json.dumps(payload, ensure_ascii=True)


def setup_logger(env: str = "development") -> Any:
    logger.remove()
    log_level = "DEBUG" if env.lower() == "development" else "INFO"

    logger.add(
        sys.stdout,
        level=log_level,
        colorize=True,
        enqueue=True,
        backtrace=True,
        diagnose=(env.lower() == "development"),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[request_id]}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        filter=lambda record: record["extra"].setdefault("request_id", "-") or True,
    )

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "app.json.log",
        level=log_level,
        rotation="100 MB",
        retention="30 days",
        compression="gz",
        enqueue=True,
        serialize=False,
        format="{message}",
        filter=lambda record: True,
        opener=lambda file, flags: open(file, flags, encoding="utf-8"),
    )
    logger.add(
        log_dir / "errors.json.log",
        level="ERROR",
        rotation="1 day",
        retention="30 days",
        compression="gz",
        enqueue=True,
        serialize=False,
        format="{message}",
        filter=lambda record: record["level"].name in {"ERROR", "CRITICAL"},
        opener=lambda file, flags: open(file, flags, encoding="utf-8"),
    )

    logger.configure(patcher=lambda record: record.update(message=_json_sink(record)))
    logger.info("Logger initialized", env=env)
    return logger


def log_api_request(endpoint: str, method: str, status_code: int, duration: float, user_info: dict[str, Any] | None = None) -> None:
    logger.bind(component="api", request_id=create_request_id()).info(
        "API request",
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        duration_ms=round(duration * 1000, 2),
        user=_mask_sensitive(user_info or {}),
    )


def log_data_collection(source: str, count: int, duration: float, errors: list[str] | None = None) -> None:
    logger.bind(component="collector", request_id=create_request_id()).info(
        "Data collection",
        source=source,
        collected=count,
        duration_ms=round(duration * 1000, 2),
        errors=_mask_sensitive(errors or []),
        success=(count > 0 and not errors),
    )


def log_ml_inference(model: str, input_shape: Any, duration: float, result: dict[str, Any]) -> None:
    logger.bind(component="ml", request_id=create_request_id()).info(
        "ML inference",
        model=model,
        input_shape=str(input_shape),
        duration_ms=round(duration * 1000, 2),
        result_summary=_mask_sensitive(result),
    )


def log_signal_generation(coin: str, signal_type: str, confidence: float, factors: dict[str, Any]) -> None:
    logger.bind(component="signal", request_id=create_request_id()).info(
        "Signal generated",
        coin=coin,
        signal_type=signal_type,
        confidence=confidence,
        factors=_mask_sensitive(factors),
        generated_at=datetime.now(UTC).isoformat(),
    )


def log_error(error: Exception, context: dict[str, Any] | None = None) -> None:
    logger.bind(component="error", request_id=create_request_id()).exception(
        "Unhandled error",
        error_type=type(error).__name__,
        context=_mask_sensitive(context or {}),
    )


def log_system_metrics(metrics: dict[str, Any]) -> None:
    logger.bind(component="metrics", request_id=create_request_id()).info(
        "System metrics",
        timestamp=datetime.now(UTC).isoformat(),
        metrics=_mask_sensitive(metrics),
    )


def create_request_id() -> str:
    return str(uuid.uuid4())
