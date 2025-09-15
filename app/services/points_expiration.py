# app/services/points_expiration.py

import logging
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.crud import loyalty as crud_loyalty
from app.bot.services import notification as notification_service
from app.models.user import User
from app.crud import notification as crud_notification

logger = logging.getLogger(__name__)

# Настройки, которые можно вынести в app/core/config.py
NOTIFY_DAYS_BEFORE_EXPIRATION = [7, 3, 1]  # Уведомлять за 7, 3 и 1 день


async def notify_about_expiring_points_task():
    """
    Фоновая задача для отправки упреждающих уведомлений о сгорании баллов.
    Запускается ежедневно.
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
                            await notification_service.send_points_expiring_soon_notification(
                                db=db,
                                user=user,
                                points_expiring=int(total_points),
                                days_left=days
                            )
                        except Exception as e:
                            logger.error(f"Failed to send expiring points notification to user {user.id}", exc_info=True)

        except Exception as e:
            logger.error("An error occurred during expiring points notification task", exc_info=True)
            db.rollback()

    logger.info("--- Finished scheduled job: Notify About Expiring Points ---")


async def expire_points_task():
    """
    Основная задача планировщика.
    Находит и "сжигает" просроченные бонусные баллы.
    Запускается ежедневно.
    """
    logger.info("--- Starting scheduled job: Expire Loyalty Points ---")
    
    users_to_notify = {}  # Словарь для группировки {user_object: total_expired_points}

    # Блок работы с БД
    with SessionLocal() as db:
        try:
            expired_transactions = crud_loyalty.get_expired_positive_transactions(db)
            
            if not expired_transactions:
                logger.info("No expired points found to process.")
                return

            logger.info(f"Found {len(expired_transactions)} expired transactions to process.")

            for transaction in expired_transactions:
                # Создаем парную отрицательную транзакцию
                crud_loyalty.create_transaction(
                    db=db,
                    user_id=transaction.user_id,
                    points=-transaction.points,
                    type="expired",
                    order_id_wc=transaction.order_id_wc
                )
                
                # Собираем данные для уведомлений
                user = transaction.user
                if user not in users_to_notify:
                    users_to_notify[user] = 0
                users_to_notify[user] += transaction.points
            
            # Коммитим все "сжигающие" транзакции
            db.commit()

        except Exception as e:
            logger.error("An error occurred during points expiration task while processing DB", exc_info=True)
            db.rollback()
            return # Прерываем выполнение, если с БД что-то не так

    # Блок отправки уведомлений (вынесен за пределы основной транзакции)
    if not users_to_notify:
        return

    logger.info(f"Preparing to send expiration notifications to {len(users_to_notify)} users.")
    for user, total_expired in users_to_notify.items():
        # Для каждой отправки будем использовать новую, короткоживущую сессию,
        # чтобы обновить `bot_accessible` в случае ошибки.
        with SessionLocal() as notify_db:
            try:
                # Перепривязываем объект user к новой сессии
                user_in_session = notify_db.merge(user)
                logger.info(f"Notifying user {user.id} about {total_expired} expired points.")
                await notification_service.send_points_expired_notification(
                    notify_db, user_in_session, int(total_expired)
                )
                crud_notification.create_notification(
                    db=notify_db,
                    user_id=user_in_session.id,
                    type="points_expired",
                    title="Бонусные баллы сгорели",
                    message=f"С вашего счета списано {int(total_expired)} баллов по истечению срока их действия."
                )
            except Exception as e:
                logger.error(f"Failed to send expiration notification to user {user.id}", exc_info=True)

    logger.info("--- Finished scheduled job: Expire Loyalty Points ---")