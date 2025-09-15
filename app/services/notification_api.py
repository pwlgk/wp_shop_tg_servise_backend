# app/services/notification_api.py
import math
from sqlalchemy.orm import Session
from app.crud import notification as crud_notification
from app.models.user import User
from app.schemas.product import PaginatedNotifications

def get_paginated(db: Session, user: User, page: int, size: int, unread_only: bool):
    """Собирает пагинированный ответ для уведомлений."""
    skip = (page - 1) * size
    
    notifications = crud_notification.get_notifications(
        db, user_id=user.id, skip=skip, limit=size, unread_only=unread_only
    )
    total_items = crud_notification.count_notifications(db, user_id=user.id, unread_only=unread_only)
    total_pages = math.ceil(total_items / size) if total_items > 0 else 1
    
    return PaginatedNotifications(
        total_items=total_items,
        total_pages=total_pages,
        current_page=page,
        size=size,
        items=notifications
    )

def mark_as_read(db: Session, user: User, notification_id: int):
    return crud_notification.mark_notification_as_read(db, user_id=user.id, notification_id=notification_id)

def mark_all_as_read(db: Session, user: User):
    crud_notification.mark_all_notifications_as_read(db, user_id=user.id)