# app/services/coupon.py

import httpx
import logging
from typing import List, Dict, Any

from fastapi import HTTPException, status
from app.clients.woocommerce import wc_client
from app.schemas.coupon import Coupon

logger = logging.getLogger(__name__)


async def validate_coupon(coupon_code: str, line_items: List[Dict[str, Any]]) -> Coupon:
    """
    Отправляет запрос на кастомный эндпоинт в WordPress для валидации
    купона с учетом переданного состава корзины.

    Args:
        coupon_code: Код купона для проверки.
        line_items: Список словарей товарных позиций, e.g., [{"product_id": 1, "quantity": 2}].

    Returns:
        Объект Pydantic-схемы Coupon с деталями и суммой скидки.

    Raises:
        HTTPException: Если купон невалиден или произошла ошибка API.
    """
    payload = {
        "coupon_code": coupon_code,
        "line_items": line_items
    }
    
    logger.info(f"Validating coupon '{coupon_code}' for {len(line_items)} line items.")

    try:
        # Обращаемся к нашему кастомному эндпоинту
        response_data = await wc_client.post("headless-api/v1/coupons/validate", json=payload)
        return Coupon.model_validate(response_data)
        
    except httpx.HTTPStatusError as e:
        # Обрабатываем ошибки, которые вернул наш PHP-эндпоинт
        error_message = "Промокод недействителен или не может быть применен к вашей корзине."
        
        # Пытаемся извлечь более конкретное сообщение об ошибке
        if e.response.status_code in [400, 404]:
            try:
                error_details = e.response.json()
                # WooCommerce REST API (и наш кастомный эндпоинт) кладет сообщение в 'message'
                error_message = error_details.get("message", error_message)
            except Exception:
                # Если тело ответа - не JSON, используем сообщение по умолчанию
                pass
        
        logger.warning(f"Coupon validation failed for '{coupon_code}'. Status: {e.response.status_code}, Reason: {error_message}")
        
        # "Пробрасываем" ошибку наверх, чтобы роутер вернул ее фронтенду
        raise HTTPException(
            status_code=e.response.status_code,
            detail=error_message
        )
    except Exception as e:
        logger.error(f"An unexpected error occurred during coupon validation for '{coupon_code}'", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось проверить промокод. Пожалуйста, попробуйте позже."
        )