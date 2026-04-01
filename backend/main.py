from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.routes.health import router as health_router
from backend.api.routes.market import router as market_router
from backend.api.routes.signals import router as signals_router
from backend.config import settings
from backend.database import db_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
	await db_manager.initialize()
	try:
		yield
	finally:
		await db_manager.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(health_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
