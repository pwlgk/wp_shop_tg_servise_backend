# app/services/coupon.py

import httpx
import logging
from typing import List, Dict, Any

from fastapi import HTTPException, status
from app.clients.woocommerce import wc_client
from app.models.user import User
from app.schemas.coupon import Coupon

logger = logging.getLogger(__name__)


async def validate_coupon(user: User, coupon_code: str, line_items: list) -> Coupon:
    logger.info(f"User {user.id} (WP ID: {user.wordpress_id}) is validating coupon '{coupon_code}'.")
    
    try:
        # --- ЛОГИРОВАНИЕ ПЕРЕД ЗАПРОСОМ К WC ---
        logger.info("Fetching billing address from WooCommerce...")
        customer_response = await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")
        billing_address = customer_response.json().get("billing")
        logger.info("Billing address fetched successfully.")
        # ------------------------------------
    except Exception as e:
        logger.error(f"Failed to fetch billing address for user {user.id} before coupon validation.", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Не удалось получить данные пользователя.")

    payload = {
        "coupon_code": coupon_code,
        "line_items": line_items,
        "customer_id": user.wordpress_id,
        "billing": billing_address
    }
    
    logger.info(f"Sending payload to WP for coupon validation: {payload}")

    try:
        response_data = await wc_client.post("headless-api/v1/coupons/validate", json=payload)
        logger.info(f"Successfully validated coupon '{coupon_code}'. Discount: {response_data.get('discount_amount')}")
        return Coupon.model_validate(response_data)
        
    except httpx.HTTPStatusError as e:
        error_message = "Промокод недействителен или не может быть применен к вашей корзине."
        print("!!!!!!!!!!!!!!!!")
        if e.response.status_code in [400, 403, 404]: # Добавляем 403
            try:
                error_details = e.response.json()
                error_message = error_details.get("message", error_message)
            except Exception:
                pass
        
        logger.warning(f"Coupon validation failed for '{coupon_code}'. Status: {e.response.status_code}, Reason: {error_message}")
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