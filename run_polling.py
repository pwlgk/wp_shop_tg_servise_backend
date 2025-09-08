# run_polling.py
import asyncio
import logging
import sys

# Важно: этот хак нужен, чтобы Python мог найти наши модули в папке `app`
# при запуске из корневой директории.
import os
sys.path.append(os.getcwd())

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- Импортируем все наши компоненты ---
from app.core.config import settings
from app.bot.handlers.user import user_router
from app.bot.handlers.admin_dialogs import admin_dialog_router
from app.bot.handlers.admin_actions import admin_actions_router

import logging

logger = logging.getLogger(__name__)

async def main() -> None:
    """
    Основная функция для запуска бота в режиме поллинга.
    """
    # 1. Инициализация бота и диспетчера
    # (Дублируем код из app/bot/core.py, так как он нам нужен здесь)
    default_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=default_properties)
    dp = Dispatcher()

    # 2. Подключаем все наши роутеры в правильном порядке
    logger.info("Including bot routers...")
    # Сначала админские (более специфичные)
    dp.include_router(admin_dialog_router)
    dp.include_router(admin_actions_router)
    # Потом пользовательские (более общие)
    dp.include_router(user_router)
    logger.info("Routers included successfully.")

    # 3. Удаляем веб-хук, если он был установлен ранее
    # Поллинг и веб-хуки не могут работать одновременно
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted.")

    # 4. Запускаем поллинг
    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    # Настраиваем базовое логирование, чтобы видеть ошибки aiogram
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped!")