# app/routers/coupon.py

import logging
from fastapi import APIRouter, Depends
from typing import List

from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.coupon import Coupon, CouponValidateRequest
from app.services import coupon as coupon_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/coupons/validate", response_model=Coupon)
async def validate_coupon_endpoint(
    request_data: CouponValidateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Валидирует промокод для переданного состава корзины.
    """
    logger.info(f"Received request to validate coupon '{request_data.coupon_code}' for user {current_user.id}")
    print(f"Received request to validate coupon '{request_data.coupon_code}' for user {current_user.id}")
    line_items_dict = [item.model_dump() for item in request_data.line_items]
    
    return await coupon_service.validate_coupon(
        user=current_user,
        coupon_code=request_data.coupon_code,
        line_items=line_items_dict
    )