# app/services/birthday_greeter.py

import logging
from sqlalchemy.orm import Session
import httpx

from app.db.session import SessionLocal
from app.crud import user as crud_user
from app.crud import loyalty as crud_loyalty
from app.crud import notification as crud_notification
from app.services import settings as settings_service
from app.bot.services import notification as bot_notification_service
from app.core.redis import redis_client
from app.clients.woocommerce import wc_client
from app.models.user import User

logger = logging.getLogger(__name__)


async def check_birthdays_task():
    """
    Фоновая задача: находит "именинников", проверяет выполнение условий
    (старый пользователь, есть выполненный заказ), начисляет бонусы и поздравляет.
    """
    logger.info("--- Starting scheduled job: Birthday Greeter ---")
    
    with SessionLocal() as db:
        try:
            # 1. Находим "кандидатов": у кого сегодня ДР и кто не новый
            candidate_users = crud_user.get_users_with_birthday_today(db)
            
            if not candidate_users:
                logger.info("No eligible users with birthday today found.")
                return

            logger.info(f"Found {len(candidate_users)} candidate(s) with birthday today. Verifying order history...")
            
            shop_settings = await settings_service.get_shop_settings(redis_client)
            bonus_amount = shop_settings.birthday_bonus_amount

            if bonus_amount <= 0:
                logger.info("Birthday bonus is disabled (amount is 0). Skipping.")
                return

            for user in candidate_users:
                try:
                    # 2. Проверяем, есть ли у пользователя хотя бы 1 выполненный заказ
                    orders_response = await wc_client.get(
                        "wc/v3/orders",
                        params={
                            "customer": user.wordpress_id,
                            "status": "completed",
                            "per_page": 1 # Нам не нужен список, только сам факт наличия
                        }
                    )
                    # X-WP-Total - самый надежный способ узнать общее количество
                    completed_orders_count = int(orders_response.headers.get("X-WP-Total", 0))

                    if completed_orders_count > 0:
                        logger.info(f"User {user.id} is eligible for a birthday bonus. Granting {bonus_amount} points.")
                        
                        # 3. Начисляем бонус
                        crud_loyalty.create_transaction(
                            db, user_id=user.id, points=bonus_amount, type="promo_birthday"
                        )
                        
                        # 4. Создаем уведомления
                        crud_notification.create_notification(
                            db, user_id=user.id, type="points_earned",
                            title="С Днем Рождения!",
                            message=f"Поздравляем! Мы начислили вам {bonus_amount} подарочных баллов."
                        )
                        await bot_notification_service.send_birthday_greeting(db, user, bonus_amount)
                    else:
                        logger.info(f"Skipping birthday bonus for user {user.id}: no completed orders found.")

                except httpx.HTTPStatusError as e:
                     logger.error(f"Failed to check order history for user {user.id} due to API error.", exc_info=True)
                except Exception as e:
                    logger.error(f"An unexpected error occurred while processing birthday for user {user.id}", exc_info=True)

        except Exception as e:
            logger.error("An error occurred during birthday greeter task", exc_info=True)

    logger.info("--- Finished scheduled job: Birthday Greeter ---")