# app/services/coupon_admin.py (ФИНАЛЬНАЯ ВЕРСИЯ)

import logging
from typing import List
from fastapi import HTTPException, status
import httpx

from app.clients.woocommerce import wc_client
from app.schemas.coupon import CouponCreate, CouponDetails

logger = logging.getLogger(__name__)


async def get_all_coupons() -> List[CouponDetails]:
    """
    Получает список всех промокодов из WooCommerce.
    """
    try:
        response = await wc_client.get("wc/v3/coupons", params={"per_page": 100, "orderby": "id", "order": "desc"})
        coupons_data = response.json()
        return [CouponDetails.model_validate(coupon) for coupon in coupons_data]
    except httpx.HTTPStatusError as e:
        logger.error("WooCommerce API error while fetching coupons.", exc_info=True)
        raise HTTPException(status_code=e.response.status_code, detail=f"WooCommerce error: {e.response.text}")
    except Exception as e:
        logger.error("Failed to fetch coupons from WooCommerce.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch coupons due to an unexpected error.")


async def create_coupon(coupon_data: CouponCreate) -> CouponDetails:
    """
    Создает новый промокод в WooCommerce.
    """
    payload = coupon_data.model_dump(exclude_unset=True)
    
    try:
        # Теперь wc_client.post либо вернет dict, либо выбросит исключение
        response_data = await wc_client.post("wc/v3/coupons", json=payload)
        return CouponDetails.model_validate(response_data)
        
    except httpx.HTTPStatusError as e:
        # Этот блок теперь будет корректно ловить ошибки 400
        if e.response.status_code == 400:
            error_details = e.response.json()
            message = error_details.get("message", "Invalid data provided.")
            logger.warning(f"Failed to create coupon. WooCommerce returned 400: {message}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
        
        logger.error(f"Error creating coupon in WooCommerce.", exc_info=True)
        raise HTTPException(status_code=e.response.status_code, detail="Could not create coupon in WooCommerce.")


async def delete_coupon(coupon_id: int):
    """
    Удаляет промокод из WooCommerce.
    """
    try:
        await wc_client.delete(f"wc/v3/coupons/{coupon_id}", params={"force": True})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found.")
        logger.error(f"Error deleting coupon {coupon_id} in WooCommerce.", exc_info=True)
        raise HTTPException(status_code=e.response.status_code, detail=f"WooCommerce error: {e.response.text}")