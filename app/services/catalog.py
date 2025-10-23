# app/services/catalog.py

import asyncio
import json
import logging
import math
from typing import List, Optional
import httpx
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from app.core.config import settings
from app.clients.woocommerce import wc_client
from app.dependencies import get_db_context
from app.models.user import User
from app.schemas.product import ProductCategory, Product, PaginatedProducts
from app.crud.cart import get_favorite_items
from app.services import review as review_service
from app.crud import cart as crud_cart


logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600  # 10 минут

async def get_all_categories(redis: Redis) -> List[ProductCategory]:
    """
    Получает иерархический список категорий, фильтруя ветки без товаров в наличии,
    и корректно обрабатывает отсутствующие изображения (когда API возвращает `false`).
    """
    cache_key = "categories:hierarchical:all:with_stock_v8"
    
    cached_categories = await redis.get(cache_key)
    if cached_categories:
        logger.info("Serving categories from cache.")
        return [ProductCategory.model_validate(c) for c in json.loads(cached_categories)]
            
    logger.info("--- Starting category tree build process ---")
    
    response = await wc_client.get("wc/v3/products/categories", params={"per_page": 100})
    all_categories_data = response.json()

    if not isinstance(all_categories_data, list):
        logger.warning("Received invalid category list from WooCommerce. Returning empty list.")
        return []
    
    logger.info(f"Fetched {len(all_categories_data)} total categories.")

    categories_map = {}
    root_categories = []

    for cat_data in all_categories_data:
        try:
            image_obj = cat_data.get("image")
            image_src = None
            if isinstance(image_obj, dict):
                image_src = image_obj.get("src")
            
            category_obj = ProductCategory(
                id=cat_data.get('id'),
                name=cat_data.get('name'),
                slug=cat_data.get('slug'),
                image_src=image_src,
                count=cat_data.get('count', 0),
                has_in_stock_products=cat_data.get('has_in_stock_products', False),
                children=[]
            )
            categories_map[category_obj.id] = category_obj
        except Exception as e:
            logger.warning(f"Skipping category due to validation error for cat ID {cat_data.get('id')}", exc_info=True)
            continue

    for cat_data in all_categories_data:
        category_id = cat_data.get('id')
        if category_id not in categories_map: continue
        parent_id = cat_data.get('parent', 0)
        current_category = categories_map[category_id]
        if parent_id == 0:
            root_categories.append(current_category)
        elif parent_id in categories_map:
            # Проверяем, что children - это список, прежде чем добавлять
            if not isinstance(categories_map[parent_id].children, list):
                categories_map[parent_id].children = []
            categories_map[parent_id].children.append(current_category)

    logger.info(f"Built a full tree with {len(root_categories)} root categories.")

    final_tree = _filter_empty_category_branches(root_categories)
    logger.info(f"Filtered tree down to {len(final_tree)} valid root categories.")
    
    await redis.set(cache_key, json.dumps([cat.model_dump(mode='json') for cat in final_tree]), ex=CACHE_TTL_SECONDS)
    
    logger.info("--- Finished category tree build process ---")
    return final_tree


def _is_category_branch_valid(category: ProductCategory) -> bool:
    """Рекурсивно проверяет, является ли ветка категорий 'валидной'."""
    if category.has_in_stock_products:
        return True
    if not category.children:
        return False
    return any(_is_category_branch_valid(child) for child in category.children)


def _filter_empty_category_branches(categories: List[ProductCategory]) -> List[ProductCategory]:
    """Рекурсивно фильтрует дерево категорий, удаляя 'мертвые' ветки."""
    valid_branches = []
    for category in categories:
        if category.children:
            category.children = _filter_empty_category_branches(category.children)
        if _is_category_branch_valid(category):
            valid_branches.append(category)
    return valid_branches


# app/services/catalog.py

async def get_products(
    db: Session,
    redis: Redis,
    page: int,
    size: int,
    user_id: Optional[int] = None,
    sku: Optional[str] = None,
    category: Optional[int] = None,
    tag: Optional[int] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    orderby: Optional[str] = None,
    order: Optional[str] = None,
    featured: Optional[bool] = None
) -> PaginatedProducts:
    """
    Получает пагинированный список товаров В НАЛИЧИИ, обогащая их вариациями,
    миниатюрами, флагом избранного и корректно обрабатывая поиск по SKU.
    """
    
    # --- 1. Формирование ключа кеша ---
    cache_key_parts = [
        "products_v7", f"page:{page}", f"size:{size}", # v7 для инвалидации
        f"sku:{sku}" if sku else "", f"cat:{category}" if category else "",
        f"tag:{tag}" if tag else "", f"search:{search}" if search else "",
        f"minp:{min_price}" if min_price else "", f"maxp:{max_price}" if max_price else "",
        f"orderby:{orderby}" if orderby else "", f"order:{order}" if order else "",
        f"feat:{featured}" if featured else ""
    ]
    base_cache_key = ":".join(filter(None, cache_key_parts))
    cache_key = f"{base_cache_key}:user:{user_id}" if user_id else base_cache_key

    cached_products = await redis.get(cache_key)
    if cached_products:
        logger.info(f"Serving products from cache for key: {cache_key}")
        return PaginatedProducts.model_validate(json.loads(cached_products))

    # --- 2. Получение базовых данных из WooCommerce ---
    products_data: List[dict] = []
    total_items = 0
    total_pages = 0
    
    try:
        if sku:
            # "Умный" поиск по SKU
            logger.info(f"Performing smart SKU search for '{sku}'")
            response = await wc_client.get("wc/v3/products", params={"sku": sku, "stock_status": "instock"})
            response.raise_for_status()
            products_data = response.json()

            if not products_data:
                variations_response = await wc_client.get("wc/v3/products/variations", params={"sku": sku, "stock_status": "instock"})
                variations_response.raise_for_status()
                variations_data = variations_response.json()
                
                if variations_data and isinstance(variations_data, list):
                    parent_id = variations_data[0].get("parent_id")
                    if parent_id:
                        parent_product_response = await wc_client.get(f"wc/v3/products/{parent_id}")
                        parent_product_response.raise_for_status()
                        products_data = [parent_product_response.json()]
            
            total_items = len(products_data)
            total_pages = 1 if total_items > 0 else 0
        else:
            # Стандартный поиск/фильтрация
            params = {"page": page, "per_page": size, "status": "publish", "stock_status": "instock"}
            if category: params["category"] = category
            if tag: params["tag"] = tag
            if search: params["search"] = search
            if min_price is not None: params["min_price"] = min_price
            if max_price is not None: params["max_price"] = max_price
            if orderby in ["date", "id", "title", "price", "popularity", "rating"]: params["orderby"] = orderby
            if order in ["asc", "desc"]: params["order"] = order
            if featured: params["featured"] = featured
            
            logger.info(f"Fetching products from WC with params: {params}")
            response = await wc_client.get("wc/v3/products", params=params)
            response.raise_for_status()
            
            products_data = response.json()
            total_items = int(response.headers.get("X-WP-Total", 0))
            total_pages = int(response.headers.get("X-WP-TotalPages", 0))

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error while fetching products: {e.response.status_code} - {e.response.text}", exc_info=True)
        return PaginatedProducts(total_items=0, total_pages=0, current_page=page, size=size, items=[])
    
    if not isinstance(products_data, list):
        products_data = []

    # --- 3. Обогащение вариативных товаров полными данными ---
    async def fetch_variations(product_id: int):
        try:
            var_response = await wc_client.get(f"wc/v3/products/{product_id}/variations", params={"per_page": 100, "status": "publish", "stock_status": "instock"})
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

    # --- 4. Обогащение миниатюрами ---
    media_ids_to_fetch = set()
    product_to_media_map = {}
    for p_data in products_data:
        media_id = p_data.get("featured_media")
        if not media_id or media_id == 0:
            images = p_data.get("images", [])
            if images and isinstance(images, list) and len(images) > 0:
                media_id = images[0].get("id")
        if media_id and media_id > 0:
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
                thumbnail_url = (
                    (sizes.get("large") or {}).get("source_url") or
                    (sizes.get("medium_large") or {}).get("source_url") or
                    (sizes.get("medium") or {}).get("source_url") or
                    (sizes.get("full") or {}).get("source_url") or  # В крайнем случае берем оригинал
                    media_item.get("source_url")
                )
                if thumbnail_url:
                    media_urls_map[media_item["id"]] = thumbnail_url
        except Exception as e:
            logger.error("Failed to fetch featured media details", exc_info=True)

    # --- 5. Финальная сборка, обогащение "избранным", валидация и фильтрация ---
    favorite_product_ids = {item.product_id for item in get_favorite_items(db, user_id=user_id)} if user_id else set()
    enriched_products = []
    for p_data in products_data:
        # Финальная проверка наличия
        if p_data.get("type") == "variable" and not p_data.get("variations"):
            logger.info(f"Skipping variable product ID {p_data.get('id')} because it has no available variations left.")
            total_items -= 1 # Корректируем счетчик
            continue
        
        # Обогащение миниатюрами
        product_id = p_data.get("id")
        media_id_for_product = product_to_media_map.get(product_id)
        thumbnail_url = media_urls_map.get(media_id_for_product)
        if thumbnail_url:
            p_data["images"] = [{"id": media_id_for_product or 0, "src": thumbnail_url, "alt": ""}]
        elif not p_data.get("images"):
            p_data["images"] = []
        
        try:
            product_obj = Product.model_validate(p_data)
            product_obj.is_favorite = product_obj.id in favorite_product_ids
            enriched_products.append(product_obj)
        except Exception as e:
            logger.warning(f"Failed to validate product data for product ID {p_data.get('id')}", exc_info=True)
            total_items -= 1 # Корректируем счетчик, если товар не прошел валидацию
            
    paginated_result = PaginatedProducts(
        total_items=total_items,
        total_pages=math.ceil(total_items / size) if total_items > 0 else 1,
        current_page=page,
        size=size,
        items=enriched_products
    )
    
    if not sku:
        await redis.set(cache_key, paginated_result.model_dump_json(), ex=CACHE_TTL_SECONDS)
    
    return paginated_result


# async def get_products(
#     db: Session,
#     redis: Redis,
#     page: int,
#     size: int,
#     user_id: Optional[int] = None,
#     sku: Optional[str] = None,
#     category: Optional[int] = None,
#     tag: Optional[int] = None,
#     search: Optional[str] = None,
#     min_price: Optional[float] = None,
#     max_price: Optional[float] = None,
#     orderby: Optional[str] = None,
#     order: Optional[str] = None,
#     featured: Optional[bool] = None
# ) -> PaginatedProducts:
#     """
#     Получает пагинированный список товаров В НАЛИЧИИ, обогащая их вариациями
#     и флагом избранного. Возвращает все изображения в оригинальном качестве.
#     """
    
#     # --- 1. Формирование ключа кеша ---
#     cache_key_parts = [
#         "products_v8", f"page:{page}", f"size:{size}", # v8 для инвалидации
#         f"sku:{sku}" if sku else "", f"cat:{category}" if category else "",
#         f"tag:{tag}" if tag else "", f"search:{search}" if search else "",
#         f"minp:{min_price}" if min_price else "", f"maxp:{max_price}" if max_price else "",
#         f"orderby:{orderby}" if orderby else "", f"order:{order}" if order else "",
#         f"feat:{featured}" if featured else ""
#     ]
#     base_cache_key = ":".join(filter(None, cache_key_parts))
#     cache_key = f"{base_cache_key}:user:{user_id}" if user_id else base_cache_key

#     cached_products = await redis.get(cache_key)
#     if cached_products:
#         logger.info(f"Serving products from cache for key: {cache_key}")
#         return PaginatedProducts.model_validate(json.loads(cached_products))

#     # --- 2. Логика получения данных из WooCommerce ---
#     products_data: List[dict] = []
#     total_items = 0
#     total_pages = 0
    
#     try:
#         if sku:
#             # "Умный" поиск по SKU
#             logger.info(f"Performing smart SKU search for '{sku}'")
#             response = await wc_client.get("wc/v3/products", params={"sku": sku, "stock_status": "instock"})
#             response.raise_for_status()
#             products_data = response.json()

#             if not products_data:
#                 variations_response = await wc_client.get("wc/v3/products/variations", params={"sku": sku, "stock_status": "instock"})
#                 variations_response.raise_for_status()
#                 variations_data = variations_response.json()
                
#                 if variations_data and isinstance(variations_data, list):
#                     parent_id = variations_data[0].get("parent_id")
#                     if parent_id:
#                         parent_product_response = await wc_client.get(f"wc/v3/products/{parent_id}")
#                         parent_product_response.raise_for_status()
#                         products_data = [parent_product_response.json()]
            
#             total_items = len(products_data)
#             total_pages = 1 if total_items > 0 else 0
#         else:
#             # Стандартный поиск/фильтрация
#             params = {"page": page, "per_page": size, "status": "publish", "stock_status": "instock"}
#             if category: params["category"] = category
#             if tag: params["tag"] = tag
#             if search: params["search"] = search
#             if min_price is not None: params["min_price"] = min_price
#             if max_price is not None: params["max_price"] = max_price
#             if orderby in ["date", "id", "title", "price", "popularity", "rating"]: params["orderby"] = orderby
#             if order in ["asc", "desc"]: params["order"] = order
#             if featured: params["featured"] = featured
            
#             logger.info(f"Fetching products from WC with params: {params}")
#             response = await wc_client.get("wc/v3/products", params=params)
#             response.raise_for_status()
            
#             products_data = response.json()
#             total_items = int(response.headers.get("X-WP-Total", 0))
#             total_pages = int(response.headers.get("X-WP-TotalPages", 0))

#     except httpx.HTTPStatusError as e:
#         logger.error(f"HTTP error while fetching products: {e.response.status_code} - {e.response.text}", exc_info=True)
#         return PaginatedProducts(total_items=0, total_pages=0, current_page=page, size=size, items=[])
    
#     if not isinstance(products_data, list):
#         products_data = []

#     # --- 3. Обогащение вариативных товаров полными данными ---
#     async def fetch_variations(product_id: int):
#         try:
#             var_response = await wc_client.get(f"wc/v3/products/{product_id}/variations", params={"per_page": 100, "status": "publish", "stock_status": "instock"})
#             var_response.raise_for_status()
#             return product_id, var_response.json()
#         except Exception:
#             return product_id, []

#     tasks = [fetch_variations(p['id']) for p in products_data if p.get("type") == "variable"]
#     if tasks:
#         results = await asyncio.gather(*tasks)
#         variations_map = dict(results)
#         for p_data in products_data:
#             if p_data.get("id") in variations_map:
#                 p_data["variations"] = variations_map[p_data["id"]]

#     # --- 4. Финальная сборка, обогащение "избранным" и валидация ---
#     favorite_product_ids = {item.product_id for item in get_favorite_items(db, user_id=user_id)} if user_id else set()
#     enriched_products = []
    
#     for p_data in products_data:
#         # Финальная проверка наличия
#         if p_data.get("type") == "variable" and not p_data.get("variations"):
#             logger.info(f"Skipping variable product ID {p_data.get('id')} because it has no available variations left.")
#             total_items -= 1
#             continue
        
#         # --- ЛОГИКА ОБОГАЩЕНИЯ МИНИАТЮРАМИ ПОЛНОСТЬЮ УДАЛЕНА ---
#         # Мы просто доверяем данным, которые пришли от WooCommerce в поле 'images'.
        
#         try:
#             product_obj = Product.model_validate(p_data)
#             product_obj.is_favorite = product_obj.id in favorite_product_ids
#             enriched_products.append(product_obj)
#         except Exception as e:
#             logger.warning(f"Failed to validate product data for product ID {p_data.get('id')}", exc_info=True)
#             total_items -= 1
            
#     paginated_result = PaginatedProducts(
#         total_items=total_items,
#         total_pages=math.ceil(total_items / size) if total_items > 0 else 1,
#         current_page=page,
#         size=size,
#         items=enriched_products
#     )
    
#     if not sku:
#         await redis.set(cache_key, paginated_result.model_dump_json(), ex=CACHE_TTL_SECONDS)
    
#     return paginated_result

async def get_product_by_id(
    db: Session,
    redis: Redis,
    product_id: int,
    user_id: Optional[int] = None
) -> Optional[Product]:
    """
    Получает детальную информацию о товаре по ID, включая все вариации,
    даже если товара нет в наличии. Обогащает ее флагами is_favorite
    и can_review для текущего пользователя.
    """
    cache_key = f"product:{product_id}:user:{user_id}" if user_id else f"product:{product_id}"

    cached_product = await redis.get(cache_key)
    if cached_product:
        # Если в кеше "null", значит товара не существует, возвращаем None
        if cached_product == "null":
            return None
        return Product.model_validate(json.loads(cached_product))
    
    try:
        response = await wc_client.get(f"wc/v3/products/{product_id}")
        
        # --- ИЗМЕНЕНИЕ: Обрабатываем 404, но не фильтруем по наличию ---
        # Если WooCommerce вернул 404, значит товара в принципе не существует
        if response.status_code == 404:
            logger.warning(f"Product with ID {product_id} not found in WooCommerce (404).")
            # Кешируем "пустой" результат, чтобы не запрашивать несуществующий товар снова
            await redis.set(cache_key, "null", ex=CACHE_TTL_SECONDS)
            return None

        response.raise_for_status() # Вызовет ошибку для 5xx или других 4xx
        product_data = response.json()
        
        # Получаем вариации, если они есть
        if product_data.get("type") == "variable":
            variations_response = await wc_client.get(f"wc/v3/products/{product_id}/variations", params={"per_page": 100})
            variations_response.raise_for_status()
            product_data["variations"] = variations_response.json()
        
        # --- БЛОК ПРОВЕРКИ НАЛИЧИЯ УДАЛЕН ---
        # Теперь мы не возвращаем None, если товара нет на складе.
        # Фронтенд сам будет анализировать поле 'stock_status'.

        # Логика обогащения флагами is_favorite и can_review
        is_favorite = False
        can_review = False
        
        if user_id:
            user = None
            with get_db_context() as session:
                user = session.query(User).filter(User.id == user_id).first()

            if user:
                with get_db_context() as session:
                    is_favorite = crud_cart.get_favorite_item(session, user_id=user.id, product_id=product_id) is not None

                can_review = await review_service.check_if_user_can_review(user, product_id)

        product_data["is_favorite"] = is_favorite
        product_data["can_review"] = can_review
        
        product = Product.model_validate(product_data)
        
        await redis.set(cache_key, product.model_dump_json(), ex=CACHE_TTL_SECONDS)
        
        return product
        
    except httpx.HTTPStatusError as e:
        # Ловим другие ошибки, кроме 404
        logger.error(f"HTTP error fetching product by ID {product_id}: {e.response.status_code}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching product by ID {product_id}", exc_info=True)
        return None
    

async def _get_any_product_by_id_from_wc(product_id: int) -> dict | None:
    """Получает 'сырые' данные о товаре, игнорируя кеш и статус наличия."""
    try:
        response = await wc_client.get(f"wc/v3/products/{product_id}")
        return response.json()
    except Exception:
        logger.warning(f"Could not fetch raw product data from WC for product ID {product_id}.")
        return None