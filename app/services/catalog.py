# app/services/catalog.py

import asyncio
import json
import logging
from typing import List, Optional
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from app.core.config import settings
from app.clients.woocommerce import wc_client
from app.schemas.product import ProductCategory, Product, PaginatedProducts
from app.crud.cart import get_favorite_items  # Импортируем CRUD-функцию для избранного

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600  # 10 минут

async def get_all_categories(redis: Redis) -> List[ProductCategory]:
    """
    Получает иерархический список категорий, фильтруя ветки без товаров в наличии.
    """
    cache_key = "categories:hierarchical:all:with_stock_v6"
    
    cached_categories = await redis.get(cache_key)
    if cached_categories:
        logger.info("Serving categories from cache.")
        return [ProductCategory.model_validate(cat) for cat in json.loads(cached_categories)]
            
    logger.info("--- Starting category tree build process ---")
    
    # 1. Получаем ВСЕ категории ОДНИМ запросом
    logger.info("Fetching all categories from WooCommerce in a flat list...")
    response = await wc_client.get("wc/v3/products/categories", params={"per_page": 100})
    all_categories_data = response.json()

    if not all_categories_data:
        logger.warning("Received empty category list from WooCommerce. Returning empty list.")
        return []
    
    logger.info(f"Fetched {len(all_categories_data)} total categories.")

    # 2. Строим полное дерево в памяти
    categories_map = {}
    root_categories_data = []

    for cat_data in all_categories_data:
        try:
            category_obj = ProductCategory.model_validate(cat_data)
            categories_map[category_obj.id] = category_obj
        except Exception as e:
            logger.warning(f"Skipping category due to validation error for cat ID {cat_data.get('id')}: {e}")
            continue

    for cat_data in all_categories_data:
        category_id = cat_data.get('id')
        if category_id not in categories_map:
            continue
            
        parent_id = cat_data.get('parent', 0)
        current_category = categories_map[category_id]
        
        if parent_id == 0:
            root_categories_data.append(current_category)
        elif parent_id in categories_map:
            parent_category = categories_map[parent_id]
            # Убедимся, что children - это список
            if not isinstance(parent_category.children, list):
                parent_category.children = []
            parent_category.children.append(current_category)

    logger.info(f"Built a full tree with {len(root_categories_data)} root categories.")

    # 3. Рекурсивно фильтруем "мертвые" ветки
    final_tree = _filter_empty_category_branches(root_categories_data)
    logger.info(f"Filtered tree down to {len(final_tree)} valid root categories.")
    
    # 4. Кешируем и возвращаем результат
    await redis.set(cache_key, json.dumps([cat.model_dump(mode='json') for cat in final_tree]), ex=CACHE_TTL_SECONDS)
    
    logger.info("--- Finished category tree build process ---")
    return final_tree


def _is_category_branch_valid(category: ProductCategory) -> bool:
    """
    Рекурсивно проверяет, является ли ветка категорий "валидной".
    Ветка валидна, если сама категория имеет товары в наличии,
    ИЛИ хотя бы одна из ее дочерних веток валидна.
    """
    # Логируем проверку для текущей категории
    logger.debug(f"Checking validity for category '{category.name}' (ID: {category.id}). Has stock: {category.has_in_stock_products}")

    if category.has_in_stock_products:
        logger.debug(f"Category '{category.name}' is valid because it has products in stock.")
        return True
    
    if not category.children:
        logger.debug(f"Category '{category.name}' is invalid because it has no stock and no children.")
        return False
        
    # Рекурсивно проверяем детей
    is_any_child_valid = any(_is_category_branch_valid(child) for child in category.children)
    if is_any_child_valid:
        logger.debug(f"Category '{category.name}' is valid because it has at least one valid child branch.")
    else:
        logger.debug(f"Category '{category.name}' is invalid because it has no stock and all its children are invalid.")
        
    return is_any_child_valid


def _filter_empty_category_branches(categories: List[ProductCategory]) -> List[ProductCategory]:
    """
    Рекурсивно фильтрует дерево категорий, удаляя "мертвые" ветки.
    """
    valid_branches = []
    for category in categories:
        # Сначала всегда фильтруем детей
        if category.children:
            category.children = _filter_empty_category_branches(category.children)
        
        # Теперь, когда дети "очищены", проверяем, валидна ли текущая ветка
        if _is_category_branch_valid(category):
            valid_branches.append(category)
            
    return valid_branches

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
    Получает пагинированный список товаров, обогащая их уменьшенными изображениями.
    """
    
    # 1. Формируем ключ для кеша
    cache_key_parts = [
        "products_v3", f"page:{page}", f"size:{size}", # v3, чтобы избежать старого кеша
        f"sku:{sku}" if sku else "",
        f"cat:{category}" if category else "",
        f"tag:{tag}" if tag else "",
        f"search:{search}" if search else "",
        f"minp:{min_price}" if min_price else "",
        f"maxp:{max_price}" if max_price else "",
        f"orderby:{orderby}" if orderby else "",
        f"order:{order}" if order else "",
        f"feat:{featured}" if featured else ""
    ]
    base_cache_key = ":".join(filter(None, cache_key_parts))
    cache_key = f"{base_cache_key}:user:{user_id}" if user_id else base_cache_key

    cached_products = await redis.get(cache_key)
    if cached_products:
        logger.info(f"Serving products from cache for key: {cache_key}")
        return PaginatedProducts.model_validate(json.loads(cached_products))
        
    # 2. Получаем ID избранных товаров
    favorite_product_ids = set()
    if user_id:
        favorite_items_db = get_favorite_items(db, user_id=user_id)
        favorite_product_ids = {item.product_id for item in favorite_items_db}

    # 3. Формируем параметры для WooCommerce API
    params = { "page": page, "per_page": size, "status": "publish", "stock_status": "instock" }
    if sku:
        params["sku"] = sku
    elif search:
        params["search"] = search
    
    if category: params["category"] = category
    if tag: params["tag"] = tag
    if min_price is not None: params["min_price"] = min_price
    if max_price is not None: params["max_price"] = max_price
    if orderby in ["date", "id", "title", "price", "popularity", "rating"]: params["orderby"] = orderby
    if order in ["asc", "desc"]: params["order"] = order
    if featured: params["featured"] = featured
    
    logger.info(f"Fetching products from WC with params: {params}")
    response = await wc_client.get("wc/v3/products", params=params)
    products_data = response.json()
    
    total_items = int(response.headers.get("X-WP-Total", 0))
    total_pages = int(response.headers.get("X-WP-TotalPages", 0))
    
    # --- ЛОГИКА ПОЛУЧЕНИЯ МИНИАТЮР ---
    
    # 4. Собираем ID главных изображений
    media_ids_to_fetch = []
    # Словарь для связи ID товара с ID его изображения
    product_to_media_map = {} 

    for p_data in products_data:
        media_id = p_data.get("featured_media")
        
        # Fallback: если нет featured_media, берем первое из галереи
        if not media_id or media_id == 0:
            images = p_data.get("images", [])
            if images and isinstance(images, list) and len(images) > 0:
                media_id = images[0].get("id")
        
        if media_id and media_id > 0:
            media_ids_to_fetch.append(media_id)
            product_to_media_map[p_data["id"]] = media_id
    
    # Убираем дубликаты, если несколько товаров используют одно и то же изображение
    media_ids_to_fetch = list(set(media_ids_to_fetch))
    logger.info(f"Found {len(media_ids_to_fetch)} unique media IDs to fetch: {media_ids_to_fetch}")
    
    media_urls_map = {}
    if media_ids_to_fetch:
        try:
            include_ids_str = ",".join(map(str, media_ids_to_fetch))
            media_params = {"include": include_ids_str, "per_page": len(media_ids_to_fetch)}
            
            # --- ИСПРАВЛЕНИЕ: Формируем абсолютный URL вручную ---
            media_url = f"{settings.WP_URL}/wp-json/wp/v2/media"
            logger.info(f"Requesting media details from URL: {media_url} with params: {media_params}")
            
            # Используем .get() напрямую у httpx клиента, чтобы он не добавлял свой base_url
            media_response = await wc_client.async_client.get(media_url, params=media_params)
            media_response.raise_for_status()
            media_data = media_response.json()
            
            logger.debug(f"Received media details response: {json.dumps(media_data, indent=2)}")

            # 6. Создаем "карту" из ID в URL миниатюры
            for media_item in media_data:
                thumbnail_url = None
                sizes = media_item.get("media_details", {}).get("sizes", {})
                
                if "woocommerce_thumbnail" in sizes:
                    thumbnail_url = sizes["woocommerce_thumbnail"]["source_url"]
                elif "medium" in sizes:
                    thumbnail_url = sizes["medium"]["source_url"]
                elif "full" in sizes:
                    thumbnail_url = sizes["full"]["source_url"]
                else:
                    # Если размеров нет, берем основной URL
                    thumbnail_url = media_item.get("source_url")
                
                if thumbnail_url:
                    media_urls_map[media_item["id"]] = thumbnail_url
        except Exception as e:
            logger.error("Failed to fetch featured media details", exc_info=True)

    # 7. Обрабатываем и "обогащаем" данные
    enriched_products = []
    if products_data and isinstance(products_data, list):
        for p_data in products_data:
            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            product_id = p_data.get("id")
            # Находим ID изображения, который мы сохранили для этого товара
            media_id_for_product = product_to_media_map.get(product_id)
            # Получаем URL миниатюры из нашей карты
            thumbnail_url = media_urls_map.get(media_id_for_product)
            
            if thumbnail_url:
                p_data["images"] = [{"id": media_id_for_product, "src": thumbnail_url, "alt": ""}]
            elif p_data.get("images") and p_data.get("images"):
                 pass
            else:
                p_data["images"] = []

            try:
                product_obj = Product.model_validate(p_data)
                product_obj.is_favorite = product_obj.id in favorite_product_ids
                enriched_products.append(product_obj)
            except Exception as e:
                logger.warning(f"Failed to validate product data for product ID {p_data.get('id')}", exc_info=True)
        
    paginated_result = PaginatedProducts(
        total_items=total_items, total_pages=total_pages,
        current_page=page, size=size, items=enriched_products
    )
    
    await redis.set(cache_key, paginated_result.model_dump_json(), ex=CACHE_TTL_SECONDS)
    
    return paginated_result

async def get_product_by_id(
    db: Session,
    redis: Redis,
    product_id: int,
    user_id: Optional[int] = None
) -> Optional[Product]:
    """
    Получает детальную информацию о товаре по ID, используя кеш,
    и обогащает ее флагом is_favorite для текущего пользователя.
    """
    base_cache_key = f"product:{product_id}"
    cache_key = f"{base_cache_key}:user:{user_id}" if user_id else base_cache_key

    cached_product = await redis.get(cache_key)
    if cached_product:
        product_from_cache = Product.model_validate(json.loads(cached_product))
        if product_from_cache.stock_status == 'instock':
            return product_from_cache
        else:
            await redis.delete(cache_key)
    
    try:
        response = await wc_client.get(f"wc/v3/products/{product_id}")
        product_data = response.json()
        
        if product_data.get("stock_status") != "instock":
            return None

        product = Product.model_validate(product_data)

        # Обогащаем данные флагом is_favorite.
        if user_id:
            # Делаем один запрос к БД, чтобы получить все ID избранного
            favorite_items_db = get_favorite_items(db, user_id=user_id)
            favorite_product_ids = {item.product_id for item in favorite_items_db}
            product.is_favorite = product.id in favorite_product_ids
        
        await redis.set(cache_key, product.model_dump_json(), ex=CACHE_TTL_SECONDS)
        
        return product
    except Exception:
        return None

async def _get_any_product_by_id_from_wc(product_id: int) -> dict | None:
    """
    Получает "сырые" данные о товаре из WooCommerce по ID,
    ИГНОРИРУЯ кеш и статус наличия.
    """
    try:
        response = await wc_client.get(f"wc/v3/products/{product_id}")
        return response.json()
    except Exception:
        logger.warning(f"Could not fetch product data from WC for product ID {product_id} (it may be deleted).")
        return None