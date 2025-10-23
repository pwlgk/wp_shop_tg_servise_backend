# app/services/review.py

import asyncio
import httpx
import logging
from typing import List
from fastapi import HTTPException, status

from app.clients.woocommerce import wc_client
from app.models.user import User
from app.schemas.product import PaginatedReviews, ProductReviewSchema, ReviewCreateSchema, ReviewImageSchema

logger = logging.getLogger(__name__)

async def get_reviews_for_product(product_id: int, page: int, size: int) -> PaginatedReviews:
    """
    Получает пагинированный список опубликованных отзывов для товара,
    надежно извлекая прикрепленные изображения из кастомного REST API поля.
    """
    try:
        # Шаг 1: Получаем базовый список отзывов
        params = {"product": product_id, "page": page, "per_page": size, "status": "approved"}
        # wc_client.get выбросит исключение в случае ошибки сети или статуса 4xx/5xx
        response = await wc_client.get("wc/v3/products/reviews", params=params)
        
        total_items = int(response.headers.get("X-WP-Total", 0))
        total_pages = int(response.headers.get("X-WP-TotalPages", 0))
        reviews_data = response.json()
        
        if not reviews_data:
            return PaginatedReviews(total_items=0, total_pages=0, current_page=page, size=size, items=[])

        # Шаг 2: Извлекаем ID изображений
        all_image_ids = set()
        for review in reviews_data:
            if review.get("review_image_ids") and isinstance(review["review_image_ids"], list):
                all_image_ids.update(review["review_image_ids"])

        # Шаг 3: Получаем URL'ы для всех найденных ID изображений
        media_url_map = {}
        if all_image_ids:
            image_id_list = list(all_image_ids)
            logger.info(f"Found {len(image_id_list)} unique image IDs in reviews to fetch: {image_id_list}")
            try:
                # Используем .async_client напрямую, так как нам не нужна проверка raise_for_status от wc_client
                media_response = await wc_client.async_client.get(
                    "wp/v2/media", params={"include": ",".join(map(str, image_id_list))}
                )
                media_data = media_response.json()
                for media_item in media_data:
                    media_url_map[media_item["id"]] = media_item.get("source_url")
            except Exception as e:
                logger.error(f"Failed to fetch media for reviews of product {product_id}", exc_info=True)

        # Шаг 4: Собираем финальный ответ
        items = []
        for review_dict in reviews_data:
            image_ids = review_dict.get("review_image_ids", [])
            review_images = [
                ReviewImageSchema(id=img_id, src=media_url_map[img_id])
                for img_id in image_ids if img_id in media_url_map and media_url_map.get(img_id)
            ]
            
            cleaned_text = review_dict.get("review", "").replace("<p>", "").replace("</p>", "").strip()
            
            review_dict["review"] = cleaned_text
            review_dict["images"] = review_images
            items.append(ProductReviewSchema.model_validate(review_dict))

        return PaginatedReviews(
            total_items=total_items, total_pages=total_pages, current_page=page, size=size, items=items
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"WooCommerce API error while fetching reviews for product {product_id}", exc_info=True)
        raise HTTPException(status_code=503, detail="Не удалось загрузить отзывы о товаре.")
    except Exception as e:
        logger.error(f"Failed to fetch reviews for product {product_id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Произошла непредвиденная ошибка при загрузке отзывов.")


async def create_review_for_product(user: User, product_id: int, review_data: ReviewCreateSchema) -> ProductReviewSchema:
    """
    Создает новый отзыв, предварительно проверив, покупал ли пользователь этот товар,
    и передает ID изображений в кастомное REST API поле.
    """
    # Шаг 1: Проверка права на отзыв
    try:
        orders_response = await wc_client.get("wc/v3/orders", params={
            "customer": user.wordpress_id, "product": product_id, "status": "completed", "per_page": 1
        })
        if int(orders_response.headers.get("X-WP-Total", 0)) == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Вы можете оставлять отзывы только на те товары, которые приобрели."
            )
    except httpx.HTTPStatusError:
         raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Не удалось проверить историю ваших покупок.")

    # Шаг 2: Получение данных пользователя
    try:
        customer_response = await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")
        customer_data = customer_response.json()
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Не удалось получить данные вашего профиля.")

    # Шаг 3: Формирование payload
    payload = {
        "product_id": product_id,
        "review": review_data.review,
        "rating": review_data.rating,
        "reviewer": f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip() or user.username or f"User {user.telegram_id}",
        "reviewer_email": customer_data.get('email'),
        "status": "hold",
        "review_image_ids": review_data.image_ids
    }
    
    # --- ИСПРАВЛЕННАЯ ЛОГИКА ОТПРАВКИ И ОБРАБОТКИ ОШИБОК ---
    try:
        # wc_client.post теперь либо вернет dict, либо выбросит httpx.HTTPStatusError
        created_review_data = await wc_client.post("wc/v3/products/reviews", json=payload)

    except httpx.HTTPStatusError as e:
        # Перехватываем ошибку от WooCommerce
        error_details = e.response.json()
        error_code = error_details.get("code", "")
        
        # Проверяем на специфичную ошибку "дубликат отзыва"
        if "duplicate" in error_code:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Вы уже оставляли отзыв на этот товар."
            )
        else:
            # Возвращаем любую другую ошибку от WooCommerce
            raise HTTPException(
                status_code=e.response.status_code,
                detail=error_details.get("message", "Произошла ошибка при создании отзыва.")
            )
    except httpx.RequestError:
        # Перехватываем сетевую ошибку
        logger.error(f"Network error creating review for product {product_id} by user {user.id}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Сервис магазина временно недоступен.")
    # --------------------------------------------------------

    # Шаг 5: Обогащение успешного ответа (без изменений)
    created_review_data['images'] = []
    if review_data.image_ids:
        try:
            media_response = await wc_client.async_client.get(
                "wp/v2/media", 
                params={"include": ",".join(map(str, review_data.image_ids))}
            )
            media_data = media_response.json()
            for media in media_data:
                created_review_data['images'].append(ReviewImageSchema(id=media["id"], src=media["source_url"]))
        except Exception:
            logger.warning(f"Could not fetch media details for newly created review {created_review_data['id']}")

    return ProductReviewSchema.model_validate(created_review_data)


async def check_if_user_can_review(user: User, product_id: int) -> bool:
    """
    Проверяет, покупал ли пользователь данный товар и не оставлял ли уже отзыв.
    Возвращает True, если пользователь может оставить отзыв.
    """
    if not user:
        return False
        
    try:
        # --- Используем asyncio.gather для параллельного выполнения обоих запросов ---
        orders_task = wc_client.get("wc/v3/orders", params={
            "customer": user.wordpress_id,
            "product": product_id,
            "status": "completed",
            "per_page": 1
        })
        
        # --- ИСПРАВЛЕНИЕ: Ищем отзывы по ID покупателя, а не по email ---
        reviews_task = wc_client.get("wc/v3/products/reviews", params={
            "customer": user.wordpress_id,
            "product": product_id
        })

        orders_response, reviews_response = await asyncio.gather(orders_task, reviews_task)
        
        orders_response.raise_for_status()
        reviews_response.raise_for_status()
        
        # --- Анализ результатов ---
        has_purchased = int(orders_response.headers.get("X-WP-Total", 0)) > 0
        has_already_reviewed = len(reviews_response.json()) > 0
        
        return has_purchased and not has_already_reviewed

    except Exception:
        logger.warning(f"Failed to check review eligibility for user {user.id} and product {product_id}", exc_info=True)
        return False