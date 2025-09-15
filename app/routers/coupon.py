# app/routers/coupon.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from redis.asyncio import Redis
from app.core.redis import get_redis_client
from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.coupon import Coupon, CouponValidateRequest
from app.services import coupon as coupon_service

router = APIRouter()

@router.post("/coupons/validate", response_model=Coupon)
async def validate_coupon_endpoint(
    request_data: CouponValidateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    return await coupon_service.validate_coupon_for_user(db, redis, current_user, request_data.coupon_code)