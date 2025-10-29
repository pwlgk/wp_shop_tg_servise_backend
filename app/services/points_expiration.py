# app/services/points_expiration.py

import logging
import asyncio
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.crud import loyalty as crud_loyalty
from app.crud import notification as crud_notification
from app.bot.services import notification as bot_notification_service
from app.models.user import User
from app.models.loyalty import LoyaltyTransaction

logger = logging.getLogger(__name__)

# Настройки, которые можно вынести в app/core/config.py
NOTIFY_DAYS_BEFORE_EXPIRATION = [7, 3, 1]  # Уведомлять за 7, 3 и 1 день


async def notify_about_expiring_points_task():
    """
    Фоновая задача для отправки упреждающих уведомлений о сгорании баллов.
    Использует FIFO-логику для точного расчета сгорающей суммы.
    """
    logger.info("--- Starting scheduled job: Notify About Expiring Points ---")

    try:
        # Получаем всех пользователей, у которых в принципе есть баллы со сроком годности
        with SessionLocal() as db:
            user_ids_to_check_tuples = db.query(LoyaltyTransaction.user_id).filter(
                LoyaltyTransaction.expires_at.isnot(None),
                LoyaltyTransaction.points > 0
            ).distinct().all()
            user_ids_to_check = [user_id for user_id, in user_ids_to_check_tuples]

        if not user_ids_to_check:
            logger.info("No users with expiring points found to check.")
            return

        logger.info(f"Found {len(user_ids_to_check)} users with potentially expiring points to check.")

        for user_id in user_ids_to_check:
            for days in NOTIFY_DAYS_BEFORE_EXPIRATION:
                points_to_notify = 0
                # Для каждого пользователя и каждого срока считаем точную сумму к сгоранию
                with SessionLocal() as db:
                    points_to_notify = crud_loyalty.get_unspent_expiring_soon(db, user_id, days_left=days)

                if points_to_notify > 0:
                    with SessionLocal() as db:
                        user = db.query(User).filter(User.id == user_id).first()
                        if user and user.bot_accessible:
                            try:
                                logger.info(f"Notifying user {user.id} about {points_to_notify} points expiring in {days} days.")
                                await bot_notification_service.send_points_expiring_soon_notification(
                                    db=db,
                                    user=user,
                                    points_expiring=int(points_to_notify),
                                    days_left=days
                                )
                                # Пауза, чтобы не превысить лимиты API Telegram
                                await asyncio.sleep(0.1)
                            except Exception as e:
                                logger.error(f"Failed to send expiring points notification to user {user.id}", exc_info=True)

    except Exception as e:
        logger.error("An error occurred during expiring points notification task", exc_info=True)

    logger.info("--- Finished scheduled job: Notify About Expiring Points ---")


async def expire_points_task():
    """
    Основная задача: находит и "сжигает" просроченные бонусные баллы,
    используя строгую FIFO-логику для предотвращения отрицательного баланса.
    """
    logger.info("--- Starting scheduled job: Expire Loyalty Points ---")
    
    try:
        users_to_process_ids = []
        with SessionLocal() as db:
            # 1. Находим всех пользователей, у которых в принципе могут быть просроченные баллы
            user_ids_tuples = crud_loyalty.get_users_with_potentially_expired_points(db)
            users_to_process_ids = [user_id for user_id, in user_ids_tuples]

        if not users_to_process_ids:
            logger.info("No users with expired points found to process.")
            return

        logger.info(f"Found {len(users_to_process_ids)} users with potentially expired points to process.")

        # 2. Для каждого пользователя рассчитываем точную сумму к списанию и выполняем операцию
        for user_id in users_to_process_ids:
            with SessionLocal() as db:
                try:
                    # 2.1. Рассчитываем, сколько баллов РЕАЛЬНО нужно списать
                    points_to_expire = crud_loyalty.calculate_points_to_expire_for_user(db, user_id)
                    total_points_to_expire = int(points_to_expire)


                    if total_points_to_expire <= 0:
                        # Если по итогу нечего списывать, просто "закрываем" старые транзакции
                        logger.info(f"User {user_id}: No unspent points to expire.")
                        crud_loyalty.mark_positive_transactions_as_processed(db, user_id)
                        db.commit()
                        continue
                    
                    # 5. Жесткая проверка: не можем списать больше, чем есть на балансе
                    current_balance = crud_loyalty.get_user_balance(db, user_id)
                    if total_points_to_expire > current_balance:
                        logger.warning(
                            f"User {user_id}: Calculation resulted in expiring {total_points_to_expire} points, "
                            f"but balance is only {current_balance}. Expiring {current_balance} instead to prevent negative balance."
                        )
                        total_points_to_expire = current_balance

                    if total_points_to_expire <= 0:
                         logger.info(f"User {user_id}: After final check, nothing to expire.")
                         crud_loyalty.mark_positive_transactions_as_processed(db, user_id)
                         db.commit()
                         continue

                    logger.info(f"User {user_id}: Calculated {total_points_to_expire} points to expire.")

                    # 6. Создаем транзакцию списания
                    crud_loyalty.create_transaction(
                        db=db, user_id=user_id, points=-total_points_to_expire, type="expired"
                    )
                    
                    # 7. "Закрываем" старые транзакции
                    crud_loyalty.mark_positive_transactions_as_processed(db, user_id)
                    
                    db.commit()

                    # 8. Отправляем уведомления
                    user = db.query(User).filter(User.id == user_id).first()
                    if user and user.bot_accessible:
                        await bot_notification_service.send_points_expired_notification(
                            db, user, total_points_to_expire
                        )
                        crud_notification.create_notification(
                            db=db,
                            user_id=user.id,
                            type="points_expired",
                            title="Бонусные баллы сгорели",
                            message=f"С вашего счета списано {total_points_to_expire} баллов по истечению срока их действия."
                        )
                        db.commit()
                        await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error(f"Failed to process points expiration for user {user_id}", exc_info=True)
                    db.rollback()

    except Exception as e:
        logger.error("A critical error occurred during the main loop of expire_points_task", exc_info=True)

    logger.info("--- Finished scheduled job: Expire Loyalty Points ---")