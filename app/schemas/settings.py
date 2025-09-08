# app/schemas/settings.py
from pydantic import BaseModel

class ShopSettings(BaseModel):
    min_order_amount: float
    welcome_bonus_amount: int
    is_welcome_bonus_active: bool