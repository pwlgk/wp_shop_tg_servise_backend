# app/routers/v1/admin/general.py

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

# Импортируем зависимости и сервисы
from app.dependencies import get_db
from app.schemas.admin import AdminDashboardStats
from app.services import admin as admin_service
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# Создаем роуter для этого модуля.
# Префикс не указываем, так как эндпоинты будут иметь вид /admin/dashboard, /admin/cache/clear
router = APIRouter()

@router.get("/dashboard/stats", response_model=AdminDashboardStats)
async def get_admin_dashboard_stats(
    db: Session = Depends(get_db),
    period: Literal["day", "week", "month"] = Query("day", description="Период для расчета статистики")
):
    """
    [АДМИН] Возвращает расширенную статистику для главного экрана админ-панели.
    """
    return await admin_service.get_dashboard_stats(db, period)


# Старый эндпоинт /dashboard можно удалить или оставить как редирект/алиас
@router.get("/dashboard", include_in_schema=False)
async def get_admin_dashboard_legacy(db: Session = Depends(get_db)):
    # Просто вызываем новый эндпоинт со значением по умолчанию
    return await admin_service.get_dashboard_stats(db, period="day")

@router.post("/cache/clear")
async def clear_cache_endpoint(
    target: Literal["all", "settings", "catalog"] = "all"
):
    """
    [АДМИН] Принудительно очищает кеш в Redis.
    
    - **all**: Очищает ВСЕ ключи в текущей базе Redis.
    - **settings**: Очищает только кеш настроек магазина.
    - **catalog**: Очищает кеш каталога (товары, категории, cms-контент).
    """
    if target == "all":
        deleted_count = await redis_client.flushdb()
        # flushdb возвращает bool, но для консистентности ответа посчитаем это как "много"
        message = "All Redis cache has been cleared."
        logger.info("Full Redis cache clear was triggered by admin.")
        return {"status": "ok", "message": message}
    
    keys_to_delete = []
    if target == "settings":
        # Ищем ключи, начинающиеся с 'shop_settings'
        keys_to_delete = [key async for key in redis_client.scan_iter("shop_settings*")]
    elif target == "catalog":
        # Собираем ключи для всех сущностей каталога
        keys_to_delete.extend([key async for key in redis_client.scan_iter("product:*")])
        keys_to_delete.extend([key async for key in redis_client.scan_iter("products:*")])
        keys_to_delete.extend([key async for key in redis_client.scan_iter("categories:*")])
        keys_to_delete.extend([key async for key in redis_client.scan_iter("cms:*")])
        
    if keys_to_delete:
        # Удаляем все найденные ключи за один вызов
        await redis_client.delete(*keys_to_delete)
        message = f"{len(keys_to_delete)} keys for '{target}' have been cleared."
        logger.info(f"Cache clear for target '{target}' was triggered by admin. Deleted {len(keys_to_delete)} keys.")
        return {"status": "ok", "message": message}
        
    return {"status": "ok", "message": f"No keys to clear for target '{target}'."}