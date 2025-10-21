# app/routers/v1/admin/orders.py

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.admin import (
    PaginatedAdminOrders, 
    AdminOrderDetails,
    AdminOrderStatusUpdate,
    AdminOrderNoteCreate, # <- Новая схема
)
from app.schemas.order import Order # Импортируем базовую схему для ответа
from app.services import admin as admin_service

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля. 
# Префикс /orders будет добавлен на уровне выше в admin/__init__.py
router = APIRouter()


@router.get("", response_model=PaginatedAdminOrders)
async def get_orders_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(default=None, description="Фильтр по статусу: pending, processing, on-hold, etc."),
    search: str | None = Query(default=None, description="Поиск по номеру заказа, email или имени клиента"),
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Возвращает пагинированный список всех заказов.
    Исключает заказы со статусом 'checkout-draft'.
    """
    filters = {"status": status, "search": search}
    active_filters = {k: v for k, v in filters.items() if v}
    return await admin_service.get_paginated_orders(db, page, size, **active_filters)


@router.get("/{order_id}", response_model=AdminOrderDetails)
async def get_order_details_endpoint(
    order_id: int,
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Получает детальную информацию о конкретном заказе.
    """
    order_details = await admin_service.get_order_details(db, order_id)
    if not order_details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {order_id} not found."
        )
    return order_details


@router.put("/{order_id}/status", response_model=Order)
async def update_order_status_endpoint(
    order_id: int,
    status_update: AdminOrderStatusUpdate
):
    """
    [АДМИН] Обновляет статус заказа.
    """
    try:
        updated_order = await admin_service.update_order_status(order_id, status_update.status)
        return updated_order
    except HTTPException as e:
        # Перебрасываем HTTP исключения из сервиса дальше
        raise e
    except Exception as e:
        logger.error(f"Failed to update status for order {order_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred while updating order status.")


@router.post("/{order_id}/notes", status_code=status.HTTP_201_CREATED)
async def create_order_note_endpoint(
    order_id: int,
    note_data: AdminOrderNoteCreate
):
    """
    [АДМИН] Добавляет приватную заметку к заказу.
    """
    try:
        await admin_service.create_order_note(order_id, note_data.note)
        return {"status": "ok", "message": "Note added successfully."}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Failed to add note to order {order_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred while adding the note.")