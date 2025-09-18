# app/services/cart.py

import logging
import math
from typing import Optional
from sqlalchemy.orm import Session
from redis.asyncio import Redis
from fastapi import HTTPException

from app.crud import cart as crud_cart
from app.services import catalog as catalog_service
from app.services import settings as settings_service
from app.services import loyalty as loyalty_service
from app.services import coupon as coupon_service
from app.schemas.cart import (
    CartResponse, CartItemResponse, FavoriteResponse, CartStatusNotification
)
from app.schemas.product import PaginatedFavorites
from app.models.user import User

logger = logging.getLogger(__name__)


async def get_user_cart(
    db: Session,
    redis: Redis,
    current_user: User,
    coupon_code: str | None = None
) -> CartResponse:
    """
    Собирает полную информацию о корзине пользователя, включая:
    - "Самоисцеление" (проверка наличия и количества товаров).
    - Расчет максимально возможного списания бонусов.
    - Применение и валидацию промокода.
    """
    # 1. Получаем настройки магазина и "сырое" содержимое корзины из БД
    shop_settings = await settings_service.get_shop_settings(redis)
    cart_items_db = crud_cart.get_cart_items(db, user_id=current_user.id)
    
    response_items = []
    total_items_price = 0.0
    notifications = []
    
    # 2. "Самоисцеление" корзины и расчет "чистой" стоимости
    if cart_items_db:
        for item in cart_items_db:
            product_details = await catalog_service.get_product_by_id(
                db, redis, item.product_id, current_user.id
            )
            
            if not product_details or product_details.stock_status != 'instock' or (product_details.stock_quantity is not None and product_details.stock_quantity == 0):
                crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id)
                notifications.append(CartStatusNotification(
                    level="error",
                    message=f"Товар '{product_details.name if product_details else f'ID {item.product_id}'}' закончился и был удален из корзины."
                ))
                continue

            current_quantity = item.quantity
            if product_details.stock_quantity is not None and item.quantity > product_details.stock_quantity:
                crud_cart.add_or_update_cart_item(db, user_id=current_user.id, product_id=item.product_id, quantity=product_details.stock_quantity)
                notifications.append(CartStatusNotification(
                    level="warning",
                    message=f"Количество товара '{product_details.name}' уменьшено до {product_details.stock_quantity} шт. (остаток на складе)."
                ))
                current_quantity = product_details.stock_quantity

            response_items.append(CartItemResponse(product=product_details, quantity=current_quantity))
            total_items_price += float(product_details.price) * current_quantity

    # 3. Применение купона, если он передан и корзина не пуста
    discount_amount = 0.0
    applied_coupon_code = None
    if coupon_code and response_items:
        try:
            line_items_for_validation = [{"product_id": item.product.id, "quantity": item.quantity} for item in response_items]
            # --- ИСПРАВЛЕНИЕ: Передаем `current_user` в сервис валидации ---
            validated_coupon = await coupon_service.validate_coupon(
                current_user, coupon_code, line_items_for_validation
            )
            discount_amount = validated_coupon.discount_amount
            applied_coupon_code = validated_coupon.code
            
            notifications.append(CartStatusNotification(
                level="success",
                message=f"Промокод '{validated_coupon.code.upper()}' успешно применен! Скидка: {discount_amount} руб."
            ))
        except HTTPException as e:
            notifications.append(CartStatusNotification(level="error", message=e.detail))

    # 4. Финальные расчеты
    final_price = total_items_price - discount_amount
    final_price = max(final_price, 0)
    
    current_balance = loyalty_service.get_user_balance(db, current_user)
    max_points_from_percentage = final_price * (shop_settings.max_points_payment_percentage / 100)
    max_points_to_spend = int(min(current_balance, max_points_from_percentage))
    
    is_min_amount_reached = total_items_price >= shop_settings.min_order_amount

    return CartResponse(
        items=response_items,
        total_items_price=round(total_items_price, 2),
        discount_amount=round(discount_amount, 2),
        final_price=round(final_price, 2),
        notifications=notifications,
        min_order_amount=shop_settings.min_order_amount,
        is_min_amount_reached=is_min_amount_reached,
        max_points_to_spend=max_points_to_spend,
        applied_coupon_code=applied_coupon_code
    )


async def get_user_favorites(
    db: Session, 
    redis: Redis, 
    current_user: User, 
    page: int, 
    size: int
) -> PaginatedFavorites:
    """Собирает пагинированный список избранных товаров."""
    total_items = crud_cart.get_favorite_items_count(db, user_id=current_user.id)
    
    skip = (page - 1) * size
    favorite_items_db = crud_cart.get_favorite_items(db, user_id=current_user.id, skip=skip, limit=size)
    
    response_items = []
    for item in favorite_items_db:
        product_details = await catalog_service.get_product_by_id(
            db=db, redis=redis, product_id=item.product_id, user_id=current_user.id
        )
        if product_details:
            response_items.append(product_details)
            
    return PaginatedFavorites(
        total_items=total_items,
        total_pages=math.ceil(total_items / size) if total_items > 0 else 1,
        current_page=page,
        size=size,
        items=response_items
    )