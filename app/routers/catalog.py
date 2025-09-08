# app/routers/catalog.py

from fastapi import APIRouter, Depends, Query, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.redis import get_redis_client
from app.dependencies import get_db, get_optional_current_user
from app.models.user import User
from app.schemas.product import ProductCategory, Product, PaginatedProducts
from app.services import catalog as catalog_service

router = APIRouter()


@router.get("/categories", response_model=List[ProductCategory])
async def get_categories(redis: Redis = Depends(get_redis_client)):
    """
    Получение списка всех категорий товаров.
    Этот эндпоинт публичный и не требует аутентификации.
    """
    return await catalog_service.get_all_categories(redis)


@router.get("/products", response_model=PaginatedProducts)
async def get_all_products(
    # Параметры пагинации
    page: int = Query(1, ge=1, description="Номер страницы"),
    size: int = Query(20, ge=1, le=100, description="Количество товаров на странице"),
    
    # Параметры фильтрации
    category: Optional[int] = Query(None, description="ID категории для фильтрации"),
    tag: Optional[int] = Query(None, description="ID метки (тега) для фильтрации"),
    search: Optional[str] = Query(None, description="Поисковый запрос"),
    min_price: Optional[float] = Query(None, ge=0, description="Минимальная цена"),
    max_price: Optional[float] = Query(None, ge=0, description="Максимальная цена"),
    
    # Параметры сортировки
    orderby: Optional[str] = Query(None, description="Поле для сортировки: date, price, popularity, rating, title"),
    order: Optional[str] = Query("desc", description="Направление сортировки: asc, desc"),
    
    # Прочие фильтры
    featured: Optional[bool] = Query(None, description="Показывать только рекомендуемые товары"),
    
    # Зависимости
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Получение списка товаров с пагинацией, фильтрацией, поиском и сортировкой.
    Если пользователь авторизован, товары будут помечены флагом is_favorite.
    """
    user_id = current_user.id if current_user else None
    
    return await catalog_service.get_products(
        db=db,
        redis=redis,
        user_id=user_id,
        page=page,
        size=size,
        category=category,
        tag=tag,
        search=search,
        min_price=min_price,
        max_price=max_price,
        orderby=orderby,
        order=order,
        featured=featured
    )


@router.get("/products/{product_id}", response_model=Product)
async def get_single_product(
    product_id: int,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis_client),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Получение детальной информации о товаре.
    Если пользователь авторизован, товар будет помечен флагом is_favorite.
    """
    user_id = current_user.id if current_user else None
    
    product = await catalog_service.get_product_by_id(db, redis, product_id, user_id)
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Product not found"
        )
        
    return product