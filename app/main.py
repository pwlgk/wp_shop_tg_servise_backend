# app/main.py

import asyncio
import os
import traceback
import logging
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Конфигурация и ядро
from app.core.config import settings as config
from app.core.logging_config import setup_logging
from app.core.redis import redis_client

# Роутеры FastAPI
from app.routers import (
    auth, user, catalog, cart, order, admin as admin_router,
    settings as settings_router, coupon as coupon_router,
    notification as notification_router, cms
)
from app.routers.webhooks import wc_router, telegram_router

# Логика бота
from app.bot.core import bot, dp
from app.bot.handlers.user import user_router
from app.bot.handlers.admin_dialogs import admin_dialog_router
from app.bot.handlers.admin_actions import admin_actions_router
from app.services.user_updater import update_all_usernames_task
# Фоновые задачи и сервисы
from app.services.user_levels import update_all_user_levels
from app.services.points_expiration import expire_points_task, notify_about_expiring_points_task
from app.services.bot_status_updater import check_inactive_bots_task
from app.services.notification_cleanup import cleanup_old_notifications_task
from app.bot.services import notification as bot_notification_service
from app.services.birthday_greeter import check_birthdays_task

# --- Инициализация ---
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# --- Обработчик критических ошибок ---
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Глобальный обработчик для всех необработанных исключений.
    Логирует ошибку и отправляет уведомление супер-админам.
    """
    logger.critical(f"Unhandled exception for request: {request.method} {request.url}", exc_info=True)
    
    error_details = "".join(traceback.format_exception(exc))
    
    request_info = (
        f"<b>URL:</b> <code>{request.method} {request.url}</code>\n"
        f"<b>Client:</b> <code>{request.client.host}:{request.client.port}</code>"
    )
    
    error_message = (
        f"🚨 <b>Критическая ошибка в API!</b>\n\n"
        f"{request_info}\n\n"
        f"<b>Traceback:</b>\n<pre>{error_details}</pre>"
    )
    
    asyncio.create_task(
        bot_notification_service.send_error_to_super_admins(error_message)
    )
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error. The administrator has been notified."},
    )

# --- Lifespan Manager (запуск и остановка приложения) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Application lifespan startup...")
    
    # Регистрация роутеров aiogram должна быть у всех воркеров
    dp.include_router(admin_dialog_router)
    dp.include_router(admin_actions_router)
    dp.include_router(user_router)
    logger.info("Aiogram routers included.")
    
    # Надежная блокировка через Redis для однократной инициализации
    is_main_worker = await redis_client.set("app_startup_lock", "1", ex=60, nx=True)
    
    if is_main_worker:
        logger.info("This is the main worker. Running initial setup...")
        
        webhook_url = f"{config.BASE_WEBHOOK_URL}{config.TELEGRAM_WEBHOOK_PATH}"
        await bot.set_webhook(url=webhook_url, secret_token=config.TELEGRAM_WEBHOOK_SECRET)
        logger.info(f"Telegram webhook set to: {webhook_url}")
        
        if not scheduler.running:
            scheduler.add_job(update_all_user_levels, 'cron', hour=3, minute=0, timezone='Europe/Moscow')
            scheduler.add_job(expire_points_task, 'cron', hour=4, minute=0, timezone='Europe/Moscow')
            scheduler.add_job(notify_about_expiring_points_task, 'cron', hour=10, minute=0, timezone='Europe/Moscow')
            scheduler.add_job(check_inactive_bots_task, 'cron', hour=5, minute=0, timezone='Europe/Moscow')
            scheduler.add_job(cleanup_old_notifications_task, 'cron', hour=5, minute=30, timezone='Europe/Moscow')
            scheduler.add_job(check_birthdays_task, 'cron', hour=2, minute=0, timezone='Europe/Moscow')
            scheduler.add_job(update_all_usernames_task, 'cron', day_of_week='sun', hour=6, timezone='Europe/Moscow') # Каждое воскресенье в 6 утра
            scheduler.start()
            logger.info("Scheduler started with background jobs.")
    else:
        logger.info("This is a secondary worker. Skipping initial setup.")

    yield
    
    # Код при остановке
    if is_main_worker:
        logger.info("Main worker shutting down...")
        if scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler shut down.")
        
        await bot.delete_webhook()
        logger.info("Telegram webhook deleted.")
        
        await redis_client.delete("app_startup_lock")
    else:
        logger.info("Secondary worker shutting down.")

# --- Создание FastAPI приложения ---
app = FastAPI(
    title="Telegram Mini App Service",
    description="Backend for Frontend service for Telegram e-commerce app",
    version="0.1.0",
    lifespan=lifespan
)

origins = [
    "http://localhost",
    "http://localhost:3000", # для React/Vue
    "http://localhost:8080",
    "http://localhost:5173", # для Vite
    "https://web.telegram.org",
    config.MINI_APP_URL,
    config.MINI_APP_URL_ADMIN
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # Разрешить запросы с этих доменов
    allow_credentials=True, # Разрешить передачу cookie (если понадобится)
    allow_methods=["*"],    # Разрешить все методы (GET, POST, PUT, DELETE и т.д.)
    allow_headers=["*"],    # Разрешить все заголовки
)


# --- Регистрация обработчика исключений ---
app.add_exception_handler(Exception, unhandled_exception_handler)

# --- Подключение роутеров FastAPI ---
api_router = APIRouter(prefix="/api/v1")

# Пользовательские и публичные эндпоинты
api_router.include_router(auth.router, tags=["Authentication"])
api_router.include_router(user.router, tags=["Users"])
api_router.include_router(catalog.router, tags=["Catalog"])
api_router.include_router(cart.router, tags=["Cart & Favorites"])
api_router.include_router(order.router, tags=["Orders"])
api_router.include_router(settings_router.router, tags=["Settings"])
api_router.include_router(coupon_router.router, tags=["Coupons"])
api_router.include_router(notification_router.router, tags=["Notifications"])
api_router.include_router(cms.router, tags=["CMS"])

# Админские эндпоинты
api_router.include_router(admin_router.router, prefix="/admin", tags=["Admin"])

# Подключаем главный роутер к приложению
app.include_router(api_router)

# Веб-хуки (остаются в корне)
app.include_router(telegram_router, tags=["Telegram Bot"])
app.include_router(wc_router, prefix="/internal/webhooks", tags=["Internal Webhooks"])