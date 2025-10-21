# app/services/admin.py

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
from bs4 import BeautifulSoup
import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.core.redis import redis_client

from app.crud import user as crud_user
from app.crud import loyalty as crud_loyalty
from app.clients.woocommerce import wc_client
from app.dependencies import get_db_context
from app.models.user import User
from app.schemas.admin import (
    AdminDashboardStats, AdminOrderCustomerInfo, AdminOrderDetails, AdminOrderListItem, AdminPromoListItem, AdminUserListItem, PaginatedAdminOrders, PaginatedAdminPromos, PaginatedAdminUsers, AdminUserDetails
)
from app.schemas.cms import Banner
from app.services import settings as settings_service

from app.schemas.order import Order
from app.schemas.loyalty import LoyaltyTransaction
from app.models.loyalty import LoyaltyTransaction as LoyaltyTransactionModel
from app.schemas.product import PaginatedOrders, PaginatedResponse
from app.bot.utils.user_display import get_display_name
from app.bot.services import notification as bot_notification_service
from app.schemas.settings import ShopSettings, ShopSettingsUpdate
from app.services.cms import extract_image_url_from_html

logger = logging.getLogger(__name__)


async def get_dashboard_stats(db: Session, period: str = "day") -> AdminDashboardStats:
    """Собирает расширенную статистику для админской приборной панели за выбранный период."""
    
    # 1. Определяем временной интервал
    now = datetime.now(timezone.utc)
    if period == "week":
        start_date = now - timedelta(days=now.weekday())
    elif period == "month":
        start_date = now.replace(day=1)
    else: # "day"
        start_date = now
    
    date_min = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    cache_key = f"dashboard_stats:{period}:{date_min.strftime('%Y-%m-%d')}"
    cached_data = await redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Serving dashboard stats for period '{period}' from cache.")
        return AdminDashboardStats.model_validate_json(cached_data)

    logger.info(f"Calculating fresh dashboard stats for period '{period}'.")
    
    # 2. Получаем финансовые метрики из WooCommerce
    revenue = 0.0
    order_count = 0
    items_sold = 0
    try:
        reports_resp = await wc_client.get(
            "wc/v3/reports/sales",
            params={"date_min": date_min.strftime('%Y-%m-%d')}
        )
        sales_data = reports_resp.json()
        if sales_data:
            report = sales_data[0]
            revenue = float(report.get("total_sales", 0.0))
            order_count = int(report.get("total_orders", 0))
            items_sold = int(report.get("total_items", 0))
    except Exception:
        logger.error(f"Failed to fetch WooCommerce sales report for period '{period}'", exc_info=True)

    # 3. Получаем метрики из локальной БД
    new_users = db.query(func.count(User.id)).filter(User.created_at >= date_min).scalar()
    
    points_earned = db.query(func.sum(LoyaltyTransactionModel.points)).filter(
        LoyaltyTransactionModel.created_at >= date_min,
        LoyaltyTransactionModel.points > 0
    ).scalar() or 0
    
    points_spent = abs(db.query(func.sum(LoyaltyTransactionModel.points)).filter(
        LoyaltyTransactionModel.created_at >= date_min,
        LoyaltyTransactionModel.points < 0,
        LoyaltyTransactionModel.type == "order_spend"
    ).scalar() or 0)

    # 4. Собираем и кешируем результат
    stats_data = AdminDashboardStats(
        period=period,
        revenue=revenue,
        avg_order_value=revenue / order_count if order_count > 0 else 0.0,
        order_count=order_count,
        items_sold=items_sold,
        new_users=new_users,
        loyalty_points_earned=int(points_earned),
        loyalty_points_spent=int(points_spent)
    )
    
    # Кешируем на 1 час, используя определенный ранее ключ
    await redis_client.set(cache_key, stats_data.model_dump_json(), ex=3600)
    
    return stats_data


async def get_paginated_users(db: Session, page: int, size: int, **filters) -> PaginatedAdminUsers:
    """Собирает пагинированный список пользователей для админки."""
    skip = (page - 1) * size
    
    users = crud_user.get_users(db, skip=skip, limit=size, **filters)
    total_users = crud_user.count_users_with_filters(db, **filters)
    total_pages = math.ceil(total_users / size) if total_users > 0 else 1
    
    items = []
    for user in users:
        try:
            wc_user_data = (await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")).json()
            display_name = get_display_name(wc_user_data, user)
            
            items.append(AdminUserListItem(
                id=user.id,
                telegram_id=user.telegram_id,
                display_name=display_name,
                username=user.username,
                level=user.level,
                is_blocked=user.is_blocked,
                bot_accessible=user.bot_accessible,
                created_at=user.created_at
            ))
        except Exception:
            logger.warning(f"Could not fetch WC data for user {user.id} in user list.")

    return PaginatedAdminUsers(
        total_items=total_users, total_pages=total_pages,
        current_page=page, size=size, items=items
    )


async def get_user_details(
    db: Session, 
    user_id: int,
    orders_page: int = 1,  # <-- Новый параметр
    points_page: int = 1   # <-- Новый параметр
) -> AdminUserDetails | None:
    """Собирает полную карточку пользователя для админки с пагинацией вложенных списков."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        return None

    # 1. Получаем основные данные
    wc_user_data = (await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")).json()
    display_name = get_display_name(wc_user_data, user)

    # 2. Получаем пагинированную историю заказов
    orders_per_page = 5
    orders_resp = await wc_client.get(
        "wc/v3/orders", 
        params={"customer": user.wordpress_id, "page": orders_page, "per_page": orders_per_page}
    )
    orders_data = orders_resp.json()
    total_orders = int(orders_resp.headers.get("X-WP-Total", 0))
    
    CANCELLABLE_STATUSES = {"pending", "on-hold"}
    for order_dict in orders_data:
        order_dict['can_be_cancelled'] = order_dict.get('status') in CANCELLABLE_STATUSES
    
    # 3. Получаем пагинированную историю баллов
    points_per_page = 10
    points_skip = (points_page - 1) * points_per_page
    loyalty_items = crud_loyalty.get_user_transactions(db, user_id=user.id, skip=points_skip, limit=points_per_page)
    total_loyalty_items = crud_loyalty.count_user_transactions(db, user_id=user.id)
    
    user_details = AdminUserDetails(
        id=user.id, telegram_id=user.telegram_id, display_name=display_name,
        username=user.username, level=user.level, is_blocked=user.is_blocked,
        bot_accessible=user.bot_accessible, created_at=user.created_at,
        wordpress_id=user.wordpress_id, email=wc_user_data.get("email"),
        
        latest_orders=PaginatedResponse[Order](
            total_items=total_orders,
            total_pages=math.ceil(total_orders / orders_per_page) if total_orders > 0 else 1,
            current_page=orders_page, 
            size=orders_per_page,
            items=[Order.model_validate(o) for o in orders_data]
        ),
        loyalty_history=PaginatedResponse[LoyaltyTransaction](
            total_items=total_loyalty_items,
            total_pages=math.ceil(total_loyalty_items / points_per_page) if total_loyalty_items > 0 else 1,
            current_page=points_page, 
            size=points_per_page,
            items=loyalty_items
        )
    )
    return user_details

async def get_paginated_orders(db: Session, page: int, size: int, **filters) -> PaginatedOrders:
# -------------------------
    """
    Собирает пагинированный и УПРОЩЕННЫЙ список всех заказов для админки.
    """
    params = { "page": page, "per_page": size }
    if filters.get("status"): params["status"] = filters["status"]
    if filters.get("search"): params["search"] = filters["search"]

    try:
        response = await wc_client.get("wc/v3/orders", params=params)
        
        total_items = int(response.headers.get("X-WP-Total", 0))
        total_pages = int(response.headers.get("X-WP-TotalPages", 0))
        orders_data = response.json()
        
        items = []
        for order_dict in orders_data:
            customer_id = order_dict.get("customer_id")
            customer_display_name = "Гость"
            customer_telegram_id = None
            
            if customer_id:
                # --- ИЗМЕНЕНИЕ ЗДЕСЬ: используем переданный `db` ---
                user = crud_user.get_user_by_wordpress_id(db, customer_id)
                if user:
                    customer_telegram_id = user.telegram_id
                    customer_display_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or f"ID {user.telegram_id}"

            # Формируем краткую сводку по товарам
            line_items = order_dict.get("line_items", [])
            items_summary_parts = [f"{item['name']} (x{item['quantity']})" for item in line_items]
            items_summary = ", ".join(items_summary_parts)
            # Обрезаем, если слишком длинная
            if len(items_summary) > 70:
                items_summary = items_summary[:67] + "..."

            items.append(AdminOrderListItem(
                id=order_dict["id"],
                number=order_dict["number"],
                status=order_dict["status"],
                created_date=order_dict["date_created"],
                total=order_dict["total"],
                customer_display_name=customer_display_name,
                customer_telegram_id=customer_telegram_id,
                items_summary=items_summary
            ))

        return PaginatedAdminOrders(
            total_items=total_items, total_pages=total_pages,
            current_page=page, size=size, items=items
        )
    except Exception as e:
        logger.error("Failed to fetch orders for admin panel", exc_info=True)
        return PaginatedAdminOrders(total_items=0, total_pages=0, current_page=page, size=size, items=[])
    
async def get_current_shop_settings() -> ShopSettings:
    """Просто проксирует вызов к сервису настроек."""
    # Используем redis_client напрямую, так как он глобальный
    return await settings_service.get_shop_settings(redis_client)


async def get_paginated_promos(page: int, size: int) -> PaginatedAdminPromos:
    """
    Собирает пагинированный и очищенный список всех акций для админки.
    """
    params = {"page": page, "per_page": size, "orderby": "date", "order": "desc"}
    try:
        response = await wc_client.async_client.get("wp/v2/promos", params=params)
        
        total_items = int(response.headers.get("X-WP-Total", 0))
        total_pages = int(response.headers.get("X-WP-TotalPages", 0))
        promos_data = response.json()
        
        items = []
        for promo in promos_data:
            # --- ЛОГИКА ПАРСИНГА И ОЧИСТКИ ---
            title = promo.get("title", {}).get("rendered", "Без заголовка")
            content_html = promo.get("content", {}).get("rendered", "")
            acf_fields = promo.get("acf", {})
            
            # Извлекаем картинку
            image_url = extract_image_url_from_html(content_html)
            
            # Очищаем HTML от тегов, чтобы получить чистый текст
            soup = BeautifulSoup(content_html, "lxml")
            text_content = soup.get_text(separator='\n', strip=True)

            items.append(AdminPromoListItem(
                id=promo["id"],
                title=title,
                status=promo.get("status", "unknown"),
                created_date=promo.get("date"),
                text_content=text_content,
                image_url=image_url,
                target_level=acf_fields.get("promo_target_level", "all"),
                action_url=acf_fields.get("promo_action_url")
            ))
            # ------------------------------------

        return PaginatedAdminPromos(
            total_items=total_items,
            total_pages=total_pages,
            current_page=page,
            size=size,
            items=items
        )
    except Exception as e:
        logger.error("Failed to fetch promos for admin panel", exc_info=True)
        return PaginatedAdminPromos(total_items=0, total_pages=0, current_page=page, size=size, items=[])


async def update_shop_settings(settings_data: ShopSettingsUpdate) -> ShopSettings:
    """
    Обновляет настройки магазина в WordPress и сбрасывает кеш.
    """
    # Преобразуем Pydantic модель в словарь, исключая поля, которые не были переданы (None)
    update_payload = settings_data.model_dump(exclude_unset=True)

    if not update_payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No settings data provided to update."
        )

    try:
        # Отправляем запрос на наш кастомный эндпоинт в WordPress
        await wc_client.post("headless-api/v1/settings", json=update_payload)
        logger.info(f"Successfully sent update request to WordPress for settings: {list(update_payload.keys())}")
    except Exception as e:
        logger.error("Failed to update shop settings in WordPress.", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not update settings in WordPress."
        )

    # КРИТИЧЕСКИ ВАЖНЫЙ ШАГ: принудительно очищаем кеш настроек
    await redis_client.delete("shop_settings")
    logger.info("Shop settings cache has been invalidated.")

    # Запрашиваем и возвращаем свежие настройки, чтобы фронтенд сразу увидел изменения
    return await settings_service.get_shop_settings(redis_client)

async def get_order_details(db: Session, order_id: int) -> Optional[AdminOrderDetails]:
    """
    Получает детальную информацию о заказе из WooCommerce и обогащает ее
    данными о пользователе из локальной БД.
    """
    try:
        # 1. Получаем основные данные о заказе из WooCommerce
        order_response = await wc_client.get(f"wc/v3/orders/{order_id}")
        order_data = order_response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None # Заказ не найден
        logger.error(f"Failed to fetch order details for order ID {order_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch order details from WooCommerce.")

    # 2. Получаем и формируем информацию о клиенте
    customer_wp_id = order_data.get("customer_id")
    customer_info: AdminOrderCustomerInfo
    
    if customer_wp_id and customer_wp_id > 0:
        # Ищем пользователя в нашей БД, чтобы получить telegram_id и наше имя
        user = crud_user.get_user_by_wordpress_id(db, customer_wp_id)
        if user:
            # Если пользователь найден, берем его данные
            display_name = user.first_name or user.username or f"User #{user.id}"
            customer_info = AdminOrderCustomerInfo(
                user_id=user.id,
                wordpress_id=user.wordpress_id,
                telegram_id=user.telegram_id,
                display_name=display_name,
                email=order_data.get("billing", {}).get("email"),
                phone=order_data.get("billing", {}).get("phone")
            )
        else:
            # Пользователь есть в WC, но нет в нашей БД (редкий случай)
            customer_info = AdminOrderCustomerInfo(
                user_id=None,
                wordpress_id=customer_wp_id,
                telegram_id=None,
                display_name=f"{order_data.get('billing', {}).get('first_name')} {order_data.get('billing', {}).get('last_name')}".strip(),
                email=order_data.get("billing", {}).get("email"),
                phone=order_data.get("billing", {}).get("phone")
            )
    else:
        # Это гостевой заказ
        customer_info = AdminOrderCustomerInfo(
            user_id=None,
            wordpress_id=0,
            telegram_id=None,
            display_name=f"Гость ({order_data.get('billing', {}).get('first_name')})",
            email=order_data.get("billing", {}).get("email"),
            phone=order_data.get("billing", {}).get("phone")
        )

    # 3. Собираем и валидируем итоговый объект
    # Добавляем наше обогащенное поле customer_info в исходные данные заказа
    order_data["customer_info"] = customer_info
    
    try:
        # Pydantic сам провалидирует все поля, включая вложенные
        return AdminOrderDetails.model_validate(order_data)
    except Exception as e:
        logger.error(f"Failed to validate order data for order ID {order_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to parse order data.")
    

async def update_order_status(order_id: int, new_status: str) -> Order:
    """Обновляет статус заказа в WooCommerce."""
    try:
        payload = {"status": new_status}
        response_data = await wc_client.post(f"wc/v3/orders/{order_id}", json=payload)
        # Возвращаем Pydantic-модель, чтобы FastAPI мог ее валидировать
        return Order.model_validate(response_data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Order with ID {order_id} not found in WooCommerce.")
        logger.error(f"Error updating order {order_id} status in WooCommerce.", exc_info=True)
        raise HTTPException(status_code=e.response.status_code, detail=f"WooCommerce error: {e.response.text}")

async def create_order_note(order_id: int, note_text: str):
    """Создает приватную заметку для заказа в WooCommerce."""
    try:
        payload = {
            "note": note_text,
            "customer_note": False # False делает заметку приватной (только для админов)
        }
        await wc_client.post(f"wc/v3/orders/{order_id}/notes", json=payload)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Order with ID {order_id} not found in WooCommerce.")
        logger.error(f"Error creating note for order {order_id} in WooCommerce.", exc_info=True)
        raise HTTPException(status_code=e.response.status_code, detail=f"WooCommerce error: {e.response.text}")