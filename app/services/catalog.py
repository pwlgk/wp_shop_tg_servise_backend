# app/services/catalog.py

import json
from typing import List, Optional
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.clients.woocommerce import wc_client
from app.schemas.product import ProductCategory, Product, PaginatedProducts
from app.crud.cart import get_favorite_items  # Импортируем CRUD-функцию для избранного

CACHE_TTL_SECONDS = 600  # 10 минут


async def get_all_categories(redis: Redis) -> List[ProductCategory]:
    """Получает список всех категорий товаров, используя кеш."""
    cache_key = "categories:all"
    
    cached_categories = await redis.get(cache_key)
    if cached_categories:
        return [ProductCategory.model_validate(cat) for cat in json.loads(cached_categories)]
        
    response = await wc_client.get("wc/v3/products/categories", params={"hide_empty": True})
    categories_data = response.json()
    
    categories = []
    for cat in categories_data:
        image_src = cat.get("image", {}).get("src") if cat.get("image") else None
        categories.append(ProductCategory(id=cat['id'], name=cat['name'], slug=cat['slug'], image_src=image_src))
        
    await redis.set(cache_key, json.dumps([cat.model_dump() for cat in categories]), ex=CACHE_TTL_SECONDS)
    
    return categories


async def get_products(
    db: Session,
    redis: Redis,
    page: int,
    size: int,
    user_id: Optional[int] = None,
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
    Получает пагинированный список товаров с поддержкой фильтров, поиска, сортировки
    и обогащает его флагом is_favorite для текущего пользователя.
    """
    
    # 1. Формируем уникальный ключ для кеша на основе всех параметров, включая user_id.
    cache_key_parts = [
        "products", f"page:{page}", f"size:{size}",
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
        
    # 2. Получаем ID избранных товаров для текущего пользователя (если он есть).
    favorite_product_ids = set()
    if user_id:
        favorite_items_db = get_favorite_items(db, user_id=user_id)
        favorite_product_ids = {item.product_id for item in favorite_items_db}

    # 3. Формируем параметры для WooCommerce API.
    params = {
        "page": page, "per_page": size,
        "status": "publish", "stock_status": "instock"
    }
    if category: params["category"] = category
    if tag: params["tag"] = tag
    if search: params["search"] = search
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
    for p_data in products_data:
        product_obj = Product.model_validate(p_data)
        product_obj.is_favorite = product_obj.id in favorite_product_ids
        enriched_products.append(product_obj)
        
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