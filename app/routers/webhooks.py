# app/routers/webhooks.py

import hmac
import hashlib
import base64
import json
import traceback
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from aiogram.types import Update
from app.bot.services import notification as notification_service # <-- Добавляем импорт
from datetime import datetime, timezone, timedelta
from app.core.redis import get_redis_client
from app.core.config import settings
from app.dependencies import get_db
from app.models.loyalty import LoyaltyTransaction
from app.models.referral import Referral
from app.schemas.cms import PromoWebhookPayload
from app.services import loyalty as loyalty_service
from app.models.user import User
from app.bot.core import bot, dp
from app.crud import referral as crud_referral # <-- Импортируем CRUD для рефералов
from app.crud import loyalty as crud_loyalty   # <-- Импортируем CRUD для лояльности
from app.clients.woocommerce import wc_client
import logging
from app.crud import notification as crud_notification
from app.bot.services import notification as bot_notification_service
from app.services import settings as settings_service
from app.core.redis import redis_client
from app.services import cms as cms_service


logger = logging.getLogger(__name__)
# --- Роутер для Telegram ---
# Будет подключен в main.py БЕЗ префикса
telegram_router = APIRouter()

# --- Роутер для WooCommerce ---
# Будет подключен в main.py С префиксом /internal/webhooks
wc_router = APIRouter()


# --- Зависимость для проверки подписи WooCommerce ---
async def verify_webhook_signature(
    request: Request, 
    x_wc_webhook_signature: str | None = Header(None)
):
    """
    Зависимость для проверки подписи. Пропускает запросы без подписи.
    """
    raw_body = await request.body()
    
    # Если это "пинг" от WooCommerce (пустое тело), подписи не будет. Пропускаем.
    if not raw_body:
        return

    # Если секрет не настроен или не пришел заголовок, тоже пропускаем.
    if not settings.WP_WEBHOOK_SECRET or not x_wc_webhook_signature:
        logger.warning("Webhook secret not configured or signature header missing. Skipping verification.")
        return

    expected_signature = base64.b64encode(hmac.new(settings.WP_WEBHOOK_SECRET.encode('utf-8'), raw_body, hashlib.sha256).digest()).decode()
    
    if not hmac.compare_digest(expected_signature, x_wc_webhook_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )
    logger.debug("Webhook signature verified successfully.")
    
async def verify_promo_webhook_secret(x_webhook_secret: str = Header(...)):
    if x_webhook_secret != settings.WP_PROMO_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid promo webhook secret")


# --- Эндпоинт для Telegram (на отдельном роутере) ---
@telegram_router.post(settings.TELEGRAM_WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str = Header(None)
):
    """
    Принимает обновления от Telegram и передает их в диспетчер aiogram.
    """
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secret token")

    await dp.feed_webhook_update(bot=bot, update=Update(**update))
    return {"status": "ok"}


# --- Эндпоинты для WooCommerce (на отдельном роутере) ---

@wc_router.post("/product-updated", dependencies=[Depends(verify_webhook_signature)])
async def product_updated_webhook(
    request: Request,
    redis: Redis = Depends(get_redis_client)
):
    """
    Веб-хук для обработки обновлений товаров из WooCommerce.
    Инвалидирует (удаляет) кеш для обновленного товара.
    """
    raw_body = await request.body()
    if not raw_body:
        return {"status": "ok", "message": "Empty payload, nothing to do"}

    try:
        product_data = json.loads(raw_body)
        product_id = product_data.get("id")

        if not product_id:
            return {"status": "ok", "message": "Product ID not found in payload"}

        cache_key_user_prefix = f"product:{product_id}:user:*"
        cache_key_anon = f"product:{product_id}"
        
        # Удаляем кеш для всех пользователей и для анонимного
        user_keys = await redis.keys(cache_key_user_prefix)
        if user_keys:
            await redis.delete(*user_keys)
        await redis.delete(cache_key_anon)
        
        logger.info(f"Cache invalidated for product ID: {product_id}")
        return {"status": "ok", "message": f"Cache invalidated for product {product_id}"}
        
    except json.JSONDecodeError:
        return {"status": "ok", "message": "Non-JSON payload received"}
    except Exception as e:
        logger.error(f"Error processing product webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Карта статусов из WooCommerce в человекочитаемый формат
ORDER_STATUS_MAP = { "pending": "Ожидает оплаты", "processing": "В обработке", "on-hold": "На удержании", "completed": "Выполнен", "cancelled": "Отменен", "refunded": "Возвращен", "failed": "Не удался" }

async def check_and_reward_referrer(db: Session, referred_user: User):
    """Проверяет, является ли это первой покупкой реферала, и вознаграждает реферера."""
    referral_link = db.query(Referral).filter(Referral.referred_id == referred_user.id, Referral.status == 'pending').first()
    if not referral_link:
        return

    try:
        orders_response = await wc_client.get("wc/v3/orders", params={"customer": referred_user.wordpress_id, "status": "completed"})
        total_header = orders_response.headers.get("X-WP-Total")
        completed_orders_count = int(total_header) if total_header else 0
    except Exception as e:
        logger.error(f"Could not fetch completed orders count for user {referred_user.id}", exc_info=True)
        return

    if completed_orders_count == 1:
        referrer = referral_link.referrer
        if not referrer:
            return

        shop_settings = await settings_service.get_shop_settings(redis_client)
        bonus_amount = shop_settings.referrer_bonus

        if bonus_amount > 0:
            crud_loyalty.create_transaction(db, user_id=referrer.id, points=bonus_amount, type="referral_earn")
            
            referred_user_name = referred_user.username or f"пользователь с ID {referred_user.telegram_id}"
            
            crud_notification.create_notification(
                db, user_id=referrer.id, type="points_earned",
                title="Бонус за друга!",
                message=f"Ваш друг {referred_user_name} совершил первую покупку. Вам начислено {bonus_amount} баллов!"
            )
            await bot_notification_service.send_referral_bonus(db, referrer, referred_user_name, bonus_amount)
            logger.info(f"Referrer {referrer.id} rewarded with {bonus_amount} points for first purchase of referred user {referred_user.id}")
        
        referral_link.status = "completed"
        db.add(referral_link)
        db.commit()

IGNORED_ORDER_STATUSES_FOR_USER_NOTIFICATION = {
    "checkout-draft", # Технический статус для черновиков
    "trash"           # Заказ удален в корзину
}

@wc_router.post("/order-updated", dependencies=[Depends(verify_webhook_signature)])
async def order_updated_webhook(request: Request, db: Session = Depends(get_db)):
    """Обрабатывает обновления заказов, отправляет уведомления и начисляет бонусы."""
    raw_body = await request.body()
    if not raw_body: return {"status": "ok", "message": "Empty payload."}
    
    try:
        order_data = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"status": "ok", "message": "Invalid JSON payload."}
        
    # logger.info(f"--- Order Updated Webhook Received ---\nBody:\n{json.dumps(order_data, indent=2)}\n------------------------------------")

    order_id, order_status, customer_id = order_data.get("id"), order_data.get("status"), order_data.get("customer_id")
    if not all([order_id, order_status, customer_id]):
        return {"status": "ok", "message": "Missing required fields."}
    
    user = db.query(User).filter(User.wordpress_id == customer_id).first()
    if not user:
        return {"status": "ok", "message": "User not found"}

    try:

        if order_status in IGNORED_ORDER_STATUSES_FOR_USER_NOTIFICATION:
            logger.info(f"Order {order_id} status changed to '{order_status}', which is in the ignored list. Skipping user notification.")
        
        else:
            is_just_created_webhook = not order_data.get("date_paid_gmt") and order_status in ['pending', 'on-hold']

            if not is_just_created_webhook:
                status_title = ORDER_STATUS_MAP.get(order_status, order_status.capitalize())
                await bot_notification_service.send_order_status_update(db, user, order_id, status_title)
                crud_notification.create_notification(
                        db=db,
                        user_id=user.id,
                        type="order_status_update",
                        title=f"Статус заказа №{order_id} обновлен",
                        message=f"Новый статус вашего заказа: {status_title}.",
                        related_entity_id=str(order_id)
                    )
        if order_status == "completed":
            
            # --- НОВАЯ ПРОВЕРКА ---
            # `coupon_lines` - это массив с примененными купонами.
            # Если он не пустой, значит, в заказе была скидка.
            coupon_lines = order_data.get("coupon_lines", [])
            
            if coupon_lines:
                logger.info(f"Order {order_id} was completed with coupons. Skipping cashback accrual.")
            else:
                # Кешбэк начисляется, ТОЛЬКО если не было купонов
                logger.info(f"Order {order_id} was completed without coupons. Accruing cashback.")
                order_total = float(order_data.get("total", "0"))
                points_added = loyalty_service.add_cashback_for_order(db, user, order_total, order_id)
                
                if points_added and points_added > 0:
                    # Уведомления о начислении кешбэка
                    await bot_notification_service.send_points_earned(db, user, points_added, order_id)
                    crud_notification.create_notification(
                        db, user_id=user.id, type="points_earned",
                        title="Кешбэк начислен!", message=f"Вы получили {points_added} бонусных баллов за заказ №{order_id}.",
                        related_entity_id=str(order_id)
                    )
            # --- КОНЕЦ НОВОЙ ПРОВЕРКИ ---

            # Логика начисления реферального бонуса остается без изменений,
            # так как она не зависит от кешбэка.
            await check_and_reward_referrer(db, user)
        elif order_status == "cancelled":
            logger.info(f"Order {order_id} was cancelled. Checking for spent points to refund.")
            
            # 1. Ищем транзакцию списания для этого заказа
            spend_transaction = crud_loyalty.get_spend_transaction_by_order_id(
                db, user_id=user.id, order_id_wc=order_id
            )
            
            if spend_transaction:
                # 2. Если списание было, создаем "возвратную" транзакцию
                points_to_refund = -spend_transaction.points # `points` отрицательное, поэтому `-` делает его положительным
                
                # Проверяем, не был ли уже сделан возврат для этого заказа
                has_refund = db.query(LoyaltyTransaction).filter_by(user_id=user.id, order_id_wc=order_id, type="points_refund").first()
                
                if not has_refund:
                    crud_loyalty.create_transaction(
                        db,
                        user_id=user.id,
                        points=points_to_refund,
                        type="points_refund",
                        order_id_wc=order_id
                    )
                    
                    # 3. Отправляем уведомления (в бот и Mini App)
                    # await bot_notification_service.send_points_refund_notification(db, user, points_to_refund, order_id)
                    crud_notification.create_notification(
                        db, user_id=user.id, type="points_refund",
                        title="Баллы возвращены",
                        message=f"Списанные баллы за отмененный заказ №{order_id} возвращены на ваш счет."
                    )
                    logger.info(f"Refunded {points_to_refund} points to user {user.id} for cancelled order {order_id}.")
                else:
                    logger.warning(f"Refund for order {order_id} already exists. Skipping.")
        
        return {"status": "ok", "message": f"Successfully processed webhook for order {order_id}"}


    except Exception as e:
        logger.error(f"Error during webhook business logic for order {order_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during webhook processing")
    


@wc_router.post("/promo-created", dependencies=[Depends(verify_promo_webhook_secret)])
async def promo_created_webhook(
    payload: PromoWebhookPayload,
    background_tasks: BackgroundTasks,
    # Убираем db: Session = Depends(get_db) отсюда, он не нужен
):
    """
    Веб-хук для обработки публикации новой акции из WordPress.
    Запускает фоновую задачу для создания уведомлений и рассылки.
    """
    logger.info(f"Received promo-created webhook for promo_id: {payload.promo_id}")
    
    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # Передаем ТОЛЬКО promo_id
    background_tasks.add_task(cms_service.process_new_promo, payload.promo_id)
    
    return {"status": "accepted"}

@wc_router.post("/customer-updated", dependencies=[Depends(verify_webhook_signature)])
async def customer_updated_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Синхронизирует данные пользователя (ФИО) при обновлении в WP.
    Устойчив к пустым или невалидным запросам.
    """
    # 1. Получаем "сырое" тело запроса
    raw_body = await request.body()
    
    # 2. Проверяем, что тело не пустое
    if not raw_body:
        logger.info("Received empty webhook payload (likely a ping from WooCommerce). Responding OK.")
        return {"status": "ping_ok"}
    # ---------------------------
    # 3. Безопасно парсим JSON
    try:
        customer_data = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning(f"Received customer-updated webhook with non-JSON payload: {raw_body.decode(errors='ignore')}")
        return {"status": "skipped", "reason": "invalid json"}

    # 4. Извлекаем ID и проверяем, что он есть
    wordpress_id = customer_data.get("id")
    if not wordpress_id:
        logger.warning(f"Received customer-updated webhook without customer ID in payload.")
        return {"status": "skipped", "reason": "id missing"}
        
    # 5. Ищем пользователя и обновляем данные
    user_to_update = db.query(User).filter(User.wordpress_id == wordpress_id).first()
    
    if user_to_update:
        # Обновляем только те поля, которые изменились, чтобы избежать лишних записей в БД
        updated = False
        if customer_data.get("first_name") != user_to_update.first_name:
            user_to_update.first_name = customer_data.get("first_name")
            updated = True
        if customer_data.get("last_name") != user_to_update.last_name:
            user_to_update.last_name = customer_data.get("last_name")
            updated = True
        
        if updated:
            db.commit()
            logger.info(f"Synced user data for WP ID {wordpress_id}")
            return {"status": "ok", "message": "User data synced"}
        else:
            logger.info(f"Webhook for WP ID {wordpress_id} received, but no data changed.")
            return {"status": "ok", "message": "No data changed"}
            
    logger.warning(f"Received customer-updated webhook for WP ID {wordpress_id}, but user not found in local DB.")
    return {"status": "not_found"}