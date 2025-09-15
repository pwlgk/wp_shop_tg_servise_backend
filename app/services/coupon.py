# app/services/coupon.py
import httpx
from fastapi import HTTPException, status
from app.clients.woocommerce import wc_client
from app.schemas.coupon import Coupon
from app.models.user import User # Нужен для корзины
from app.services.cart import get_user_cart # Для получения суммы корзины

async def validate_coupon_for_user(db, redis, user: User, coupon_code: str) -> Coupon:
    """Валидирует купон с учетом текущей корзины пользователя."""
    
    # Получаем "чистую" стоимость товаров в корзине
    cart_data = await get_user_cart(db, redis, user)
    cart_total = cart_data.total_items_price

    payload = {
        "coupon_code": coupon_code,
        "cart_total": cart_total
    }
    
    try:
        response = await wc_client.post("headless-api/v1/coupons/validate", json=payload)
        return Coupon.model_validate(response)
    except httpx.HTTPStatusError as e:
        error_message = "Промокод недействителен."
        if e.response.status_code in [400, 404]:
            try:
                error_details = e.response.json()
                error_message = error_details.get("message", error_message)
            except Exception:
                pass
        raise HTTPException(status_code=e.response.status_code, detail=error_message)