# app/services/customer_engagement.py
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.crud import user as crud_user
from app.models.user import User
from app.clients.woocommerce import wc_client
from app.bot.services import notification as bot_notification_service

logger = logging.getLogger(__name__)

# Настройки (можно вынести в config)
DAYS_AFTER_REGISTRATION_TO_ENGAGE = 7 # Отправлять напоминание через 7 дней после регистрации

async def activate_new_users_task():
    """
    Находит пользователей, зарегистрированных ровно 30 дней назад,
    у которых нет покупок, и отправляет им стимулирующее сообщение.
    """
    DAYS_AGO = 30
    logger.info(f"--- Starting job: Activate users registered {DAYS_AGO} days ago ---")
    
    target_date = (datetime.utcnow() - timedelta(days=DAYS_AGO)).date()
    
    with SessionLocal() as db:
        # 1. Находим пользователей, зарегистрированных в целевой день
        users_to_check = crud_user.get_users_registered_on_date(db, target_date=target_date)
        if not users_to_check:
            logger.info("No users found for this registration date.")
            return

        for user in users_to_check:
            try:
                # 2. Проверяем, есть ли у пользователя заказы
                response = await wc_client.get("wc/v3/orders", params={"customer": user.wordpress_id, "per_page": 1})
                total_orders = int(response.headers.get("X-WP-Total", 0))

                # 3. Если заказов НЕТ, отправляем уведомление
                if total_orders == 0:
                    logger.info(f"User {user.id} has no orders. Sending activation message.")
                    # TODO: Создать персональный промокод через API
                    promo_code = "EH7RJXH5" # Заглушка
                    await bot_notification_service.send_activation_notification(db, user, promo_code)
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to process activation for user {user.id}", exc_info=True)

    logger.info("--- Finished job: Activate new users ---")


# --- ЗАДАЧА 2: РЕАКТИВАЦИЯ "СПЯЩИХ" ---
async def reactivate_sleeping_users_task():
    """
    Находит пользователей, чей последний заказ был ровно 90 дней назад,
    и отправляет им реактивационное сообщение.
    """
    DAYS_AGO = 90
    logger.info(f"--- Starting job: Reactivate users with last order {DAYS_AGO} days ago ---")

    # 1. Формируем даты "от" и "до" для поиска (весь день ровно 90 дней назад)
    date_end = datetime.utcnow() - timedelta(days=DAYS_AGO)
    date_start = date_end - timedelta(days=1)
    
    try:
        # 2. Получаем все заказы, сделанные в этот день
        params = {
            "after": date_start.isoformat(),
            "before": date_end.isoformat(),
            "status": "completed", # Ищем только по выполненным заказам
            "per_page": 100 # Обрабатываем до 100 таких заказов в день
        }
        response = await wc_client.get("wc/v3/orders", params=params)
        orders_from_target_day = response.json()
        
        customers_to_check = {order['customer_id'] for order in orders_from_target_day if order.get('customer_id')}

        if not customers_to_check:
            logger.info("No completed orders found for the target date.")
            return

        with SessionLocal() as db:
            for customer_id in customers_to_check:
                # 3. Для каждого покупателя проверяем, не было ли у него БОЛЕЕ НОВЫХ заказов
                params_latest = {
                    "customer": customer_id,
                    "after": date_end.isoformat(), # Ищем заказы ПОСЛЕ нашей целевой даты
                    "per_page": 1
                }
                response_latest = await wc_client.get("wc/v3/orders", params=params_latest)
                latest_orders_count = int(response_latest.headers.get("X-WP-Total", 0))

                # 4. Если более новых заказов НЕТ, это наш "спящий" клиент
                if latest_orders_count == 0:
                    user = crud_user.get_user_by_wordpress_id(db, customer_id)
                    if user:
                        logger.info(f"User {user.id} is a sleeping customer. Sending reactivation message.")
                        # TODO: Создать персональный промокод
                        promo_code = "ACESD6MC" # Заглушка
                        await bot_notification_service.send_reactivation_notification(db, user, promo_code)
                        await asyncio.sleep(0.1)
    
    except Exception as e:
        logger.error("An error occurred during customer reactivation task", exc_info=True)
        
    logger.info("--- Finished job: Reactivate sleeping users ---")