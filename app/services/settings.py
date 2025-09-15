# app/services/settings.py

import json
import logging
from redis.asyncio import Redis

from app.clients.woocommerce import wc_client
from app.schemas.settings import ShopSettings
from app.core.config import settings as app_settings # Используем псевдоним, чтобы избежать конфликтов

logger = logging.getLogger(__name__)

# ID страницы "Настройки магазина" в WordPress.
# Важно: этот ID должен соответствовать ID страницы, созданной в вашей админке.
SHOP_SETTINGS_PAGE_ID = app_settings.SHOP_SETTINGS_PAGE_ID # Предполагаем, что вынесли в .env / config.py
CACHE_TTL_SECONDS = 3600  # Кешируем настройки на 1 час

async def get_shop_settings(redis: Redis) -> ShopSettings:
    """
    Получает глобальные настройки магазина из WordPress через REST API,
    используя кеширование в Redis.
    """
    cache_key = "shop_settings"
    
    # 1. Пытаемся получить настройки из кеша
    cached_settings = await redis.get(cache_key)
    if cached_settings:
        try:
            return ShopSettings.model_validate(json.loads(cached_settings))
        except Exception as e:
            logger.warning(f"Failed to validate cached shop settings: {e}. Fetching fresh settings.")
            
    # 2. Если в кеше нет, идем в WordPress REST API
    logger.info(f"Fetching fresh shop settings from WP page ID: {SHOP_SETTINGS_PAGE_ID}")
    try:
        response = await wc_client.async_client.get(f"wp/v2/pages/{SHOP_SETTINGS_PAGE_ID}")
        response.raise_for_status()
        page_data = response.json()
        
        acf_data = page_data.get("acf", {})
        
        # 3. Безопасно извлекаем и приводим к нужному типу каждое значение.
        #    Предоставляем адекватные значения по умолчанию на случай, если поле не заполнено в ACF.
        settings_values = {
            "min_order_amount": float(acf_data.get("min_order_amount", 0.0)),
            "welcome_bonus_amount": int(acf_data.get("welcome_bonus_amount", 0)),
            "is_welcome_bonus_active": bool(acf_data.get("is_welcome_bonus_active", False)),
            "max_points_payment_percentage": int(acf_data.get("max_points_payment_percentage", 100)),
            "referral_welcome_bonus": int(acf_data.get("referral_welcome_bonus", 0)),
            "referrer_bonus": int(acf_data.get("referrer_bonus", 0)),
            "birthday_bonus_amount": int(acf_data.get("birthday_bonus_amount", 0)),
            "client_data_version": int(acf_data.get("client_data_version", 1)),
        }

        # 4. Валидируем данные через Pydantic-схему
        settings_data = ShopSettings.model_validate(settings_values)
            
        # 5. Сохраняем валидные данные в кеш
        await redis.set(cache_key, settings_data.model_dump_json(), ex=CACHE_TTL_SECONDS)
        
        return settings_data

    except Exception as e:
        logger.error("CRITICAL: Failed to fetch or parse shop settings from WordPress.", exc_info=True)
        # В случае критической ошибки возвращаем "безопасные" настройки по умолчанию,
        # чтобы приложение не упало полностью.
        return ShopSettings(
            min_order_amount=999999.0, # Ставим высокую планку, чтобы предотвратить заказы
            welcome_bonus_amount=0,
            is_welcome_bonus_active=False,
            max_points_payment_percentage=0,
            referral_welcome_bonus=0,
            referrer_bonus=0,
            birthday_bonus_amount=0,
            client_data_version=1,
        )