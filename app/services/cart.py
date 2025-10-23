# app/services/cart.py

import asyncio
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
    CartResponse, CartItemResponse, CartStatusNotification
)
from app.schemas.product import PaginatedFavorites, ProductVariationSchema, Product
from app.models.user import User
from app.clients.woocommerce import wc_client
from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_user_cart(
    db: Session,
    redis: Redis,
    current_user: User,
    coupon_code: str | None = None
) -> CartResponse:
    """
    Собирает полную информацию о корзине пользователя, включая:
    - "Самоисцеление" (проверка наличия и количества простых товаров и их вариаций).
    - Расчет максимально возможного списания бонусов.
    - Применение и валидацию промокода.
    """
    shop_settings = await settings_service.get_shop_settings(redis)
    cart_items_db = crud_cart.get_cart_items(db, user_id=current_user.id)
    
    response_items = []
    total_items_price = 0.0
    notifications = []
    
    if cart_items_db:
        for item in cart_items_db:
            product_details = await catalog_service.get_product_by_id(
                db, redis, item.product_id, current_user.id
            )
            
            if not product_details:
                crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id, variation_id=item.variation_id)
                notifications.append(CartStatusNotification(
                    level="error",
                    message=f"Товар с ID {item.product_id} был удален из магазина и убран из корзины."
                ))
                continue

            selected_variation: Optional[ProductVariationSchema] = None
            item_price_str = product_details.price
            item_stock_quantity = product_details.stock_quantity
            item_stock_status = product_details.stock_status

            if item.variation_id:
                if not product_details.variations:
                    crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id, variation_id=item.variation_id)
                    notifications.append(CartStatusNotification(
                        level="error", message=f"Опции для товара '{product_details.name}' изменились. Товар удален из корзины."
                    ))
                    continue

                variation_found = False
                for v in product_details.variations:
                    if v.id == item.variation_id:
                        selected_variation = v
                        variation_found = True
                        break
                
                if not variation_found or selected_variation.stock_status != 'instock':
                    crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id, variation_id=item.variation_id)
                    notifications.append(CartStatusNotification(
                        level="error", message=f"Выбранная опция для товара '{product_details.name}' закончилась и была удалена из корзины."
                    ))
                    continue

                item_price_str = selected_variation.price
                item_stock_quantity = selected_variation.stock_quantity
                item_stock_status = selected_variation.stock_status
            
            elif product_details.variations:
                 crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id, variation_id=item.variation_id)
                 notifications.append(CartStatusNotification(
                     level="warning", message=f"Пожалуйста, выберите опции (размер, цвет и т.д.) для товара '{product_details.name}'. Он был удален из корзины."
                 ))
                 continue
            
            if item_stock_status != 'instock':
                 crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id, variation_id=item.variation_id)
                 notifications.append(CartStatusNotification(
                     level="error", message=f"Товар '{product_details.name}' закончился и был удален из корзины."
                 ))
                 continue

            # --- НАЧАЛО ГЛАВНОГО ИСПРАВЛЕНИЯ ---
            # Прежде чем считать стоимость, убедимся, что цена не пустая строка.
            # Если цена пустая или невалидная, мы считаем ее равной 0.0 и пропускаем
            # эту позицию при расчете, чтобы избежать падения всего запроса.
            try:
                current_item_price = float(item_price_str)
            except (ValueError, TypeError):
                logger.warning(
                    f"Could not convert price '{item_price_str}' to float for product ID {item.product_id} "
                    f"(variation ID: {item.variation_id}). Treating as 0."
                )
                current_item_price = 0.0
            # --- КОНЕЦ ГЛАВНОГО ИСПРАВЛЕНИЯ ---
            
            current_quantity = item.quantity
            if item_stock_quantity is not None and item.quantity > item_stock_quantity:
                crud_cart.add_or_update_cart_item(db, user_id=current_user.id, product_id=item.product_id, quantity=item_stock_quantity, variation_id=item.variation_id)
                notifications.append(CartStatusNotification(
                    level="warning",
                    message=f"Количество товара '{product_details.name}' уменьшено до {item_stock_quantity} шт. (остаток на складе)."
                ))
                current_quantity = item_stock_quantity

            response_items.append(CartItemResponse(
                product=product_details, 
                quantity=current_quantity,
                variation=selected_variation
            ))
            # Используем уже проверенную и преобразованную цену
            total_items_price += current_item_price * current_quantity

    # ... (остальная часть функции остается без изменений) ...

    # 3. Применение купона, если он передан и корзина не пуста
    discount_amount = 0.0
    applied_coupon_code = None
    if coupon_code and response_items:
        try:
            line_items_for_validation = []
            for res_item in response_items:
                line_items_for_validation.append({
                    "product_id": res_item.product.id,
                    "quantity": res_item.quantity,
                    "variation_id": res_item.variation.id if res_item.variation else None
                })

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
    """
    Собирает пагинированный список избранных товаров, используя унифицированную
    логику обогащения, аналогичную get_products.
    """
    
    # Шаг 1: Получаем пагинированный список ID избранных товаров из нашей БД
    skip = (page - 1) * size
    favorite_items_db = crud_cart.get_favorite_items(db, user_id=current_user.id, skip=skip, limit=size)
    total_items = crud_cart.get_favorite_items_count(db, user_id=current_user.id)

    if not favorite_items_db:
        return PaginatedFavorites(total_items=0, total_pages=0, current_page=page, size=size, items=[])

    product_ids_to_fetch = [item.product_id for item in favorite_items_db]

    # Шаг 2: Делаем ОДИН пакетный запрос к WooCommerce для получения данных всех товаров
    try:
        response = await wc_client.get("wc/v3/products", params={
            "include": ",".join(map(str, product_ids_to_fetch)),
            "per_page": size
        })
        response.raise_for_status()
        products_data = response.json()
    except Exception:
        logger.error(f"Failed to fetch favorite products details for user {current_user.id}", exc_info=True)
        return PaginatedFavorites(total_items=0, total_pages=0, current_page=page, size=size, items=[])

    # --- Шаг 3: Применяем ту же самую логику обогащения, что и в get_products ---
    
    # 3.1 Обогащение вариациями
    async def fetch_variations(product_id: int):
        try:
            var_response = await wc_client.get(f"wc/v3/products/{product_id}/variations", params={"per_page": 100, "status": "publish"})
            var_response.raise_for_status()
            return product_id, var_response.json()
        except Exception:
            return product_id, []

    tasks = [fetch_variations(p['id']) for p in products_data if p.get("type") == "variable"]
    if tasks:
        results = await asyncio.gather(*tasks)
        variations_map = dict(results)
        for p_data in products_data:
            if p_data.get("id") in variations_map:
                p_data["variations"] = variations_map[p_data["id"]]

    # 3.2 Обогащение миниатюрами
    media_ids_to_fetch = set()
    product_to_media_map = {}
    for p_data in products_data:
        media_id = p_data.get("featured_media") or (p_data.get("images")[0].get("id") if p_data.get("images") else 0)
        if media_id:
            media_ids_to_fetch.add(media_id)
            product_to_media_map[p_data["id"]] = media_id
    
    media_urls_map = {}
    if media_ids_to_fetch:
        try:
            media_url = f"{settings.WP_URL}/wp-json/wp/v2/media"
            media_params = {"include": ",".join(map(str, list(media_ids_to_fetch))), "per_page": len(media_ids_to_fetch)}
            media_response = await wc_client.async_client.get(media_url, params=media_params)
            media_response.raise_for_status()
            media_data = media_response.json()
            for media_item in media_data:
                sizes = media_item.get("media_details", {}).get("sizes", {})
                optimal_image_url = (sizes.get("large") or {}).get("source_url") or \
                                    (sizes.get("medium_large") or {}).get("source_url") or \
                                    (sizes.get("medium") or {}).get("source_url") or \
                                    (sizes.get("full") or {}).get("source_url") or \
                                    media_item.get("source_url")
                if optimal_image_url:
                    media_urls_map[media_item["id"]] = optimal_image_url
        except Exception:
            logger.error("Failed to fetch featured media details for favorites", exc_info=True)

    # --- Шаг 4: Финальная сборка ---
    enriched_products = []
    for p_data in products_data:
        product_id = p_data.get("id")
        media_id_for_product = product_to_media_map.get(product_id)
        optimal_url = media_urls_map.get(media_id_for_product)
        
        if optimal_url:
            p_data["images"] = [{"id": media_id_for_product or 0, "src": optimal_url, "alt": ""}]
        elif not p_data.get("images"):
            p_data["images"] = []
        
        try:
            product_obj = Product.model_validate(p_data)
            # Все товары в этом списке - избранные по определению
            product_obj.is_favorite = True 
            enriched_products.append(product_obj)
        except Exception as e:
            logger.warning(f"Failed to validate favorite product data for product ID {p_data.get('id')}", exc_info=True)
            
    return PaginatedFavorites(
        total_items=total_items,
        total_pages=math.ceil(total_items / size) if total_items > 0 else 1,
        current_page=page,
        size=size,
        items=enriched_products
    )