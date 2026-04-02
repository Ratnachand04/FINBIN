from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from celery import Celery
from sqlalchemy import update

logger = logging.getLogger(__name__)

celery_app = Celery(
    "binfin",
    broker="redis://redis:6379/1",
    backend="redis://redis:6379/2",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=86400,  # 24 hours
)


async def _update_job_status(
    job_id: str,
    status: str,
    progress_percent: int = 0,
    error_message: str | None = None,
) -> None:
    """Update fine-tuning job status in database."""
    from backend.database import db_session_context
    from backend.models.finetune import FinetuneJob

    try:
        async with db_session_context() as session:
            stmt = (
                update(FinetuneJob)
                .where(FinetuneJob.job_id == job_id)
                .values(
                    status=status,
                    progress_percent=progress_percent,
                    error_message=error_message,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        logger.exception(f"Failed to update job {job_id} status: {exc}")


@celery_app.task(bind=True, name="finetune.run_qlora")
def run_qlora_finetune(
    self: Any,
    job_id: str,
    adapter_name: str,
    ollama_model_name: str,
    trainer_mode: str = "gpu-qlora",
    dataset_limit: int = 15000,
    epochs: float = 1.0,
    learning_rate: float = 0.0002,
    batch_size: int = 1,
    grad_accum: int = 16,
) -> dict[str, Any]:
    """
    Async task to run adapter fine-tuning in the llm-trainer container.
    Updates job status via Redis and returns final status.
    Supports both Windows (PowerShell) and Unix-like systems (bash).
    """
    root_dir = Path(__file__).resolve().parents[2]
    is_windows = platform.system() == "Windows"
    
    if is_windows:
        run_script = root_dir / "scripts" / "run_llm_finetune.ps1"
    else:
        run_script = root_dir / "scripts" / "run_llm_finetune.sh"

    if not run_script.exists():
        error_msg = f"Fine-tune script not found: {run_script}"
        logger.error(error_msg)
        asyncio.run(_update_job_status(job_id, "failed", error_message=error_msg))
        return {"status": "failed", "error": error_msg}

    try:
        asyncio.run(_update_job_status(job_id, "running"))

        if is_windows:
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(run_script),
                f"-AdapterName={adapter_name}",
                f"-OllamaModelName={ollama_model_name}",
                f"-TrainerMode={trainer_mode}",
                f"-DatasetLimit={dataset_limit}",
                f"-Epochs={epochs}",
                f"-LearningRate={learning_rate}",
                f"-BatchSize={batch_size}",
                f"-GradAccum={grad_accum}",
            ]
        else:
            cmd = [
                "bash",
                str(run_script),
                "--adapter-name",
                adapter_name,
                "--ollama-model-name",
                ollama_model_name,
                "--trainer-mode",
                trainer_mode,
                "--dataset-limit",
                str(dataset_limit),
                "--epochs",
                str(epochs),
                "--lr",
                str(learning_rate),
                "--batch-size",
                str(batch_size),
                "--grad-accum",
                str(grad_accum),
            ]

        logger.info(f"Starting fine-tune task {job_id}: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(root_dir),
        )

        stdout, stderr = process.communicate(timeout=86400)

        if stdout:
            logger.info(f"Fine-tune task {job_id} output:\n{stdout}")
        
        if process.returncode == 0:
            logger.info(f"Fine-tune task {job_id} completed successfully")
            asyncio.run(_update_job_status(job_id, "completed", progress_percent=100))
            return {
                "status": "completed",
                "job_id": job_id,
                "adapter_name": adapter_name,
                "ollama_model_name": ollama_model_name,
                "trainer_mode": trainer_mode,
            }
        else:
            error_msg = f"Fine-tune script failed with exit code {process.returncode}: {stderr}"
            logger.error(error_msg)
            asyncio.run(_update_job_status(job_id, "failed", error_message=error_msg))
            return {"status": "failed", "job_id": job_id, "error": error_msg}

    except subprocess.TimeoutExpired:
        error_msg = "Fine-tune task timed out after 24 hours"
        logger.error(error_msg)
        process.kill()
        asyncio.run(_update_job_status(job_id, "failed", error_message=error_msg))
        return {"status": "failed", "job_id": job_id, "error": error_msg}
    except Exception as exc:
        error_msg = f"Unexpected error during fine-tuning: {exc}"
        logger.exception(error_msg)
        asyncio.run(_update_job_status(job_id, "failed", error_message=error_msg))
        return {"status": "failed", "job_id": job_id, "error": error_msg}
