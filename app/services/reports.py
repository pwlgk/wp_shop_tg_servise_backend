# app/services/reports.py

import logging
from fastapi import HTTPException
import httpx
from app.clients.woocommerce import wc_client
from app.core.redis import redis_client
from app.schemas.admin import PaginatedAdminProducts, AdminProductListItem

logger = logging.getLogger(__name__)

async def get_low_stock_products(threshold: int) -> PaginatedAdminProducts:
    """
    Получает список товаров с низкими остатками, используя кеширование.
    """
    cache_key = f"reports:low_stock:{threshold}"
    cached_data = await redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Serving low stock report for threshold '{threshold}' from cache.")
        return PaginatedAdminProducts.model_validate_json(cached_data)

    logger.info(f"Fetching fresh low stock report for threshold '{threshold}'.")
    
    try:
        all_products = []
        page = 1
        while True:
            response = await wc_client.get(
                "wc/v3/products",
                params={"per_page": 100, "page": page, "manage_stock": True, "stock_status": "instock"}
            )
            products_chunk = response.json()
            if not products_chunk:
                break
            all_products.extend(products_chunk)
            page += 1
        
        low_stock_items = [
            p for p in all_products 
            if p.get("stock_quantity") is not None and p.get("stock_quantity") <= threshold
        ]
        
        validated_items = [AdminProductListItem.model_validate(p) for p in low_stock_items]
        
        result = PaginatedAdminProducts(
            total_items=len(validated_items),
            total_pages=1,
            current_page=1,
            size=len(validated_items),
            items=validated_items
        )

        # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
        # Добавляем cache_key как первый аргумент
        await redis_client.set(cache_key, result.model_dump_json(), ex=14400)
        # -------------------------
        
        return result

    except httpx.HTTPStatusError as e:
        logger.error("WooCommerce API error while generating low stock report.", exc_info=True)
        raise HTTPException(status_code=503, detail=f"WooCommerce API is unavailable: {e.response.text}")
    except Exception as e:
        logger.error("Failed to generate low stock products report.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate report.")