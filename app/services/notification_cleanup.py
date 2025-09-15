# app/services/notification_cleanup.py
import logging
from app.db.session import SessionLocal
from app.crud import notification as crud_notification

logger = logging.getLogger(__name__)

# Настройки (можно вынести в config.py)
DELETE_READ_NOTIFICATIONS_AFTER_DAYS = 30
DELETE_ANY_NOTIFICATION_AFTER_DAYS = 90

def cleanup_old_notifications_task():
    """Фоновая задача для "умного" удаления старых уведомлений."""
    logger.info("--- Starting scheduled job: Smart Cleanup of Old Notifications ---")
    with SessionLocal() as db:
        try:
            deleted_count = crud_notification.smart_delete_old_notifications(
                db,
                read_older_than_days=DELETE_READ_NOTIFICATIONS_AFTER_DAYS,
                any_older_than_days=DELETE_ANY_NOTIFICATION_AFTER_DAYS
            )
            if deleted_count > 0:
                logger.info(f"Successfully deleted {deleted_count} old notifications.")
            else:
                logger.info("No old notifications to delete.")
        except Exception as e:
            logger.error("An error occurred during notification cleanup task", exc_info=True)
            db.rollback()
    logger.info("--- Finished scheduled job: Smart Cleanup of Old Notifications ---")