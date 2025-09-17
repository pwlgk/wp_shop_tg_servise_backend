# app/routers/coupon.py

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from redis.asyncio import Redis

from app.dependencies import get_current_user, get_db
from app.core.redis import get_redis_client
from app.models.user import User
from app.schemas.coupon import Coupon, CouponValidateRequest
from app.services import coupon as coupon_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/coupons/validate", response_model=Coupon)
async def validate_coupon_endpoint(
    request_data: CouponValidateRequest,
    # Мы не передаем зависимости current_user, db, redis в сервис,
    # так как вся логика валидации теперь инкапсулирована в WordPress,
    # а состав корзины передается напрямую от фронтенда.
):
    """
    Валидирует промокод для переданного состава корзины.
    Возвращает детали купона и точную сумму скидки, если он применим.
    """
    # Преобразуем Pydantic-объекты в словари для отправки в сервис
    line_items_dict = [item.model_dump() for item in request_data.line_items]
    
    return await coupon_service.validate_coupon(
        coupon_code=request_data.coupon_code,
        line_items=line_items_dict
    )