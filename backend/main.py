from fastapi import FastAPI

from backend.api.routes.health import router as health_router
from backend.api.routes.market import router as market_router
from backend.api.routes.signals import router as signals_router
from backend.config import settings

app = FastAPI(title=settings.app_name)
app.include_router(health_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
