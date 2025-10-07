# app/schemas/product.py
from pydantic import BaseModel
from typing import Generic, List, Optional, TypeVar

from .notification import Notification
from app.schemas.order import Order

class ProductCategory(BaseModel):
    id: int
    name: str
    slug: str
    image_src: Optional[str] = None
    children: List['ProductCategory'] = [] # <-- Добавляем поле для дочерних категорий
    count: int
    has_in_stock_products: bool = False
    class Config:
        from_attributes = True

class EmbeddedProductCategory(BaseModel):
    """Упрощенная схема категории, как она приходит внутри объекта Product."""
    id: int
    name: str
    slug: str

    class Config:
        from_attributes = True

class ProductImage(BaseModel):
    id: int
    src: str
    alt: str

class Product(BaseModel):
    id: int
    name: str
    slug: str
    sku: str | None = ""
    price: str
    regular_price: str
    sale_price: str
    on_sale: bool
    short_description: str
    description: str
    stock_quantity: int | None
    stock_status: str
    images: List[ProductImage]
    categories: List[EmbeddedProductCategory]
    is_favorite: bool = False # По умолчанию False


    class Config:
        from_attributes = True
        
# Схема для пагинированного ответа
class PaginatedProducts(BaseModel):
    total_items: int
    total_pages: int
    current_page: int
    size: int
    items: List[Product]

# --- КОНКРЕТНЫЕ РЕАЛИЗАЦИИ ДЛЯ НАШИХ МОДЕЛЕЙ ---

DataType = TypeVar('DataType')

# --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
class PaginatedResponse(BaseModel, Generic[DataType]): # <-- BaseModel теперь первый
    """
    Универсальная Pydantic-схема для пагинированных ответов.
    """
    total_items: int
    total_pages: int
    current_page: int
    size: int
    items: List[DataType]

# Конкретные реализации менять не нужно, они наследуют правильный порядок
class PaginatedFavorites(PaginatedResponse[Product]):
    pass

class PaginatedOrders(PaginatedResponse[Order]):
    pass

class PaginatedNotifications(PaginatedResponse[Notification]):
    pass