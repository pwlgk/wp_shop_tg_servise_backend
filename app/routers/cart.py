# app/routers/cart.py

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from redis.asyncio import Redis

from app.dependencies import get_current_user, get_db
from app.core.redis import get_redis_client
from app.models.user import User
from app.schemas.cart import (
    CartItemUpdate, CartResponse, FavoriteItemUpdate, FavoriteResponse
)
# Импортируем схемы для пагинации
from app.schemas.product import PaginatedFavorites
from app.crud import cart as crud_cart
from app.services import cart as cart_service
from app.services import catalog as catalog_service
from app.core import locales
logger = logging.getLogger(__name__)

router = APIRouter()


# --- Эндпоинты для Корзины ---

@router.get("/cart", response_model=CartResponse)
async def get_cart(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """Получение содержимого корзины текущего пользователя."""
    return await cart_service.get_user_cart(db, redis, current_user)


@router.post("/cart/items")
async def update_cart_item(
    item_data: CartItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """
    Добавление товара в корзину или обновление его количества
    с проверкой наличия на складе.
    """
    user_id = current_user.id if current_user else None
    product = await catalog_service.get_product_by_id(
        db=db,
        redis=redis,
        product_id=item_data.product_id,
        user_id=user_id
    )
    
    if not product or product.stock_status != 'instock':
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=locales.ERROR_PRODUCT_NOT_FOUND_OR_OUT_OF_STOCK
        )
        
    if product.stock_quantity is not None:
        if item_data.quantity > product.stock_quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=locales.ERROR_NOT_ENOUGH_STOCK.format(available_quantity=product.stock_quantity)
            )

    crud_cart.add_or_update_cart_item(
        db, user_id=current_user.id, product_id=item_data.product_id, quantity=item_data.quantity
    )
    return {"status": "ok", "message": locales.SUCCESS_CART_UPDATED}


@router.delete("/cart/items/{product_id}")
def delete_cart_item(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Удаление товара из корзины."""
    success = crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=product_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=locales.ERROR_ITEM_NOT_IN_CART)
    return {"status": "ok", "message": locales.SUCCESS_ITEM_REMOVED_FROM_CART}


# --- Эндпоинты для Избранного ---

@router.get("/favorites", response_model=PaginatedFavorites)
async def get_favorites(
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """Получение списка избранных товаров."""
    return await cart_service.get_user_favorites(db, redis, current_user, page, size)


@router.post("/favorites/items")
async def add_favorite( # <-- Делаем эндпоинт асинхронным
    item_data: FavoriteItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client) # <-- Добавляем Redis
):
    """Добавление товара в избранное."""
    crud_cart.add_favorite_item(db, user_id=current_user.id, product_id=item_data.product_id)
    
    # --- НОВАЯ ЛОГИКА: ИНВАЛИДАЦИЯ КЕША ---
    # Мы не знаем, на какой странице каталога был этот товар,
    # поэтому проще всего удалить все кешированные страницы каталога для этого пользователя.
    # `product:*` - кеш для отдельных товаров, `products:*` - кеш для списков.
    keys_to_delete = await redis.keys(f"product*user:{current_user.id}")
    keys_to_delete += await redis.keys(f"products*user:{current_user.id}")
    if keys_to_delete:
        await redis.delete(*keys_to_delete)
        logger.info(f"Cache invalidated for user {current_user.id} after adding to favorites.")
    # ------------------------------------
        
    return {"status": "ok", "message": locales.SUCCESS_ADDED_TO_FAVORITES}

@router.delete("/favorites/items/{product_id}")
async def remove_favorite( # <-- Делаем эндпоинт асинхронным
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client) # <-- Добавляем Redis
):
    """Удаление товара из избранного."""
    success = crud_cart.remove_favorite_item(db, user_id=current_user.id, product_id=product_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=locales.ERROR_ITEM_NOT_IN_FAVORITES)
        
    # --- НОВАЯ ЛОГИКА: ИНВАЛИДАЦИЯ КЕША ---
    keys_to_delete = await redis.keys(f"product*user:{current_user.id}")
    keys_to_delete += await redis.keys(f"products*user:{current_user.id}")
    if keys_to_delete:
        await redis.delete(*keys_to_delete)
        logger.info(f"Cache invalidated for user {current_user.id} after removing from favorites.")
    # ------------------------------------

    return {"status": "ok", "message": locales.SUCCESS_REMOVED_FROM_FAVORITES}