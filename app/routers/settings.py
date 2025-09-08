# app/routers/settings.py
from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app.core.redis import get_redis_client
from app.schemas.settings import ShopSettings
from app.services import settings as settings_service

router = APIRouter()

@router.get("/settings", response_model=ShopSettings)
async def get_settings(redis: Redis = Depends(get_redis_client)):
    """Получение глобальных настроек магазина."""
    return await settings_service.get_shop_settings(redis)