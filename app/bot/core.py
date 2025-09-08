# app/bot/core.py
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties # <-- Импортируем новый класс
from aiogram.enums import ParseMode
from app.core.config import settings

# Создаем объект с настройками по умолчанию
default_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)

# Передаем его в Bot через аргумент `default`
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=default_properties)

dp = Dispatcher()