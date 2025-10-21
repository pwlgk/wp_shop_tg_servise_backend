# app/routers/v2/endpoints/cart.py

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from redis.asyncio import Redis

from app.dependencies import get_current_user, get_db
from app.core.redis import get_redis_client
from app.models.user import User
from app.schemas.cart import (
    CartItemUpdate, CartResponse, FavoriteItemUpdate
)
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
    coupon_code: str | None = Query(None, description="Промокод для расчета скидки"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """
    Получение содержимого корзины текущего пользователя.
    Опционально принимает промокод для расчета скидок.
    """
    return await cart_service.get_user_cart(db, redis, current_user, coupon_code)

@router.post("/cart/items", status_code=status.HTTP_200_OK)
async def update_cart_item(
    item_data: CartItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """
    Добавление товара (или его вариации) в корзину или обновление его количества
    с проверкой наличия на складе.
    """
    product = await catalog_service.get_product_by_id(
        db=db,
        redis=redis,
        product_id=item_data.product_id,
        user_id=current_user.id
    )
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=locales.ERROR_PRODUCT_NOT_FOUND_OR_OUT_OF_STOCK
        )
    
    # --- НАЧАЛО ЛОГИКИ ПРОВЕРКИ ВАРИАЦИИ ---
    stock_quantity_to_check = product.stock_quantity
    
    if item_data.variation_id:
        # Если клиент хочет добавить вариацию
        if not product.variations:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Этот товар не имеет опций.")
        
        selected_variation = next((v for v in product.variations if v.id == item_data.variation_id), None)
        
        if not selected_variation or selected_variation.stock_status != 'instock':
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Выбранная опция товара закончилась или не существует.")
        
        stock_quantity_to_check = selected_variation.stock_quantity

    elif product.variations:
        # Если товар вариативный, но клиент не передал variation_id
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Необходимо выбрать опции для этого товара (размер, цвет и т.д.).")
    # --- КОНЕЦ ЛОГИКИ ПРОВЕРКИ ВАРИАЦИИ ---
        
    if stock_quantity_to_check is not None and item_data.quantity > stock_quantity_to_check:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=locales.ERROR_NOT_ENOUGH_STOCK.format(available_quantity=stock_quantity_to_check)
        )

    # --- ГЛАВНОЕ ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # Теперь мы передаем variation_id в функцию сохранения в БД.
    crud_cart.add_or_update_cart_item(
        db, 
        user_id=current_user.id, 
        product_id=item_data.product_id, 
        quantity=item_data.quantity,
        variation_id=item_data.variation_id
    )
    # --------------------------------

    return {"status": "ok", "message": locales.SUCCESS_CART_UPDATED}


@router.delete("/cart/items/{product_id}")
def delete_cart_item(
    product_id: int,
    # --- ДОБАВЛЯЕМ QUERY ПАРАМЕТР ---
    variation_id: int | None = Query(None, description="ID вариации для удаления, если это вариативный товар"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Удаление товара или его конкретной вариации из корзины."""
    # --- ПЕРЕДАЕМ variation_id В CRUD ---
    success = crud_cart.remove_cart_item(db, user_id=current_user.id, product_id=product_id, variation_id=variation_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=locales.ERROR_ITEM_NOT_IN_CART)
    return {"status": "ok", "message": locales.SUCCESS_ITEM_REMOVED_FROM_CART}


# --- Эндпоинты для Избранного (без изменений) ---

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
async def add_favorite(
    item_data: FavoriteItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """Добавление товара в избранное."""
    crud_cart.add_favorite_item(db, user_id=current_user.id, product_id=item_data.product_id)
    
    keys_to_delete = await redis.keys(f"product*user:{current_user.id}")
    keys_to_delete += await redis.keys(f"products*user:{current_user.id}")
    if keys_to_delete:
        await redis.delete(*keys_to_delete)
        logger.info(f"Cache invalidated for user {current_user.id} after adding to favorites.")
        
    return {"status": "ok", "message": locales.SUCCESS_ADDED_TO_FAVORITES}

@router.delete("/favorites/items/{product_id}")
async def remove_favorite(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client)
):
    """Удаление товара из избранного."""
    success = crud_cart.remove_favorite_item(db, user_id=current_user.id, product_id=product_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=locales.ERROR_ITEM_NOT_IN_FAVORITES)
        
    keys_to_delete = await redis.keys(f"product*user:{current_user.id}")
    keys_to_delete += await redis.keys(f"products*user:{current_user.id}")
    if keys_to_delete:
        await redis.delete(*keys_to_delete)
        logger.info(f"Cache invalidated for user {current_user.id} after removing from favorites.")

    return {"status": "ok", "message": locales.SUCCESS_REMOVED_FROM_FAVORITES}