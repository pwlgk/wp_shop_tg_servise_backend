# app/schemas/coupon.py
from pydantic import BaseModel
from typing import List, Literal, Optional

class CouponValidateRequest(BaseModel):
    coupon_code: str

class Coupon(BaseModel):
    id: int
    code: str
    amount: str
    discount_type: str
    description: str
    date_expires: Optional[str] = None
    usage_count: int
    individual_use: bool
    product_ids: List[int]
    excluded_product_ids: List[int]
    usage_limit: Optional[int]
    usage_limit_per_user: Optional[int]
    limit_usage_to_x_items: Optional[int]
    free_shipping: bool
    product_categories: List[int]
    excluded_product_categories: List[int]
    exclude_sale_items: bool
    minimum_amount: str
    maximum_amount: str
    email_restrictions: List[str]