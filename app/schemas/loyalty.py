# app/schemas/loyalty.py
from pydantic import BaseModel
from datetime import datetime
from typing import List, Literal

from app.schemas.user import UserCounters

class LoyaltyTransaction(BaseModel):
    points: int
    type: str
    order_id_wc: int | None = None
    created_at: datetime
    expires_at: datetime | None = None

    class Config:
        from_attributes = True

class LoyaltyHistory(BaseModel):
    balance: int
    level: str
    transactions: List[LoyaltyTransaction]


class LoyaltyProgress(BaseModel):
    current_spending: float
    next_level: str | None # null, если достигнут максимальный уровень
    spending_to_next_level: float | None

class UserDashboard(BaseModel):
    first_name: str | None
    last_name: str | None
    
    # --- НОВЫЕ ПОЛЯ ---
    is_blocked: bool          # Заблокирован ли пользователь админом
    is_bot_accessible: bool   # Доступен ли бот для отправки сообщений
    # ------------------

    balance: int
    level: str
    has_active_orders: bool
    loyalty_progress: LoyaltyProgress
    counters: UserCounters
    phone: str | None = None
    has_unread_notifications: bool
    profile_completion_status: Literal[
            "new_user_prompt", 
            "incomplete_profile_indicator", 
            "complete"
        ]