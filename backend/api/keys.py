from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.dependencies import get_current_user
from backend.database import get_db
from backend.models.user import User, UserAPIKey

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])

class APIKeyCreate(BaseModel):
    provider: str
    api_key: str
    api_secret: str | None = None

class APIKeyResponse(BaseModel):
    id: int
    provider: str
    # Do not expose the actual API keys or secrets in responses usually for security
    # But for simplicity, we return the existence

@router.post("/", response_model=APIKeyResponse)
async def create_or_update_key(
    key_in: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    provider_upper = key_in.provider.upper()
    result = await db.execute(
        select(UserAPIKey).where(
            UserAPIKey.user_id == current_user.id,
            UserAPIKey.provider == provider_upper
        )
    )
    existing_key = result.scalar_one_or_none()
    
    if existing_key:
        existing_key.api_key = key_in.api_key
        existing_key.api_secret = key_in.api_secret
        await db.commit()
        await db.refresh(existing_key)
        return existing_key

    new_key = UserAPIKey(
        user_id=current_user.id,
        provider=provider_upper,
        api_key=key_in.api_key,
        api_secret=key_in.api_secret
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    return new_key

@router.get("/", response_model=List[APIKeyResponse])
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    result = await db.execute(
        select(UserAPIKey).where(UserAPIKey.user_id == current_user.id)
    )
    keys = result.scalars().all()
    return keys

@router.delete("/{provider}")
async def delete_key(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    provider_upper = provider.upper()
    result = await db.execute(
        select(UserAPIKey).where(
            UserAPIKey.user_id == current_user.id,
            UserAPIKey.provider == provider_upper
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
        
    await db.delete(key)
    await db.commit()
    return {"status": "ok", "message": "Key deleted successfully"}
