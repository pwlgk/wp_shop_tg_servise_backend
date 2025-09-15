# app/schemas/notification.py
from pydantic import BaseModel
from datetime import datetime
from typing import List

class Notification(BaseModel):
    id: int
    type: str
    title: str
    message: str | None
    created_at: datetime
    is_read: bool # Важно отдавать и этот флаг

    class Config:
        from_attributes = True