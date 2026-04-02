from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.config import settings
from backend.database import db_session_context
from backend.models.user import User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/access-token"
)

async def get_current_user(
    token: str = Depends(reusable_oauth2)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise HTTPException(status_code=403, detail="Could not validate credentials")
        user_id = int(user_id_str)
    except (JWTError, ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    
    async with db_session_context() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")
        
        return user
