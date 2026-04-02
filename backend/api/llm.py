from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import db_manager, db_session_context
from backend.models.finetune import FinetuneJob
from backend.workers.finetune_worker import run_qlora_finetune

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


class FinetuneJobRequest(BaseModel):
    adapter_name: str = Field(default="binfin-mistral-qlora", description="Adapter name for save location")
    ollama_model_name: str = Field(default="binfin-mistral-finance", description="Ollama model name for local inference")
    trainer_mode: Literal["gpu-qlora", "cpu-lora"] = Field(
        default="gpu-qlora",
        description="Training pipeline mode: gpu-qlora or cpu-lora",
    )
    dataset_limit: int = Field(default=15000, ge=100, le=100000, description="Max rows to export for training")
    epochs: float = Field(default=1.0, ge=0.1, le=10.0, description="Training epochs")
    learning_rate: float = Field(default=0.0002, ge=1e-6, le=0.01, description="Learning rate for QLoRA")
    batch_size: int = Field(default=1, ge=1, le=4, description="Batch size per GPU")
    grad_accum: int = Field(default=16, ge=1, le=128, description="Gradient accumulation steps")


class FinetuneJobResponse(BaseModel):
    job_id: str
    status: str
    adapter_name: str
    ollama_model_name: str
    trainer_mode: str = "gpu-qlora"
    progress_percent: int
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


class FinetuneJobDetailResponse(FinetuneJobResponse):
    dataset_limit: int
    epochs: float
    learning_rate: float
    batch_size: int
    grad_accum: int
    updated_at: str


@router.post("/finetune", response_model=FinetuneJobResponse)
async def trigger_finetune(req: FinetuneJobRequest) -> FinetuneJobResponse:
    """
    Trigger a new QLoRA fine-tuning job asynchronously.
    Returns job_id for status monitoring.
    """
    job_id = str(uuid.uuid4())[:16]

    try:
        async with db_session_context() as session:
            job = FinetuneJob(
                job_id=job_id,
                status="pending",
                adapter_name=req.adapter_name,
                ollama_model_name=req.ollama_model_name,
                dataset_limit=req.dataset_limit,
                epochs=int(req.epochs),
                learning_rate=str(req.learning_rate),
                batch_size=req.batch_size,
                grad_accum=req.grad_accum,
                metadata={"created_from": "api", "trainer_mode": req.trainer_mode},
            )
            session.add(job)
            await session.commit()

        run_qlora_finetune.delay(
            job_id=job_id,
            adapter_name=req.adapter_name,
            ollama_model_name=req.ollama_model_name,
            trainer_mode=req.trainer_mode,
            dataset_limit=req.dataset_limit,
            epochs=req.epochs,
            learning_rate=req.learning_rate,
            batch_size=req.batch_size,
            grad_accum=req.grad_accum,
        )

        return FinetuneJobResponse(
            job_id=job_id,
            status="pending",
            adapter_name=req.adapter_name,
            ollama_model_name=req.ollama_model_name,
            trainer_mode=req.trainer_mode,
            progress_percent=0,
            created_at=datetime.now(UTC).isoformat(),
        )

    except Exception as exc:
        logger.exception(f"Failed to trigger fine-tune job: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/finetune/{job_id}", response_model=FinetuneJobDetailResponse)
async def get_finetune_job(job_id: str) -> FinetuneJobDetailResponse:
    """Get fine-tuning job status and details."""
    try:
        async with db_session_context() as session:
            result = await session.execute(select(FinetuneJob).where(FinetuneJob.job_id == job_id))
            job = result.scalars().first()

            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            metadata = job.metadata if isinstance(job.metadata, dict) else {}

            return FinetuneJobDetailResponse(
                job_id=job.job_id,
                status=job.status,
                adapter_name=job.adapter_name,
                ollama_model_name=job.ollama_model_name,
                trainer_mode=str(metadata.get("trainer_mode", "gpu-qlora")),
                progress_percent=job.progress_percent,
                created_at=job.created_at.isoformat() if job.created_at else None,
                started_at=job.started_at.isoformat() if job.started_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                error_message=job.error_message,
                dataset_limit=job.dataset_limit,
                epochs=job.epochs,
                learning_rate=float(job.learning_rate),
                batch_size=job.batch_size,
                grad_accum=job.grad_accum,
                updated_at=job.updated_at.isoformat() if job.updated_at else None,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get fine-tune job: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/finetune", response_model=list[FinetuneJobResponse])
async def list_finetune_jobs(
    status: str | None = Query(None, regex="^(pending|running|completed|failed)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[FinetuneJobResponse]:
    """List fine-tuning jobs with optional status filter."""
    try:
        async with db_session_context() as session:
            query = select(FinetuneJob).order_by(FinetuneJob.created_at.desc()).limit(limit).offset(offset)

            if status:
                query = query.where(FinetuneJob.status == status)

            result = await session.execute(query)
            jobs = result.scalars().all()

            return [
                FinetuneJobResponse(
                    job_id=j.job_id,
                    status=j.status,
                    adapter_name=j.adapter_name,
                    ollama_model_name=j.ollama_model_name,
                    trainer_mode=str((j.metadata or {}).get("trainer_mode", "gpu-qlora")) if isinstance(j.metadata, dict) else "gpu-qlora",
                    progress_percent=j.progress_percent,
                    created_at=j.created_at.isoformat() if j.created_at else None,
                    started_at=j.started_at.isoformat() if j.started_at else None,
                    completed_at=j.completed_at.isoformat() if j.completed_at else None,
                    error_message=j.error_message,
                )
                for j in jobs
            ]
    except Exception as exc:
        logger.exception(f"Failed to list fine-tune jobs: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/finetune/{job_id}/cancel")
async def cancel_finetune_job(job_id: str) -> dict[str, Any]:
    """Cancel a pending or running fine-tuning job."""
    try:
        async with db_session_context() as session:
            result = await session.execute(select(FinetuneJob).where(FinetuneJob.job_id == job_id))
            job = result.scalars().first()

            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            if job.status not in ["pending", "running"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel job in status: {job.status}",
                )

            run_qlora_finetune.revoke(job_id, terminate=True)

            from sqlalchemy import update

            await session.execute(update(FinetuneJob).where(FinetuneJob.job_id == job_id).values(status="cancelled"))
            await session.commit()

            return {"status": "cancelled", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to cancel fine-tune job: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
