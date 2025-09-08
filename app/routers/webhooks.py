# app/routers/webhooks.py

import hmac
import hashlib
import base64
import json
import traceback
from fastapi import APIRouter, Request, Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from aiogram.types import Update
from app.bot.services import notification as notification_service # <-- Добавляем импорт
from datetime import datetime, timezone, timedelta
from app.core.redis import get_redis_client
from app.core.config import settings
from app.dependencies import get_db
from app.services import loyalty as loyalty_service
from app.models.user import User
from app.bot.core import bot, dp
from app.crud import referral as crud_referral # <-- Импортируем CRUD для рефералов
from app.crud import loyalty as crud_loyalty   # <-- Импортируем CRUD для лояльности
from app.clients.woocommerce import wc_client
import logging

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
    """Зависимость для проверки подписи веб-хука WooCommerce."""
    if not settings.WP_WEBHOOK_SECRET or not x_wc_webhook_signature:
        logger.info("Webhook secret not configured or signature header missing. Skipping verification.")
        return

    raw_body = await request.body()
    
    expected_signature = base64.b64encode(
        hmac.new(settings.WP_WEBHOOK_SECRET.encode('utf-8'), raw_body, hashlib.sha256).digest()
    ).decode()
    
    if not hmac.compare_digest(expected_signature, x_wc_webhook_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )


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
ORDER_STATUS_MAP = {
    "pending": "Ожидает оплаты",
    "processing": "В обработке",
    "on-hold": "На удержании",
    "completed": "Выполнен",
    "cancelled": "Отменен",
    "refunded": "Возвращен",
    "failed": "Не удался"
}

async def check_and_reward_referrer(db: Session, referred_user: User):
    """
    Проверяет, является ли это первой покупкой реферала, и вознаграждает реферера.
    """
    # 1. Проверяем, был ли этот пользователь приглашен и связь еще в статусе 'pending'
    referral_link = crud_referral.get_referral_by_referred_id(db, referred_id=referred_user.id)
    
    if not referral_link or referral_link.status != 'pending':
        return # Этот пользователь не реферал, или бонус уже был начислен

    # 2. Проверяем, действительно ли это первый ВЫПОЛНЕННЫЙ заказ пользователя
    try:
        orders_response = await wc_client.get("wc/v3/orders", params={"customer": referred_user.wordpress_id, "status": "completed"})
        # Заголовки могут быть строкой или None, нужно безопасно преобразовать
        total_header = orders_response.headers.get("X-WP-Total")
        completed_orders_count = int(total_header) if total_header else 0
    except Exception as e:
        logger.info(f"Could not fetch completed orders count for user {referred_user.id}: {e}")
        return

    if completed_orders_count == 1:
        # Это первая покупка!
        referrer = referral_link.referrer
        if not referrer:
            return

        # Начисляем бонус рефереру (можно вынести в settings.py)
        REFERRAL_BONUS_POINTS = 100
        crud_loyalty.create_transaction(
            db, user_id=referrer.id, points=REFERRAL_BONUS_POINTS, type="referral_earn"
        )
        
        # Обновляем статус реферальной связи, чтобы бонус не начислился снова
        referral_link.status = "completed"
        db.add(referral_link)
        db.commit()

        # Отправляем уведомление рефереру!
        referred_user_name = referred_user.username or f"пользователь с ID {referred_user.telegram_id}"
        await notification_service.send_referral_bonus(db, referrer, referred_user_name, REFERRAL_BONUS_POINTS)
        logger.info(f"Referrer {referrer.id} rewarded for the first purchase of referred user {referred_user.id}")


@wc_router.post("/order-updated", dependencies=[Depends(verify_webhook_signature)])
async def order_updated_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Веб-хук для обработки обновлений заказов из WooCommerce.
    Отправляет уведомления, начисляет кешбэк и реферальные бонусы.
    """
    raw_body = await request.body()
    
    if not raw_body:
        return {"status": "ok", "message": "Empty payload."}
    try:
        order_data = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"status": "ok", "message": "Invalid JSON payload."}
        
    # logger.info(f"--- Order Updated Webhook Received ---\nBody:\n{json.dumps(order_data, indent=2)}\n------------------------------------")

    order_id = order_data.get("id")
    order_status = order_data.get("status")
    customer_id = order_data.get("customer_id")
    
    if not all([order_id, order_status, customer_id]):
        return {"status": "ok", "message": "Missing required fields."}
    
    user = db.query(User).filter(User.wordpress_id == customer_id).first()
    if not user:
        return {"status": "ok", "message": "User not found"}

    try:
        is_just_created_webhook = not order_data.get("date_paid_gmt") and order_status in ['pending', 'on-hold']

        if not is_just_created_webhook:
            status_title = ORDER_STATUS_MAP.get(order_status, order_status.capitalize())
            await notification_service.send_order_status_update(db, user, order_id, status_title)

        if order_status == "completed":
            order_total_str = order_data.get("total", "0")
            order_total = float(order_total_str) if order_total_str else 0.0
            
            # Начисляем кешбэк покупателю
            points_added = loyalty_service.add_cashback_for_order(db, user, order_total, order_id)
            if points_added and points_added > 0:
                await notification_service.send_points_earned(db, user, points_added, order_id)
            
            # Проверяем и начисляем бонус рефереру
            await check_and_reward_referrer(db, user)
        
        return {"status": "ok", "message": f"Successfully processed webhook for order {order_id}"}

    except Exception as e:
        logger.error(f"Error during webhook business logic for order {order_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error during webhook processing")