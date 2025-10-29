# app/schemas/admin.py
import json
from pydantic import BaseModel, Field, HttpUrl, model_validator
from typing import List, Literal, Optional
from app.schemas.order import Order
from app.schemas.loyalty import LoyaltyTransaction
from app.schemas.product import PaginatedResponse
from datetime import datetime

class BroadcastCreate(BaseModel):
    """Схема для создания рассылки."""
    message_text: str = Field(..., min_length=1)
    target_level: Literal["all", "bronze", "silver", "gold"] = "all"
    
    # Поля, которые приходят из формы в админке
    image_url: Optional[HttpUrl] = Field(None, description="URL изображения, если оно уже загружено.")
    button_text: Optional[str] = Field(None, max_length=50)
    button_url: Optional[str] = Field(None, pattern=r"^(/|https://|http://)")
    scheduled_at: Optional[datetime] = None




class BroadcastListItem(BaseModel):
    id: int
    message_text: str
    status: str
    target_level: str
    scheduled_at: Optional[datetime]
    sent_count: int
    failed_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class PaginatedAdminBroadcasts(PaginatedResponse[BroadcastListItem]):
    pass

class BroadcastDetails(BroadcastListItem):
    """Детальная информация о рассылке для ответа API."""
    image_url: Optional[HttpUrl] # <--- ИСПРАВЛЕНО
    button_text: Optional[str]
    button_url: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


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


class AdminOrderListItem(BaseModel):
    id: int
    number: str
    status: str
    created_date: datetime
    total: str
    
    # --- Новые, "плоские" поля ---
    customer_display_name: str
    customer_telegram_id: int | None
    items_summary: str # Краткий состав заказа, например, "Товар А x2, Товар Б x1"

class PaginatedAdminOrders(PaginatedResponse[AdminOrderListItem]):
    pass


class ChannelPostButton(BaseModel):
    """Схема для одной кнопки в посте."""
    text: str
    url: str
    type: Literal["web_app", "external"]

# --- ОБНОВЛЕННАЯ СХЕМА СОЗДАНИЯ ПОСТА ---
class ChannelPostCreate(BaseModel):
    """Схема для внутреннего представления данных поста."""
    title: Optional[str] = None
    message_text: str = Field(..., min_length=1)
    button: Optional[ChannelPostButton] = None

# --- ОБНОВЛЕННАЯ СХЕМА ДЛЯ СПИСКА ПОСТОВ ---
class ChannelPostListItem(BaseModel):
    id: int
    title: Optional[str]
    message_text: str
    media_urls: List[HttpUrl] = Field(default_factory=list)
    channel_message_id: Optional[int] = None # <-- Сделаем None по умолчанию
    status: str
    published_at: datetime
    
    @model_validator(mode='before')
    def unpack_json_fields(cls, data):
        # Распаковка media_urls
        if hasattr(data, 'media_urls_json') and data.media_urls_json:
            data.media_urls = json.loads(data.media_urls_json)
        
        # --- НОВАЯ ЛОГИКА: Извлекаем первый ID из JSON ---
        if hasattr(data, 'channel_message_ids_json') and data.channel_message_ids_json:
            message_ids = json.loads(data.channel_message_ids_json)
            if message_ids:
                data.channel_message_id = message_ids[0]
        
        return data

    class Config:
        from_attributes = True

class PaginatedAdminChannelPosts(PaginatedResponse[ChannelPostListItem]):
    pass


class DialogueUser(BaseModel):
    """Упрощенная информация о пользователе для списков диалогов."""
    id: int
    telegram_id: int
    display_name: str

class DialogueListItem(BaseModel):
    """Элемент в списке диалогов."""
    id: int
    status: str
    last_message_at: datetime
    last_message_snippet: Optional[str]
    user: DialogueUser
    
    class Config:
        from_attributes = True

class PaginatedAdminDialogues(PaginatedResponse[DialogueListItem]):
    pass

class DialogueMessageItem(BaseModel):
    """Одно сообщение в истории диалога."""
    id: int
    sender_type: str
    sender: DialogueUser
    text: Optional[str] # <--- Текст стал опциональным
    
    # --- Новые поля ---
    media_type: Optional[str] = None
    media_url: Optional[HttpUrl] = None # Pydantic провалидирует, что это URL
    file_name: Optional[str] = None
    
    created_at: datetime
    
    class Config:
        from_attributes = True

class DialogueDetails(BaseModel):
    """Полная информация о диалоге, включая историю сообщений."""
    id: int
    status: str
    user: DialogueUser
    messages: List[DialogueMessageItem] # Пока без пагинации для простоты, можно добавить позже
    
    class Config:
        from_attributes = True

class DialogueReplyRequest(BaseModel):
    """Схема для запроса на ответ в диалог."""
    text: str = Field(..., min_length=1)


class CleanupTaskRequest(BaseModel):
    older_than_days: int = Field(..., gt=0, description="Удалить файлы старше указанного количества дней.")



class TaskInfo(BaseModel):
    """Описание одной фоновой задачи."""
    task_name: str
    description: str
    # В будущем можно добавить last_run_status

class TaskRunRequest(BaseModel):
    """Схема для запроса на запуск задачи."""
    # Используем Literal, чтобы ограничить возможные значения
    # и добавить автодополнение в Swagger
    task_name: Literal[
        "all",
        "update_user_levels",
        "expire_points",
        "notify_expiring_points",
        "greet_birthdays",
        "check_inactive_bots",
        "activate_new_users",
        "reactivate_sleeping_users",
        "update_all_usernames",
        "cleanup_old_notifications"
    ]

class AdminOrderNote(BaseModel):
    """Схема для одной заметки к заказу."""
    id: int
    author: str
    date_created: datetime
    note: str
    customer_note: bool # True, если заметка видна покупателю

    class Config:
        from_attributes = True

class AdminOrderCustomerInfo(BaseModel):
    """Информация о клиенте для карточки заказа."""
    user_id: Optional[int] # Наш внутренний ID
    wordpress_id: int
    telegram_id: Optional[int]
    display_name: str
    email: str
    phone: Optional[str]

class AdminOrderDetails(Order):
    """
    Расширенная схема заказа для админ-панели.
    Включает информацию о клиенте и заметки к заказу.
    """
    customer_info: AdminOrderCustomerInfo
    notes: List[AdminOrderNote] = []

class AdminOrderStatusUpdate(BaseModel):
    status: Literal["processing", "on-hold", "completed", "cancelled", "refunded", "failed"]

class AdminOrderNoteCreate(BaseModel):
    note: str = Field(..., min_length=1)


class AdminDashboardStats(BaseModel):
    period: str
    revenue: float
    order_count: int
    avg_order_value: float
    items_sold: int
    new_users: int
    loyalty_points_earned: int
    loyalty_points_spent: int


class AdminProductListItem(BaseModel):
    """Упрощенная схема товара для админских отчетов."""
    id: int
    name: str
    sku: Optional[str] = ""
    price: str
    stock_quantity: Optional[int]
    
    class Config:
        from_attributes = True

class PaginatedAdminProducts(PaginatedResponse[AdminProductListItem]):
    pass