# app/routers/notification.py

from fastapi import APIRouter, Depends, Response, Query # <-- Добавляем Query
from sqlalchemy.orm import Session
from typing import List

from app.dependencies import get_current_user, get_db
from app.models.user import User
# --- ИСПРАВЛЕННЫЕ ИМПОРТЫ ---
from app.schemas.notification import Notification
# Импортируем PaginatedNotifications. Предполагается, что он в schemas/common.py или schemas/product.py
from app.schemas.product import PaginatedNotifications 
from app.services import notification_api as notification_service_api # <-- Исправляем импорт
# -----------------------------

router = APIRouter()

@router.get("/notifications", response_model=PaginatedNotifications)
def get_user_notifications(
    unread_only: bool = Query(False, description="Вернуть только непрочитанные уведомления"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    size: int = Query(20, ge=1, le=100, description="Количество уведомлений на странице"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получает пагинированный список уведомлений.
    По умолчанию возвращает все. Используйте ?unread_only=true для получения только новых.
    """
    # --- ИСПРАВЛЕННЫЙ ВЫЗОВ ---
    return notification_service_api.get_paginated(db, current_user, page, size, unread_only)
    # -----------------------------

@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Помечает одно уведомление как прочитанное."""
    # --- ИСПРАВЛЕННЫЙ ВЫЗОВ ---
    notification_service_api.mark_as_read(db, current_user, notification_id)
    return Response(status_code=204)

@router.post("/notifications/read-all", status_code=204)
def read_all_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Помечает ВСЕ уведомления пользователя как прочитанные."""
    # --- ИСПРАВЛЕННЫЙ ВЫЗОВ ---
    notification_service_api.mark_all_as_read(db, current_user)
    return Response(status_code=204)