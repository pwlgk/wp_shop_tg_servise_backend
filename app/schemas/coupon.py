# app/schemas/coupon.py

from datetime import datetime
from pydantic import BaseModel, Field
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


class CouponDetails(BaseModel):
    """Полная информация о промокоде из WooCommerce."""
    id: int
    code: str
    amount: str
    discount_type: Literal["fixed_cart", "percent", "fixed_product"]
    description: str
    date_expires: Optional[datetime] = None
    usage_count: int
    usage_limit: Optional[int] = None
    usage_limit_per_user: Optional[int] = None
    individual_use: bool
    
    class Config:
        from_attributes = True

class PaginatedAdminCoupons(BaseModel):
    # Используем кастомную пагинацию, так как WooCommerce не отдает total_pages
    items: List[CouponDetails]

class CouponCreate(BaseModel):
    """Схема для создания нового промокода."""
    code: str = Field(..., description="Код купона, например, 'SALE10'")
    discount_type: Literal["fixed_cart", "percent"] = Field("percent", description="Тип скидки: 'percent' (процент) или 'fixed_cart' (фикс. сумма)")
    amount: str = Field(..., description="Размер скидки. '10.00' для 10% или 10 рублей.")
    individual_use: bool = Field(True, description="Можно ли использовать с другими купонами?")
    usage_limit: Optional[int] = Field(None, gt=0, description="Сколько всего раз можно использовать купон?")
    date_expires: Optional[str] = Field(None, description="Дата окончания действия в формате YYYY-MM-DD")