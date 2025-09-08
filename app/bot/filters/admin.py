# app/bot/filters/admin.py
from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery
from typing import Union
from app.core.config import settings

class IsAdminFilter(Filter):
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        # event.from_user универсален и для сообщений, и для колбэков
        return event.from_user.id in settings.ADMIN_TELEGRAM_IDS