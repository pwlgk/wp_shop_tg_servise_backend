# app/schemas/settings.py
from pydantic import BaseModel
from typing import Optional

class ShopSettings(BaseModel):
    min_order_amount: float
    welcome_bonus_amount: int
    is_welcome_bonus_active: bool
    max_points_payment_percentage: int
    referral_welcome_bonus: int
    referrer_bonus: int
    birthday_bonus_amount: int
    client_data_version: int

# --- НОВЫЙ КЛАСС ДЛЯ ОБНОВЛЕНИЙ ---
class ShopSettingsUpdate(BaseModel):
    """
    Схема для частичного обновления настроек. 
    Все поля опциональны.
    """
    min_order_amount: Optional[float] = None
    welcome_bonus_amount: Optional[int] = None
    is_welcome_bonus_active: Optional[bool] = None
    max_points_payment_percentage: Optional[int] = None
    referral_welcome_bonus: Optional[int] = None
    referrer_bonus: Optional[int] = None
    birthday_bonus_amount: Optional[int] = None
    client_data_version: Optional[int] = None