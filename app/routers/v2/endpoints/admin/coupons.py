# app/routers/v1/admin/coupons.py

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Response

from app.schemas.coupon import CouponCreate, CouponDetails, PaginatedAdminCoupons
from app.services import coupon_admin as coupon_admin_service

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля.
router = APIRouter()


@router.get("", response_model=List[CouponDetails])
async def get_coupons_list():
    """
    [АДМИН] Получает список всех промокодов.
    """
    return await coupon_admin_service.get_all_coupons()


@router.post("", response_model=CouponDetails, status_code=status.HTTP_201_CREATED)
async def create_new_coupon(
    coupon_data: CouponCreate
):
    """
    [АДМИН] Создает новый промокод.
    """
    return await coupon_admin_service.create_coupon(coupon_data)


@router.delete("/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_existing_coupon(
    coupon_id: int
):
    """
    [АДМИН] Безвозвратно удаляет промокод.
    """
    await coupon_admin_service.delete_coupon(coupon_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)