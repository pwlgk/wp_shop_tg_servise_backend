# app/routers/order.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from redis.asyncio import Redis
from typing import List, Optional

from app.dependencies import get_current_user, get_db
from app.core.redis import get_redis_client
from app.models.user import User
from app.schemas.order import Order, OrderCreate
from app.schemas.product import PaginatedOrders
from app.services import order as order_service
from pydantic import BaseModel
router = APIRouter()

@router.post("/orders", response_model=Order)
async def create_new_order(
    order_data: OrderCreate, # <-- Теперь order_data приходит из тела запроса
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    return await order_service.create_order_from_cart(db, redis, current_user, order_data)


@router.get("/orders", response_model=PaginatedOrders)
async def get_orders_history(
    # Параметры пагинации остаются
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    
    # --- НОВЫЙ ПАРАМЕТР ---
    status: Optional[str] = Query(None, description="Фильтр по статусу заказа. Можно передать несколько через запятую (например, 'processing,on-hold')."),
    
    # Зависимости
    current_user: User = Depends(get_current_user)
):
    """
    Получение истории заказов текущего пользователя.
    Поддерживает пагинацию и фильтрацию по статусу.
    """
    return await order_service.get_user_orders(current_user, page, size, status)


class PaymentGateway(BaseModel):
    id: str
    title: Optional[str] = "" # <-- Делаем поле необязательным, по умолчанию пустая строка
    description: Optional[str] = "" # <-- И это тоже


@router.get("/payment-gateways", response_model=List[PaymentGateway])
async def get_available_payment_gateways():
    """
    Получение списка доступных способов оплаты.
    """
    return await order_service.get_payment_gateways()


@router.post("/orders/{order_id}/cancel", response_model=Order)
async def cancel_user_order(
    order_id: int,
    db: Session = Depends(get_db), # <-- Добавляем зависимость
    current_user: User = Depends(get_current_user)
):
    return await order_service.cancel_order(db, order_id, current_user) # <-- Передаем db


@router.get("/orders/{order_id}", response_model=Order)
async def get_single_order(
    order_id: int,
    current_user: User = Depends(get_current_user)
):
    """
    Получение детальной информации о конкретном заказе.
    """
    order_details = await order_service.get_order_details(order_id, current_user)
    if not order_details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заказ не найден или у вас нет прав на его просмотр."
        )
    return order_details