# app/services/points_expiration.py

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.crud import loyalty as crud_loyalty
from app.crud import notification as crud_notification
from app.bot.services import notification as bot_notification_service
from app.models.user import User
from app.models.loyalty import LoyaltyTransaction

logger = logging.getLogger(__name__)

# Настройки
NOTIFY_DAYS_BEFORE_EXPIRATION = [7, 3, 1]


async def notify_about_expiring_points_task():
    """
    Фоновая задача для отправки упреждающих уведомлений о сгорании баллов.
    """
    logger.info("--- Starting scheduled job: Notify About Expiring Points ---")
    with SessionLocal() as db:
        try:
            for days in NOTIFY_DAYS_BEFORE_EXPIRATION:
                expiring_soon_list = crud_loyalty.get_transactions_expiring_soon(
                    db, days_before_expiration=days
                )
                if not expiring_soon_list:
                    logger.info(f"No points expiring in {days} days.")
                    continue
                
                logger.info(f"Found {len(expiring_soon_list)} users with points expiring in {days} days.")
                for user_id, total_points in expiring_soon_list:
                    user = db.query(User).filter(User.id == user_id).first()
                    if user:
                        try:
                            await bot_notification_service.send_points_expiring_soon_notification(
                                db=db, user=user, points_expiring=int(total_points), days_left=days
                            )
                        except Exception:
                            logger.error(f"Failed to send expiring points notification to user {user.id}", exc_info=True)
        except Exception:
            logger.error("An error occurred during expiring points notification task", exc_info=True)
            db.rollback()
    logger.info("--- Finished scheduled job: Notify About Expiring Points ---")


async def expire_points_task():
    """
    Основная задача планировщика.
    Находит и "сжигает" просроченные бонусные баллы, используя логику FIFO.
    """
    logger.info("--- Starting scheduled job: Expire Loyalty Points (FIFO) ---")
    
    users_with_expired_points = {}  # {user_id: total_expired_points}

    # Используем `with` для автоматического управления сессией (открытие, закрытие, откат)
    with SessionLocal() as db:
        try:
            user_ids_to_check_tuples = crud_loyalty.get_users_with_expiring_points(db)
            user_ids_to_check = [uid for uid, in user_ids_to_check_tuples]
            
            if not user_ids_to_check:
                logger.info("No users with expiring points found to process.")
                return

            logger.info(f"Found {len(user_ids_to_check)} users to check for point expiration.")
            
            for user_id in user_ids_to_check:
                all_transactions = crud_loyalty.get_all_user_transactions_chronological(db, user_id)
                expiring_pool = deque()
                
                for trans in all_transactions:
                    if trans.points > 0 and trans.expires_at:
                        expiring_pool.append({'points': trans.points, 'expires_at': trans.expires_at, 'id': trans.id})
                    elif trans.points < 0 and trans.type != 'expired':
                        points_to_spend = abs(trans.points)
                        while points_to_spend > 0 and expiring_pool:
                            oldest_bonus = expiring_pool[0]
                            if oldest_bonus['points'] <= points_to_spend:
                                points_to_spend -= oldest_bonus['points']
                                expiring_pool.popleft()
                            else:
                                oldest_bonus['points'] -= points_to_spend
                                points_to_spend = 0
                
                points_to_burn_now = 0
                today = datetime.now(timezone.utc)

                for bonus in list(expiring_pool):
                    if bonus['expires_at'] < today:
                        has_expired_trans = db.query(LoyaltyTransaction).filter_by(
                            type='expired', related_transaction_id=bonus['id']
                        ).first()
                        
                        if not has_expired_trans:
                            points_to_burn_now += bonus['points']
                            # Функция create_transaction просто добавляет в сессию, но не коммитит
                            crud_loyalty.create_transaction(
                                db=db, user_id=user_id, points=-bonus['points'],
                                type='expired', related_transaction_id=bonus['id']
                            )
                
                if points_to_burn_now > 0:
                    users_with_expired_points[user_id] = points_to_burn_now
            
            # --- ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ ---
            # Коммитим ВСЕ созданные 'expired' транзакции в конце цикла
            db.commit()
            # ------------------------

        except Exception as e:
            logger.error("Error during points expiration DB processing", exc_info=True)
            db.rollback() # `with` сам сделает rollback при выходе с ошибкой, но так надежнее

    # --- Блок отправки уведомлений (остается без изменений) ---
    if users_with_expired_points:
        logger.info(f"Preparing to send expiration notifications to {len(users_with_expired_points)} users.")
        with SessionLocal() as notify_db:
            for user_id, total_expired in users_with_expired_points.items():
                try:
                    user = notify_db.query(User).filter(User.id == user_id).first()
                    if user:
                        # Уведомление в бот
                        await bot_notification_service.send_points_expired_notification(notify_db, user, int(total_expired))
                        # Уведомление в Mini App
                        crud_notification.create_notification(
                            db=notify_db, user_id=user.id, type="points_expired",
                            title="Бонусные баллы сгорели",
                            message=f"С вашего счета списано {int(total_expired)} баллов по истечению срока их действия."
                        )
                        notify_db.commit()
                except Exception as e:
                    logger.error(f"Failed to send expiration notification to user {user.id}", exc_info=True)
                    notify_db.rollback()

    logger.info("--- Finished scheduled job: Expire Loyalty Points ---")