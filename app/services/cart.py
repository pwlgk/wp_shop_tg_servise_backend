# app/services/cart.py
from sqlalchemy.orm import Session
from redis.asyncio import Redis
import math
from app.crud import cart as crud_cart
from app.schemas.product import PaginatedFavorites
from app.services import catalog as catalog_service
from app.schemas.cart import CartResponse, CartItemResponse, FavoriteResponse, CartStatusNotification
from app.models.user import User
from app.services import settings as settings_service # <-- Добавляем импорт


async def get_user_cart(db: Session, redis: Redis, current_user: User) -> CartResponse:
    # --- 1. Получаем настройки магазина ---
    shop_settings = await settings_service.get_shop_settings(redis)
    
    cart_items_db = crud_cart.get_cart_items(db, user_id=current_user.id)
    
    response_items = []
    total_items_price = 0.0 # <-- Переименовываем
    notifications = []
    
    for item in cart_items_db:
        # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
        # Передаем все аргументы: db, redis, product_id, user_id
        product_details = await catalog_service.get_product_by_id(
            db=db,
            redis=redis,
            product_id=item.product_id,
            user_id=current_user.id
        )
        
        # --- ИСПРАВЛЕННАЯ ЛОГИКА ПРОВЕРКИ ---
        
        # Сценарий 1: Товар не найден или его статус "outofstock" (полностью закончился)
        if not product_details or product_details.stock_status != 'instock':
            crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id)
            notifications.append(CartStatusNotification(
                level="error",
                message=f"Товар '{product_details.name if product_details else f'ID {item.product_id}'}' закончился и был удален из корзины."
            ))
            continue # Переходим к следующему товару, так как с этим делать нечего

        # Инициализируем текущее количество
        current_quantity = item.quantity

        # Сценарий 2: Товар в наличии, но количество на складе отслеживается и оно меньше, чем в корзине
        if product_details.stock_quantity is not None and item.quantity > product_details.stock_quantity:
            # Если остатков 0, то это эквивалентно "outofstock"
            if product_details.stock_quantity == 0:
                crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=item.product_id)
                notifications.append(CartStatusNotification(
                    level="error",
                    message=f"Товар '{product_details.name}' закончился и был удален из корзины."
                ))
                continue
            
            # Если остатки есть, но их меньше - уменьшаем количество в корзине
            crud_cart.add_or_update_cart_item(
                db, user_id=current_user.id, product_id=item.product_id, quantity=product_details.stock_quantity
            )
            notifications.append(CartStatusNotification(
                level="warning",
                message=f"Количество товара '{product_details.name}' уменьшено до {product_details.stock_quantity} шт. (остаток на складе)."
            ))
            current_quantity = product_details.stock_quantity

        # --- КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ ---
        
        response_items.append(
            CartItemResponse(product=product_details, quantity=current_quantity)
        )
        total_items_price += float(product_details.price) * current_quantity

    is_min_amount_reached = total_items_price >= shop_settings.min_order_amount

    return CartResponse(
        items=response_items, 
        total_items_price=round(total_items_price, 2), # <-- Переименовываем
        notifications=notifications,
        min_order_amount=shop_settings.min_order_amount, # <-- Новое
        is_min_amount_reached=is_min_amount_reached # <-- Новое
    )

async def get_user_favorites(db: Session, redis: Redis, current_user: User, page: int, size: int) -> PaginatedFavorites:
    # 1. Получаем общее количество для пагинации
    total_items = crud_cart.get_favorite_items_count(db, user_id=current_user.id)
    
    # 2. Получаем пагинированный список
    skip = (page - 1) * size
    favorite_items_db = crud_cart.get_favorite_items(db, user_id=current_user.id, skip=skip, limit=size)
    
    response_items = []
    for item in favorite_items_db:
        product_details = await catalog_service.get_product_by_id(
            db=db, redis=redis, product_id=item.product_id, user_id=current_user.id
        )
        if product_details:
            response_items.append(product_details)
            
    # 3. Формируем пагинированный ответ
    return PaginatedFavorites(
        total_items=total_items,
        total_pages=math.ceil(total_items / size),
        current_page=page,
        size=size,
        items=response_items
    )