# app/schemas/order.py
from pydantic import BaseModel, field_validator
from typing import List, Union
from datetime import datetime

from app.schemas.user import AddressSchema

# Схема для одной позиции в заказе
class OrderLineItem(BaseModel):
    product_id: int
    name: str
    quantity: int
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Мы ожидаем либо строку, либо число/дробное число
    price: Union[str, int, float]
    total: Union[str, int, float]
    image_url: str | None = None # URL главного изображения товара

    # --- НОВЫЙ ВАЛИДАТОР ---
    # Этот валидатор будет приводить все числовые цены к строке
    @field_validator('price', 'total', mode='before')
    @classmethod
    def validate_prices_to_str(cls, v):
        if isinstance(v, (int, float)):
            return str(v)
        return v


# Схема для ответа с деталями созданного заказа
class Order(BaseModel):
    id: int
    number: str # <-- Добавляем номер заказа (может отличаться от ID)
    status: str
    date_created: datetime
    total: str
    payment_method_title: str # <-- Добавляем название способа оплаты
    customer_telegram_id: int | None = None
    billing: AddressSchema # <-- Добавляем полный объект адреса
    
    payment_url: str
    line_items: List[OrderLineItem]
    can_be_cancelled: bool = False


    @field_validator('total', mode='before')
    @classmethod
    def validate_total_to_str(cls, v):
        if isinstance(v, (int, float)):
            return str(v)
        return v

    class Config:
        from_attributes = True

# Схема для запроса на создание заказа (без изменений)
class OrderCreate(BaseModel):
    pass

class OrderCreate(BaseModel):
    payment_method_id: str
    points_to_spend: int = 0
    coupon_code: str | None = None