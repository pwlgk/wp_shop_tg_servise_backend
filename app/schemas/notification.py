# app/schemas/notification.py
from pydantic import BaseModel, HttpUrl
from datetime import datetime
from typing import List

class Notification(BaseModel):
    id: int
    type: str
    title: str
    message: str | None
    created_at: datetime
    image_url: HttpUrl | None = None # Используем HttpUrl для валидации

    is_read: bool # Важно отдавать и этот флаг  
    action_url: str | None # Относительный URL для перехода внутри Mini App
    related_entity_id: str | None # ID связанной сущности (заказа, акции и т.д.)
    class Config:
        from_attributes = True