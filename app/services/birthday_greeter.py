# app/services/birthday_greeter.py
import logging
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.crud import user as crud_user
from app.crud import loyalty as crud_loyalty
from app.crud import notification as crud_notification
from app.services import settings as settings_service
from app.bot.services import notification as bot_notification_service
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

async def check_birthdays_task():
    """
    Фоновая задача: находит именинников, начисляет им бонусы
    и отправляет поздравления.
    """
    logger.info("--- Starting scheduled job: Birthday Greeter ---")
    
    with SessionLocal() as db:
        try:
            users_with_birthday = crud_user.get_users_with_birthday_today(db)
            if not users_with_birthday:
                logger.info("No users with birthday today.")
                return

            logger.info(f"Found {len(users_with_birthday)} user(s) with birthday today.")
            
            shop_settings = await settings_service.get_shop_settings(redis_client)
            bonus_amount = shop_settings.birthday_bonus_amount

            if bonus_amount <= 0:
                logger.info("Birthday bonus is disabled (amount is 0).")
                return

            for user in users_with_birthday:
                logger.info(f"Processing birthday for user {user.id}")
                # Начисляем бонус
                crud_loyalty.create_transaction(
                    db, user_id=user.id, points=bonus_amount, type="promo_birthday"
                )
                
                # Создаем уведомление в Mini App
                crud_notification.create_notification(
                    db, user_id=user.id, type="points_earned",
                    title="С Днем Рождения!",
                    message=f"Поздравляем! Мы начислили вам {bonus_amount} подарочных баллов."
                )
                # Отправляем уведомление в бот (нужно создать эту функцию)
                await bot_notification_service.send_birthday_greeting(db, user, bonus_amount)

        except Exception as e:
            logger.error("An error occurred during birthday greeter task", exc_info=True)

    logger.info("--- Finished scheduled job: Birthday Greeter ---")