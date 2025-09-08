# app/services/settings.py
import json
from redis.asyncio import Redis
from app.clients.woocommerce import wc_client
from app.schemas.settings import ShopSettings
import logging

logger = logging.getLogger(__name__)
# ID страницы "Настройки магазина" в WordPress. Замените на ваш!
SHOP_SETTINGS_PAGE_ID = 329 # <-- УБЕДИТЕСЬ, ЧТО ID ВЕРНЫЙ
CACHE_TTL_SECONDS = 3600 # Кешируем на 1 час

async def get_shop_settings(redis: Redis) -> ShopSettings:
    """Получает глобальные настройки магазина, используя кеш."""
    cache_key = "shop_settings"
    
    cached_settings = await redis.get(cache_key)
    if cached_settings:
        return ShopSettings.model_validate(json.loads(cached_settings))
            
    response = await wc_client.async_client.get(f"wp/v2/pages/{SHOP_SETTINGS_PAGE_ID}")
    response.raise_for_status()
    page_data = response.json()
    

    acf_data = page_data.get("acf", {})
    
    # Безопасно извлекаем значения
    min_order_amount = 0.0
    welcome_bonus_amount = 0
    is_welcome_bonus_active = False

    if isinstance(acf_data, dict):
        try:
            min_order_amount = float(acf_data.get("min_order_amount", 0.0))
            welcome_bonus_amount = int(acf_data.get("welcome_bonus_amount", 0))
            is_welcome_bonus_active = bool(acf_data.get("is_welcome_bonus_active", False))
        except (ValueError, TypeError):
            logger.warning("Warning: Could not parse one of the settings from ACF.")

    settings_data = ShopSettings(
        min_order_amount=min_order_amount,
        welcome_bonus_amount=welcome_bonus_amount,
        is_welcome_bonus_active=is_welcome_bonus_active
    )
            
    await redis.set(cache_key, settings_data.model_dump_json(), ex=CACHE_TTL_SECONDS)
    
    return settings_data