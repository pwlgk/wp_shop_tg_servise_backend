# app/schemas/settings.py
from pydantic import BaseModel

class ShopSettings(BaseModel):
    min_order_amount: float
    welcome_bonus_amount: int
    is_welcome_bonus_active: bool
    max_points_payment_percentage: int
    referral_welcome_bonus: int
    referrer_bonus: int
    birthday_bonus_amount: int
    client_data_version: int