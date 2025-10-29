# app/routers/v1/admin.py

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status, Response
from sqlalchemy.orm import Session

from app.dependencies import get_admin_user, get_db
from app.models.user import User
from app.schemas.admin import (
    PaginatedAdminUsers,
    AdminUserDetails,
    AdminSendMessageRequest,
    AdminAdjustPointsRequest,
)
from app.services import admin as admin_service
from app.crud import user as crud_user, loyalty as crud_loyalty, notification as crud_notification
from app.services import loyalty as loyalty_service
from app.bot.services import notification as bot_notification_service

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля. Префикс будет добавлен на уровне выше.
router = APIRouter()


@router.get("", response_model=PaginatedAdminUsers)
async def get_users_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    level: str | None = Query(None, description="Фильтр по уровню: bronze, silver, gold"),
    bot_blocked: bool | None = Query(None, description="Фильтр по статусу блокировки бота"),
    search: str | None = Query(None, description="Поиск по ID, TG ID, username или ФИО"),
    db: Session = Depends(get_db),
):
    """
    [АДМИН] Возвращает пагинированный список пользователей с фильтрами и поиском.
    """
    filters = {
        "level": level if level and level != 'all' else None,
        "bot_blocked": bot_blocked,
        "search": search,
    }
    active_filters = {k: v for k, v in filters.items() if v is not None}
    
    return await admin_service.get_paginated_users(db, page, size, **active_filters)


@router.get("/{user_id}", response_model=AdminUserDetails)
async def get_user_details_endpoint(
    user_id: int,
    orders_page: int = Query(1, ge=1, description="Страница для истории заказов"),
    points_page: int = Query(1, ge=1, description="Страница для истории баллов"),
    db: Session = Depends(get_db),
):
    """
    [АДМИН] Возвращает детальную информацию о конкретном пользователе.
    """
    user_details = await admin_service.get_user_details(db, user_id, orders_page, points_page)
    if not user_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user_details


@router.post("/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
def block_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Блокирует пользователя."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_blocked = True
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/unblock", status_code=status.HTTP_204_NO_CONTENT)
def unblock_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Разблокирует пользователя."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_blocked = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/message")
async def send_message_to_user_endpoint(
    user_id: int,
    request_data: AdminSendMessageRequest,
    db: Session = Depends(get_db),
):
    """[АДМИН] Отправляет сообщение пользователю от имени бота."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    success, reason = await bot_notification_service._send_message(db, user, request_data.message_text)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to send message: {reason}")
    return {"status": "ok"}


@router.post("/{user_id}/points")
async def adjust_user_points_endpoint(
    user_id: int,
    request_data: AdminAdjustPointsRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    """[АДМИН] Начисляет или списывает баллы пользователю и отправляет уведомление."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    comment = request_data.comment or f"Корректировка от админа {admin_user.telegram_id}"
    transaction_type = f"admin_adjust_{'add' if request_data.points >= 0 else 'sub'}"

    crud_loyalty.create_transaction(db, user_id=user.id, points=request_data.points, type=transaction_type)
    
    title = "Баланс баллов изменен"
    message = f"Администратор изменил ваш баланс на {request_data.points} баллов. Комментарий: {comment}"
    crud_notification.create_notification(db, user_id=user.id, type="points_update", title=title, message=message)
    
    await bot_notification_service.send_manual_points_update(db, user, request_data.points, request_data.comment)
    
    new_balance = loyalty_service.get_user_balance(db, user)
    return {"status": "ok", "new_balance": new_balance}