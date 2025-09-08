# app/schemas/admin.py
from pydantic import BaseModel, Field
from typing import Literal

class BroadcastCreate(BaseModel):
    message_text: str = Field(..., min_length=1)
    target_level: Literal["all", "bronze", "silver", "gold"] = "all"