# app/routers/v1/admin/settings.py

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# Импортируем зависимости и модели
from app.dependencies import get_db, get_admin_user
from app.models.user import User

# Импортируем схемы для настроек
from app.schemas.settings import ShopSettings, ShopSettingsUpdate

# Импортируем сервисы
from app.services import admin as admin_service

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля.
# Префикс /settings будет добавлен на уровне выше в admin/__init__.py
router = APIRouter()


@router.get("", response_model=ShopSettings)
async def get_shop_settings_endpoint():
    """
    [АДМИН] Возвращает текущие глобальные настройки магазина.
    Данные кешируются.
    """
    return await admin_service.get_current_shop_settings()


@router.put("", response_model=ShopSettings)
async def update_shop_settings_endpoint(
    settings_data: ShopSettingsUpdate,
    # Зависимость get_admin_user здесь нужна, чтобы защитить эндпоинт,
    # даже если мы не используем `admin_user` в теле функции.
    admin_user: User = Depends(get_admin_user)
):
    """
    [АДМИН] Обновляет глобальные настройки магазина.
    Можно передавать только те поля, которые нужно изменить.
    После обновления кеш настроек будет сброшен.
    """
    return await admin_service.update_shop_settings(settings_data)