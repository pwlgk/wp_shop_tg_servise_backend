# app/services/order.py

import math
from typing import Optional, List
from fastapi import HTTPException, status
import httpx
from sqlalchemy.orm import Session
from redis.asyncio import Redis
from app.bot.services import notification as notification_service
from app.clients.woocommerce import wc_client
from app.crud import cart as crud_cart
from app.crud import loyalty as crud_loyalty
from app.dependencies import get_db_context
from app.models.loyalty import LoyaltyTransaction
from app.models.user import User
from app.services import catalog as catalog_service
from app.services import loyalty as loyalty_service
from app.services import settings as settings_service
from app.schemas.order import Order, OrderCreate
from app.schemas.product import PaginatedOrders
import logging
from app.core.redis import redis_client # Глобальный клиент Redis


logger = logging.getLogger(__name__)
# Список статусов, при которых возможна отмена заказа пользователем
CANCELLABLE_STATUSES = {"pending", "on-hold"}



async def create_order_from_cart(
    db: Session,
    redis: Redis,
    current_user: User,
    order_data: OrderCreate
) -> Order:
    """
    Создает заказ в WooCommerce на основе корзины пользователя.
    Выполняет все проверки: наличие, баланс баллов, % списания, мин. сумма.
    """
    # 1. Получаем настройки магазина и содержимое корзины
    shop_settings = await settings_service.get_shop_settings(redis)
    cart_items_db = crud_cart.get_cart_items(db, user_id=current_user.id)

    if not cart_items_db:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Корзина пуста.")

    # 2. ФИНАЛЬНАЯ ПРОВЕРКА НАЛИЧИЯ и расчет "чистой" стоимости товаров
    line_items_for_wc = []
    unavailable_items = []
    total_items_price = 0.0

    for item in cart_items_db:
        await redis.delete(f"product:{item.product_id}:user:{current_user.id}")
        await redis.delete(f"product:{item.product_id}")

        product_details = await catalog_service.get_product_by_id(
            db=db,
            redis=redis,
            product_id=item.product_id,
            user_id=current_user.id
        )

        if not product_details or product_details.stock_status != 'instock':
            unavailable_items.append(product_details.name if product_details else f"ID {item.product_id}")
            continue

        if product_details.stock_quantity is not None and item.quantity > product_details.stock_quantity:
            unavailable_items.append(f"{product_details.name} (в наличии: {product_details.stock_quantity})")
            continue
        
        line_items_for_wc.append({"product_id": item.product_id, "quantity": item.quantity})
        total_items_price += float(product_details.price) * item.quantity

    if unavailable_items:
        error_detail = "Некоторые товары закончились или их количество изменилось: " + ", ".join(unavailable_items)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_detail)

    # 3. ПРОВЕРКА УСЛОВИЙ ОПЛАТЫ И ЗАКАЗА (ДО ТРАНЗАКЦИИ)

    # 3.1 Проверка минимальной суммы заказа
    if total_items_price < shop_settings.min_order_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Минимальная сумма заказа - {shop_settings.min_order_amount} руб."
        )

    # 3.2 Проверка условий списания бонусов
    if order_data.points_to_spend > 0:
        # Проверяем максимальный процент списания
        max_points_to_spend = total_items_price * (shop_settings.max_points_payment_percentage / 100)
        if order_data.points_to_spend > max_points_to_spend:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Бонусами можно оплатить не более {int(max_points_to_spend)} ({shop_settings.max_points_payment_percentage}%) от стоимости товаров."
            )
        
        # Проверяем, что на балансе достаточно баллов (эта проверка менее критична здесь,
        # так как `spend_points` сделает это атомарно, но она обеспечивает быстрый отказ)
        current_balance = loyalty_service.get_user_balance(db, current_user)
        if order_data.points_to_spend > current_balance:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недостаточно бонусных баллов для списания.")

    # 4. ОСНОВНАЯ ТРАНЗАКЦИЯ
    db.begin_nested()
    try:
        # Шаг 4.1: Атомарно списываем баллы
        if order_data.points_to_spend > 0:
            loyalty_service.spend_points(
                db, user=current_user,
                points_to_spend=order_data.points_to_spend,
                order_id_wc=0
            )

        # Шаг 4.2: Формируем и отправляем заказ в WordPress
        customer_data_response = await wc_client.get(f"wc/v3/customers/{current_user.wordpress_id}")
        customer_data = customer_data_response.json()
        
        order_payload = {
            "customer_id": current_user.wordpress_id,
            "line_items": line_items_for_wc,
            "billing": customer_data.get("billing"),
            "shipping": customer_data.get("shipping"),
            "payment_method_id": order_data.payment_method_id,
            "points_to_spend": order_data.points_to_spend,
            "coupon_code": order_data.coupon_code
        }
        
        created_order_data = await wc_client.post("headless-api/v1/orders", json=order_payload)
        new_order_id = created_order_data['id']

        # Шаг 4.3: Обновляем транзакцию списания реальным ID заказа
        if order_data.points_to_spend > 0:
            transaction_to_update = db.query(LoyaltyTransaction).filter_by(
                user_id=current_user.id, order_id_wc=0, type="order_spend"
            ).order_by(LoyaltyTransaction.id.desc()).first()
            if transaction_to_update:
                transaction_to_update.order_id_wc = new_order_id

        # Шаг 4.4: Очищаем корзину
        crud_cart.clear_cart(db, user_id=current_user.id)

        # Шаг 4.5: Коммитим транзакцию
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Transaction rolled back. Error creating order for user {current_user.id}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Не удалось создать заказ.")

    # 5. Валидация ответа и отправка уведомлений
    validated_order = Order.model_validate(created_order_data)
    
    await notification_service.send_new_order_confirmation(db, current_user, validated_order)
    await notification_service.send_new_order_to_admin(validated_order, current_user)

    return validated_order

async def get_user_orders(
    current_user: User,
    page: int,
    size: int,
    status: Optional[str] = None
) -> PaginatedOrders:
    """
    Получает историю заказов пользователя из WooCommerce, обогащая ее
    изображениями товаров и флагом возможности отмены.
    """
    # 1. Формируем параметры для WooCommerce API
    params = {
        "customer": current_user.wordpress_id,
        "page": page,
        "per_page": size
    }
    
    if status:
        allowed_statuses = {"any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"}
        requested_statuses = {s.strip() for s in status.split(',')}
        if requested_statuses.issubset(allowed_statuses):
            params["status"] = status
        else:
            logger.warning(f"Invalid order statuses requested: {status}")

    response = await wc_client.get("wc/v3/orders", params=params)
    
    # Заголовки ответа могут быть неточными, если мы фильтруем вручную, но это лучший вариант
    total_items = int(response.headers.get("X-WP-Total", 0))
    total_pages = int(response.headers.get("X-WP-TotalPages", 0))
    orders_data = response.json()
    
    # 2. Фильтруем "сырые" данные на нашей стороне (как запасной и основной вариант)
    filtered_orders_data = [
        order for order in orders_data 
        if order.get("status") != "checkout-draft"
    ]
    
    # 3. Собираем ID всех уникальных товаров из отфильтрованных заказов
    all_product_ids = set()
    if filtered_orders_data and isinstance(filtered_orders_data, list):
        for order in filtered_orders_data:
            for item in order.get("line_items", []):
                if item.get("product_id"):
                    all_product_ids.add(item['product_id'])

    # 4. Получаем детальную информацию для всех этих товаров
    product_details_map = {}
    if all_product_ids:
        for product_id in all_product_ids:
            product_data = await catalog_service._get_any_product_by_id_from_wc(product_id)
            if product_data:
                images = product_data.get("images")
                if images and isinstance(images, list) and len(images) > 0:
                    product_details_map[product_id] = images[0].get("src")

    # 5. "Обогащаем" отфильтрованные данные
    for order_dict in filtered_orders_data:
        order_dict['can_be_cancelled'] = order_dict.get('status') in CANCELLABLE_STATUSES
        for item in order_dict.get("line_items", []):
            item['image_url'] = product_details_map.get(item.get('product_id'))
            
    # 6. Валидируем и формируем ответ
    try:
        validated_orders = [Order.model_validate(order) for order in filtered_orders_data]
    except Exception as e:
        logger.error("Failed to validate enriched order data", exc_info=True)
        validated_orders = []
    
    return PaginatedOrders(
        total_items=total_items,
        total_pages=total_pages,
        current_page=page,
        size=size,
        items=validated_orders
    )

async def cancel_order(db: Session, order_id: int, current_user: User) -> Order: # <-- Добавляем db

    """Отменяет заказ пользователя, если это разрешено."""
    try:
        order_response = await wc_client.get(f"wc/v3/orders/{order_id}")
        order_data = order_response.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заказ не найден.")

    if order_data.get("customer_id") != current_user.wordpress_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для отмены этого заказа.")

    current_status = order_data.get("status")
    if current_status not in CANCELLABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Заказ в статусе '{current_status}' не может быть отменен.")

    update_payload = {"status": "cancelled"}
    
    try:
        updated_order_response = await wc_client.post(f"wc/v3/orders/{order_id}", json=update_payload)
        updated_order_data = updated_order_response
    except Exception as e:
        logger.error(f"Error cancelling order {order_id} in WooCommerce: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось отменить заказ.")

    updated_order_data['can_be_cancelled'] = updated_order_data.get('status') in CANCELLABLE_STATUSES
    validated_order = Order.model_validate(updated_order_data)
    
    # --- ОТПРАВКА УВЕДОМЛЕНИЯ ---
    await notification_service.send_order_cancellation_confirmation(
        db, current_user, validated_order.id
    )

    await notification_service.send_order_cancellation_to_admin(
        order_id=validated_order.id,
        user=current_user
    )

    return validated_order

async def get_payment_gateways():
    """Получает список активных способов оплаты из WooCommerce."""
    response = await wc_client.get("wc/v3/payment_gateways")
    gateways_data = response.json()
    
    enabled_gateways = [
        {
            "id": gw.get("id"),
            "title": gw.get("title"),
            "description": gw.get("description")
        }
        for gw in gateways_data if gw.get("enabled")
    ]
    return enabled_gateways


async def get_order_details(order_id: int, current_user: User) -> Order | None:
    """
    Получает детальную информацию о конкретном заказе,
    обогащая ее изображениями товаров (включая те, что не в наличии)
    и флагом возможности отмены.
    """
    # 1. Получаем "сырые" данные о заказе от WooCommerce
    try:
        response = await wc_client.get(f"wc/v3/orders/{order_id}")
        order_data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None # Заказ не найден
        logger.error(f"Failed to fetch order details for order ID {order_id}", exc_info=True)
        raise

    # 2. Проверяем, не является ли это техническим черновиком
    if order_data.get("status") == "checkout-draft":
        logger.warning(f"User {current_user.id} tried to access a draft order {order_id}.")
        return None

    # 3. Проверяем права доступа: принадлежит ли заказ текущему пользователю
    if order_data.get("customer_id") != current_user.wordpress_id:
        logger.warning(f"User {current_user.id} tried to access order {order_id} belonging to another customer.")
        return None

    # 4. Собираем ID товаров для "обогащения"
    all_product_ids = {
        item.get('product_id') 
        for item in order_data.get("line_items", []) 
        if item.get('product_id')
    }

    # 5. Получаем детали товаров, включая те, что не в наличии
    product_details_map = {}
    if all_product_ids:
        for product_id in all_product_ids:
            product_data = await catalog_service._get_any_product_by_id_from_wc(product_id)
            if product_data:
                images = product_data.get("images")
                if images and isinstance(images, list) and len(images) > 0:
                    product_details_map[product_id] = images[0].get("src")

    # 6. "Склеиваем" данные
    order_data['can_be_cancelled'] = order_data.get('status') in CANCELLABLE_STATUSES
    for item in order_data.get("line_items", []):
        item['image_url'] = product_details_map.get(item.get('product_id'))

    # 7. Валидируем и возвращаем результат
    try:
        return Order.model_validate(order_data)
    except Exception as e:
        logger.error(f"Failed to validate enriched single order data for order ID {order_id}", exc_info=True)
        return None