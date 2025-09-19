# app/crud/notification.py
from sqlalchemy.orm import Session
from sqlalchemy import update, or_
from app.models.notification import Notification
from typing import List
from datetime import datetime, timedelta

def create_notification(
    db: Session, 
    user_id: int, 
    type: str, 
    title: str, 
    message: str | None = None,
    related_entity_id: str | None = None,
    action_url: str | None = None,  # <-- Убедитесь, что этот аргумент присутствует
    image_url: str | None = None
) -> Notification:
    """Создает новое уведомление для пользователя."""
    db_notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        related_entity_id=related_entity_id,
        action_url=action_url, # <-- И используется здесь
        image_url=image_url
    )
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification

def get_notifications(
    db: Session, 
    user_id: int, 
    skip: int = 0, 
    limit: int = 20, 
    unread_only: bool = False
) -> List[Notification]:
    """Получает пагинированный список уведомлений."""
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

def count_notifications(db: Session, user_id: int, unread_only: bool = False) -> int:
    """Считает уведомления с фильтром."""
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.count()

def mark_notification_as_read(db: Session, user_id: int, notification_id: int) -> Notification | None:
    """Помечает конкретное уведомление как прочитанное."""
    stmt = update(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).values(is_read=True).returning(Notification)
    result = db.execute(stmt).scalar_one_or_none()
    db.commit()
    return result

def mark_all_notifications_as_read(db: Session, user_id: int):
    """Помечает все уведомления пользователя как прочитанные."""
    stmt = update(Notification).where(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).values(is_read=True)
    db.execute(stmt)
    db.commit()

def smart_delete_old_notifications(
    db: Session, 
    read_older_than_days: int, 
    any_older_than_days: int
) -> int:
    """Удаляет уведомления по "умным" правилам."""
    read_threshold = datetime.utcnow() - timedelta(days=read_older_than_days)
    any_threshold = datetime.utcnow() - timedelta(days=any_older_than_days)
    
    condition_read_and_old = (
        Notification.is_read == True,
        Notification.created_at < read_threshold
    )
    condition_any_very_old = (
        Notification.created_at < any_threshold
    )
    
    result = db.query(Notification).filter(
        or_(*condition_read_and_old, condition_any_very_old)
    ).delete(synchronize_session=False)
    
    db.commit()
    return result

def get_notification_by_type_and_entity(
    db: Session, 
    user_id: int, 
    type: str, 
    related_entity_id: str
) -> Notification | None:
    """
    Ищет конкретное уведомление для пользователя, чтобы избежать дубликатов.
    """
    return db.query(Notification).filter_by(
        user_id=user_id,
        type=type,
        related_entity_id=related_entity_id
    ).first()