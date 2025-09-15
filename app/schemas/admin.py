# app/schemas/admin.py
from pydantic import BaseModel, Field
from typing import Literal
from app.schemas.order import Order
from app.schemas.loyalty import LoyaltyTransaction
from app.schemas.product import PaginatedResponse
from datetime import datetime

class BroadcastCreate(BaseModel):
    message_text: str = Field(..., min_length=1)
    target_level: Literal["all", "bronze", "silver", "gold"] = "all"



# --- Схемы для списка пользователей ---
class AdminUserListItem(BaseModel):
    id: int
    telegram_id: int
    display_name: str
    username: str | None
    level: str
    is_blocked: bool
    bot_accessible: bool
    created_at: datetime

class PaginatedAdminUsers(PaginatedResponse[AdminUserListItem]):
    pass


# --- Схемы для детальной карточки пользователя ---
class AdminUserDetails(AdminUserListItem):
    wordpress_id: int
    email: str
    # Добавляем пагинированные списки связанных сущностей
    latest_orders: PaginatedResponse[Order]
    loyalty_history: PaginatedResponse[LoyaltyTransaction]


# --- Схемы для действий ---
class AdminSendMessageRequest(BaseModel):
    message_text: str

class AdminAdjustPointsRequest(BaseModel):
    points: int
    comment: str # Комментарий для транзакции, например, "Бонус за конкурс"

class AdminPromoListItem(BaseModel):
    id: int
    title: str
    status: str
    created_date: datetime
    
    # Очищенные данные
    text_content: str
    image_url: str | None
    
    # Параметры из ACF
    target_level: str
    action_url: str | None

class PaginatedAdminPromos(PaginatedResponse[AdminPromoListItem]):
    pass