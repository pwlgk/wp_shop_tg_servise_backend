# app/routers/v1/review.py

from fastapi import APIRouter, Depends, Query, status
from typing import List

from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.product import ProductReviewSchema, ReviewCreateSchema, PaginatedReviews
from app.services import review as review_service

router = APIRouter(prefix="/products/{product_id}/reviews", tags=["Reviews"])

@router.get("", response_model=PaginatedReviews)
async def get_product_reviews(
    product_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(5, ge=1, le=50)
):
    """Получение пагинированного списка отзывов для товара."""
    return await review_service.get_reviews_for_product(product_id, page, size)

@router.post("", response_model=ProductReviewSchema, status_code=status.HTTP_201_CREATED)
async def create_product_review(
    product_id: int,
    review_data: ReviewCreateSchema,
    current_user: User = Depends(get_current_user),
):
    """
    Создание нового отзыва для товара.
    Доступно только авторизованным пользователям, которые ранее приобрели этот товар.
    """
    return await review_service.create_review_for_product(current_user, product_id, review_data)