# app/bot/callbacks/admin.py
from typing import Optional
from aiogram.filters.callback_data import CallbackData

class UserListCallback(CallbackData, prefix="users_list"):
    action: str
    
    # --- ВОЗВРАЩАЕМ ЯВНЫЕ ПОЛЯ ---
    level: Optional[str] = None
    bot_blocked: Optional[bool] = None
    # ---------------------------
    
    page: int = 1