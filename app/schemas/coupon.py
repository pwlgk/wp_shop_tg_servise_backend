# app/schemas/coupon.py

from pydantic import BaseModel
from typing import Literal, List, Optional

class LineItemForValidation(BaseModel):
    """
    Упрощенная структура товарной позиции, необходимая для
    валидации купона на стороне WordPress.
    """
    product_id: int
    quantity: int


class CouponValidateRequest(BaseModel):
    """
    Схема для тела запроса на эндпоинт валидации купона.
    """
    coupon_code: str
    line_items: List[LineItemForValidation]


class Coupon(BaseModel):
    """
    Схема для ответа от эндпоинта валидации.
    Содержит основную информацию о купоне и рассчитанную сумму скидки.
    """
    code: str
    amount: str
    discount_type: Literal["fixed_cart", "percent", "fixed_product"]
    description: str
    discount_amount: float