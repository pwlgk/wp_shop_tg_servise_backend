# app/services/order.py

import asyncio
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
    Создает заказ в WooCommerce, используя надежный двухфазный механизм ("Сага")
    для списания бонусных баллов.
    """
    # --- Шаг 1: Получение настроек и содержимого корзины ---
    shop_settings = await settings_service.get_shop_settings(redis)
    cart_items_db = crud_cart.get_cart_items(db, user_id=current_user.id)

    if not cart_items_db:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Корзина пуста.")

    # --- Шаг 2: Финальная проверка наличия и расчет стоимости ---
    line_items_for_wc = []
    unavailable_items = []
    total_items_price = 0.0

    for item in cart_items_db:
        product_details = await catalog_service.get_product_by_id(db, redis, item.product_id, user_id=current_user.id)

        if not product_details:
            unavailable_items.append(f"ID {item.product_id} (товар удален)")
            continue
        
        # Логика проверки для вариаций и простых товаров
        item_price_str: str
        item_stock_quantity: int | None

        if item.variation_id:
            if not product_details.variations:
                unavailable_items.append(f"'{product_details.name}' (опции изменились)")
                continue
            selected_variation = next((v for v in product_details.variations if v.id == item.variation_id), None)
            if not selected_variation or selected_variation.stock_status != 'instock':
                unavailable_items.append(f"'{product_details.name}' (выбранная опция закончилась)")
                continue
            item_price_str = selected_variation.price
            item_stock_quantity = selected_variation.stock_quantity
            line_items_for_wc.append({"variation_id": item.variation_id, "quantity": item.quantity})
        else:
            if product_details.variations:
                unavailable_items.append(f"'{product_details.name}' (не выбрана опция)")
                continue
            if product_details.stock_status != 'instock':
                unavailable_items.append(f"'{product_details.name}'")
                continue
            item_price_str = product_details.price
            item_stock_quantity = product_details.stock_quantity
            line_items_for_wc.append({"product_id": item.product_id, "quantity": item.quantity})

        if item_stock_quantity is not None and item.quantity > item_stock_quantity:
            unavailable_items.append(f"'{product_details.name}' (в наличии: {item_stock_quantity} шт.)")
            continue
        
        try:
            total_items_price += float(item_price_str) * item.quantity
        except (ValueError, TypeError):
            unavailable_items.append(f"'{product_details.name}' (некорректная цена)")

    if unavailable_items:
        error_detail = "Некоторые товары закончились или их количество изменилось: " + ", ".join(unavailable_items)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_detail)

    # --- Шаг 3: Проверка бизнес-правил (мин. сумма, % списания) ---
    if total_items_price < shop_settings.min_order_amount:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Минимальная сумма заказа - {shop_settings.min_order_amount} руб.")

    if order_data.points_to_spend > 0:
        max_points_to_spend = total_items_price * (shop_settings.max_points_payment_percentage / 100)
        if order_data.points_to_spend > max_points_to_spend:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Бонусами можно оплатить не более {int(max_points_to_spend)} ({shop_settings.max_points_payment_percentage}%) от стоимости товаров.")

    # --- Шаг 4: Фаза 1 - Резервирование баллов ---
    pending_transaction: LoyaltyTransaction | None = None
    if order_data.points_to_spend > 0:
        try:
            with db.begin_nested():
                pending_transaction = loyalty_service.spend_points(
                    db, user=current_user, points_to_spend=order_data.points_to_spend,
                    order_id_wc=None, is_pending=True
                )
            db.commit()
        except HTTPException as e:
            db.rollback()
            raise e
        except Exception:
            db.rollback()
            logger.error(f"Failed to create pending spend transaction for user {current_user.id}", exc_info=True)
            raise HTTPException(status_code=500, detail="Ошибка при резервировании баллов.")

    # --- Шаг 5: Фаза 2 - Создание заказа и Подтверждение/Отмена ---
    try:
        customer_data_response = await wc_client.get(f"wc/v3/customers/{current_user.wordpress_id}")
        customer_data_response.raise_for_status()
        customer_data = customer_data_response.json()
        
        order_payload = {
            "customer_id": current_user.wordpress_id, "line_items": line_items_for_wc,
            "billing": customer_data.get("billing"), "shipping": customer_data.get("shipping"),
            "payment_method_id": order_data.payment_method_id,
            "points_to_spend": order_data.points_to_spend, "coupon_code": order_data.coupon_code
        }
        
        response = await wc_client.post("headless-api/v1/orders", json=order_payload)
        if response.status_code != status.HTTP_201_CREATED:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("message", "Магазин не смог создать заказ."))

        created_order_data = response.json()
        new_order_id = created_order_data['id']

        # Сценарий Успеха: Подтверждаем списание
        if pending_transaction:
            with get_db_context() as session:
                tx_to_confirm = session.get(LoyaltyTransaction, pending_transaction.id)
                if tx_to_confirm and tx_to_confirm.type == 'order_pending_spend':
                    tx_to_confirm.type = "order_spend"
                    tx_to_confirm.order_id_wc = new_order_id
                    session.commit()
        
        with get_db_context() as session:
            crud_cart.clear_cart(session, user_id=current_user.id)

    except Exception as e:
        # Сценарий Неудачи: Отменяем резервирование
        logger.error(f"Order creation failed for user {current_user.id} after points were reserved. Refunding.", exc_info=True)
        if pending_transaction:
            with get_db_context() as session:
                tx_to_cancel = session.get(LoyaltyTransaction, pending_transaction.id)
                # Проверяем, что мы еще не отменили этот резерв
                if tx_to_cancel and tx_to_cancel.type == 'order_pending_spend':
                    # Создаем компенсирующую транзакцию
                    crud_loyalty.create_transaction(
                        session, user_id=current_user.id, points=abs(tx_to_cancel.points),
                        type="spend_refund", related_transaction_id=tx_to_cancel.id
                    )
                    # Меняем тип "подвисшей" транзакции, чтобы она не была обработана чистильщиком
                    tx_to_cancel.type = "order_spend_failed"
                    session.commit()
        
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Не удалось создать заказ. Если вы списывали баллы, они были возвращены на ваш счет.")
    
    # --- Шаг 6: Уведомления и возврат результата ---
    validated_order = Order.model_validate(created_order_data)
    
    # Запускаем уведомления в фоне, чтобы не задерживать ответ пользователю
    try:
        asyncio.create_task(notification_service.send_new_order_confirmation(db, current_user, validated_order))
        asyncio.create_task(notification_service.send_new_order_to_admin(validated_order, current_user))
    except Exception as e:
        logger.error(f"Failed to dispatch order notifications for order {validated_order.id}", exc_info=True)

    return validated_order

async def _enrich_orders_with_images(orders_data: List[dict]) -> List[dict]:
    """
    Вспомогательная функция для обогащения списка заказов изображениями
    товаров и их вариаций.
    """
    if not orders_data:
        return []

    # Шаг 1: Собираем ID товаров и вариаций
    all_product_ids = set()
    # Теперь будем хранить пары (product_id, variation_id)
    variation_tuples_to_fetch = set()

    for order in orders_data:
        for item in order.get("line_items", []):
            product_id = item.get("product_id")
            variation_id = item.get("variation_id")
            if product_id:
                all_product_ids.add(product_id)
            if product_id and variation_id:
                variation_tuples_to_fetch.add((product_id, variation_id))
    
    # Шаг 2: Получаем детали параллельно
    product_details_map = {}
    variation_details_map = {}

    async def fetch_product_details(product_id: int):
        data = await catalog_service._get_any_product_by_id_from_wc(product_id)
        if data:
            product_details_map[product_id] = data

    # --- ИСПРАВЛЕНИЕ: Функция теперь принимает product_id ---
    async def fetch_variation_details(product_id: int, variation_id: int):
        try:
            # Используем правильный URL
            response = await wc_client.get(f"wc/v3/products/{product_id}/variations/{variation_id}")
            response.raise_for_status() # Вызовет исключение при 404
            variation_details_map[variation_id] = response.json()
        except Exception:
            logger.warning(f"Could not fetch details for variation ID {variation_id} (parent product ID {product_id})")

    tasks = [fetch_product_details(pid) for pid in all_product_ids]
    # Создаем задачи с двумя аргументами
    tasks.extend([fetch_variation_details(pid, vid) for pid, vid in variation_tuples_to_fetch])
    
    if tasks:
        await asyncio.gather(*tasks)

    # Шаг 3: "Склеиваем" данные (без изменений)
    for order_dict in orders_data:
        for item in order_dict.get("line_items", []):
            image_url = None
            variation_id = item.get("variation_id")
            product_id = item.get("product_id")

            if variation_id and variation_id in variation_details_map:
                image_data = variation_details_map[variation_id].get("image")
                if image_data and isinstance(image_data, dict):
                    image_url = image_data.get("src")
            
            if not image_url and product_id and product_id in product_details_map:
                images = product_details_map[product_id].get("images")
                if images and isinstance(images, list) and len(images) > 0:
                    image_url = images[0].get("src")
            
            item['image_url'] = image_url
            
    return orders_data


async def get_user_orders(
    current_user: User,
    page: int,
    size: int,
    status: Optional[str] = None
) -> PaginatedOrders:
    """
    Получает историю заказов пользователя, корректно обогащая ее
    изображениями вариаций товаров.
    """
    params = {
        "customer": current_user.wordpress_id,
        "page": page,
        "per_page": size
    }
    if status:
        params["status"] = status

    response = await wc_client.get("wc/v3/orders", params=params)
    response.raise_for_status()
    
    total_items = int(response.headers.get("X-WP-Total", 0))
    total_pages = int(response.headers.get("X-WP-TotalPages", 0))
    orders_data = response.json()
    
    # Фильтруем технические черновики
    filtered_orders_data = [
        order for order in orders_data 
        if order.get("status") != "checkout-draft"
    ]
    
    # Обогащаем отфильтрованные заказы изображениями
    enriched_orders_data = await _enrich_orders_with_images(filtered_orders_data)
    
    for order_dict in enriched_orders_data:
        order_dict['can_be_cancelled'] = order_dict.get('status') in CANCELLABLE_STATUSES
            
    try:
        validated_orders = [Order.model_validate(order) for order in enriched_orders_data]
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


async def get_order_details(order_id: int, current_user: User) -> Order | None:
    """
    Получает детальную информацию о конкретном заказе, корректно
    обогащая ее изображениями вариаций товаров.
    """
    try:
        response = await wc_client.get(f"wc/v3/orders/{order_id}")
        response.raise_for_status()
        order_data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404: return None
        logger.error(f"Failed to fetch order details for order ID {order_id}", exc_info=True)
        raise

    if order_data.get("status") == "checkout-draft" or order_data.get("customer_id") != current_user.wordpress_id:
        return None

    # Обогащаем один заказ, передав его как список
    enriched_orders_data = await _enrich_orders_with_images([order_data])
    if not enriched_orders_data:
        return None
        
    enriched_order_data = enriched_orders_data[0]
    enriched_order_data['can_be_cancelled'] = enriched_order_data.get('status') in CANCELLABLE_STATUSES

    try:
        return Order.model_validate(enriched_order_data)
    except Exception as e:
        logger.error(f"Failed to validate enriched single order data for order ID {order_id}", exc_info=True)
        return None


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
    # await notification_service.send_order_cancellation_confirmation(
    #     db, current_user, validated_order.id
    # )

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

