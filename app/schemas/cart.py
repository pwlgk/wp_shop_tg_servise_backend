# app/schemas/cart.py
from pydantic import BaseModel, Field
from typing import List
from .product import Product  # Импортируем схему Product для детального ответа

# Схема для добавления/обновления товара в корзине
class CartItemUpdate(BaseModel):
    product_id: int
    quantity: int = Field(1, gt=0) # Количество должно быть больше 0

# Схема для одного элемента в ответе о содержимом корзины
class CartItemResponse(BaseModel):
    product: Product  # Полная информация о товаре
    quantity: int

class CartStatusNotification(BaseModel):
    level: str  # e.g., "warning", "error"
    message: str

# Обновляем схему CartResponse
class CartResponse(BaseModel):
    items: List[CartItemResponse]
    
    # --- Основные расчеты ---
    total_items_price: float # "Чистая" стоимость товаров
    discount_amount: float = 0.0   # Сумма скидки от промокода
    final_price: float         # Итоговая цена (total_items_price - discount_amount)
    
    # --- Информация для пользователя ---
    notifications: List[CartStatusNotification]
    min_order_amount: float
    is_min_amount_reached: bool
    max_points_to_spend: int # Макс. кол-во баллов для списания (считается от final_price)
    
    # --- Информация о примененном купоне ---
    applied_coupon_code: str | None = None


# Схема для добавления/удаления товара из избранного
class FavoriteItemUpdate(BaseModel):
    product_id: int

# Схема для ответа со списком избранного
class FavoriteResponse(BaseModel):
    items: List[Product] # Просто список товаров