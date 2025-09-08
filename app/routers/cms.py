# app/routers/cms.py
from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from typing import List
from app.schemas.cms import Banner, StructuredPage
from app.core.redis import get_redis_client
from app.schemas.cms import Banner, Page
from app.services import cms as cms_service

router = APIRouter()

@router.get("/banners", response_model=List[Banner])
async def get_banners(redis: Redis = Depends(get_redis_client)):
    """Получение списка активных баннеров для главной страницы."""
    return await cms_service.get_active_banners(redis)


@router.get("/pages/{slug}", response_model=StructuredPage) # <-- Меняем response_model
async def get_page(slug: str, redis: Redis = Depends(get_redis_client)):
    """
    Получение контента информационной страницы по ее URL-адресу (slug).
    Примеры slug: 'delivery', 'privacy-policy', 'contacts'.
    """
    page = await cms_service.get_page_by_slug(redis, slug)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    return page