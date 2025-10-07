# app/services/catalog.py

import json
import logging
from typing import List, Optional
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.clients.woocommerce import wc_client
from app.schemas.product import ProductCategory, Product, PaginatedProducts
from app.crud.cart import get_favorite_items  # Импортируем CRUD-функцию для избранного

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600  # 10 минут


async def get_all_categories(redis: Redis) -> List[ProductCategory]:
    """
    Получает иерархический список всех категорий товаров, используя кеш.
    """
    cache_key = "categories:hierarchical:all" # Используем новый ключ кеша
    
    cached_categories = await redis.get(cache_key)
    if cached_categories:
        return [ProductCategory.model_validate(cat) for cat in json.loads(cached_categories)]
            
    # 1. Получаем ВСЕ категории плоским списком от WooCommerce
    # `per_page=100` - чтобы получить все за один раз (увеличьте, если категорий больше)
    response = await wc_client.get("wc/v3/products/categories", params={"per_page": 100, "hide_empty": True})
    categories_data = response.json()
    
    # --- НОВАЯ ЛОГИКА ПОСТРОЕНИЯ ДЕРЕВА ---
    
    # 2. Создаем словарь для быстрого доступа к категориям по ID и для хранения дочерних
    categories_map = {}
    root_categories = [] # Список для категорий верхнего уровня (у которых parent=0)

    # Первый проход: создаем объекты Pydantic и раскладываем их по словарю
    for cat_data in categories_data:
        image_src = cat_data.get("image", {}).get("src") if cat_data.get("image") else None
        category_obj = ProductCategory(
            id=cat_data['id'],
            name=cat_data['name'],
            slug=cat_data['slug'],
            image_src=image_src,
            children=[] # Инициализируем пустым списком
        )
        categories_map[category_obj.id] = category_obj

    # Второй проход: строим иерархию
    for cat_data in categories_data:
        category_id = cat_data['id']
        parent_id = cat_data.get('parent', 0)
        
        current_category = categories_map[category_id]
        
        if parent_id == 0:
            # Это категория верхнего уровня
            root_categories.append(current_category)
        elif parent_id in categories_map:
            # Если родитель существует, добавляем текущую категорию в его `children`
            parent_category = categories_map[parent_id]
            parent_category.children.append(current_category)
    
    # -----------------------------------
    
    # Сохраняем в кеш уже древовидную структуру
    # Pydantic v2 `model_dump` рекурсивно преобразует вложенные объекты
    await redis.set(cache_key, json.dumps([cat.model_dump() for cat in root_categories]), ex=CACHE_TTL_SECONDS)
    
    return root_categories


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
    Получает пагинированный список товаров с поддержкой фильтров,
    поиска по тексту и артикулу (SKU), и обогащает его флагом is_favorite.
    """
    
    # 1. Формируем уникальный ключ для кеша на основе всех параметров.
    cache_key_parts = [
        "products", f"page:{page}", f"size:{size}",
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
        return PaginatedProducts.model_validate(json.loads(cached_products))
        
    # 2. Получаем ID избранных товаров для текущего пользователя.
    favorite_product_ids = set()
    if user_id:
        favorite_items_db = get_favorite_items(db, user_id=user_id)
        favorite_product_ids = {item.product_id for item in favorite_items_db}

    # 3. Формируем словарь параметров для WooCommerce API.
    params = {
        "page": page, "per_page": size,
        "status": "publish", "stock_status": "instock", "_embed": "wp:featuredmedia"
    }
    
    # Поиск по SKU имеет приоритет над текстовым поиском.
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

    response = await wc_client.get("wc/v3/products", params=params)
    
    total_items = int(response.headers.get("X-WP-Total", 0))
    total_pages = int(response.headers.get("X-WP-TotalPages", 0))
    products_data = response.json()
    
    # 4. Обогащаем данные флагом is_favorite.
    enriched_products = []
    if products_data and isinstance(products_data, list):
        for p_data in products_data:
            # --- НОВАЯ ЛОГИКА ИЗВЛЕЧЕНИЯ УМЕНЬШЕННОГО ИЗОБРАЖЕНИЯ ---
            thumbnail_url = None
            try:
                # Пытаемся найти уменьшенную копию в `_embedded` блоке
                media_details = p_data["_embedded"]["wp:featuredmedia"][0]["media_details"]
                # `woocommerce_thumbnail` - это стандартный размер WooCommerce
                thumbnail_url = media_details["sizes"]["woocommerce_thumbnail"]["source_url"]
                logger.info("Add woocommerce_thumbnail")
            except (KeyError, IndexError):
                # Если что-то пошло не так (нет картинки, нет нужного размера),
                # используем полноразмерное изображение как fallback.
                if p_data.get("images") and p_data["images"][0]:
                    thumbnail_url = p_data["images"][0].get("src")
                logger.info("Not woocommerce_thumbnail")

            # Подменяем "сырой" список изображений на один thumbnail
            if thumbnail_url:
                p_data["images"] = [{"id": 0, "src": thumbnail_url, "alt": ""}]
            else:
                p_data["images"] = [] # Если изображений нет вообще
            # ----------------------------------------------------

            try:
                product_obj = Product.model_validate(p_data)
                product_obj.is_favorite = product_obj.id in favorite_product_ids
                enriched_products.append(product_obj)
            except Exception as e:
                logger.warning(f"Failed to validate product data for product ID {p_data.get('id')}: {e}")
        
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