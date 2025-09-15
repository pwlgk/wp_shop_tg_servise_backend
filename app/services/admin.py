# app/services/admin.py

import logging
import math
from datetime import datetime
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.core.redis import redis_client

from app.crud import user as crud_user
from app.crud import loyalty as crud_loyalty
from app.clients.woocommerce import wc_client
from app.dependencies import get_db_context
from app.models.user import User
from app.schemas.admin import (
    AdminPromoListItem, AdminUserListItem, PaginatedAdminPromos, PaginatedAdminUsers, AdminUserDetails
)
from app.schemas.cms import Banner
from app.services import settings as settings_service

from app.schemas.order import Order
from app.schemas.loyalty import LoyaltyTransaction
from app.schemas.product import PaginatedOrders, PaginatedResponse
from app.bot.utils.user_display import get_display_name
from app.bot.services import notification as bot_notification_service
from app.schemas.settings import ShopSettings
from app.services.cms import extract_image_url_from_html

logger = logging.getLogger(__name__)


async def get_dashboard_stats(db: Session):
    """Собирает статистику для админской приборной панели."""
    new_users_today = crud_user.count_new_users_today(db)
    total_users = crud_user.count_all_users(db)

    try:
        on_hold_orders_resp = await wc_client.get("wc/v3/orders", params={"status": "on-hold"})
        new_orders_count = int(on_hold_orders_resp.headers.get("X-WP-Total", 0))

        processing_orders_resp = await wc_client.get("wc/v3/orders", params={"status": "processing"})
        processing_orders_count = int(processing_orders_resp.headers.get("X-WP-Total", 0))
        
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat()
        reports_resp = await wc_client.get("wc/v3/reports/sales", params={"date_min": today_start})
        sales_today_data = reports_resp.json()
        sales_today = float(sales_today_data[0].get("total_sales", 0.0)) if sales_today_data else 0.0

    except Exception:
        logger.error("Failed to fetch WooCommerce stats for admin dashboard", exc_info=True)
        new_orders_count = processing_orders_count = sales_today = 0

    return {
        "new_orders_count": new_orders_count,
        "processing_orders_count": processing_orders_count,
        "new_users_today_count": new_users_today,
        "total_users_count": total_users,
        "sales_today": sales_today,
    }


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

async def get_paginated_orders(page: int, size: int, **filters) -> PaginatedOrders:
    """
    Собирает пагинированный список ВСЕХ заказов для админки
    с поддержкой фильтров и поиска.
    """
    params = {
        "page": page,
        "per_page": size,
    }

    # Добавляем фильтры, если они есть
    if filters.get("status"):
        params["status"] = filters["status"]
    if filters.get("search"):
        params["search"] = filters["search"] # WC ищет по ID, email, имени клиента

    try:
        response = await wc_client.get("wc/v3/orders", params=params)
        
        total_items = int(response.headers.get("X-WP-Total", 0))
        total_pages = int(response.headers.get("X-WP-TotalPages", 0))
        orders_data = response.json()
        
        CANCELLABLE_STATUSES = {"pending", "on-hold"}
        for order_dict in orders_data:
            order_dict['can_be_cancelled'] = order_dict.get('status') in CANCELLABLE_STATUSES
            # Добавляем telegram_id для удобства админа
            customer_id = order_dict.get("customer_id")
            if customer_id:
                # Этот вызов можно оптимизировать, если будет медленно
                with get_db_context() as db: 
                    user = crud_user.get_user_by_wordpress_id(db, customer_id)
                    order_dict['customer_telegram_id'] = user.telegram_id if user else None

        validated_orders = [Order.model_validate(o) for o in orders_data]
        
        return PaginatedOrders(
            total_items=total_items, total_pages=total_pages,
            current_page=page, size=size, items=validated_orders
        )
    except Exception as e:
        logger.error("Failed to fetch orders for admin panel", exc_info=True)
        # Возвращаем пустой результат в случае ошибки, чтобы не ломать админку
        return PaginatedOrders(total_items=0, total_pages=0, current_page=page, size=size, items=[])
    

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
