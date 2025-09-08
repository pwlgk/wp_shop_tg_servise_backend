# app/main.py
from fastapi import APIRouter, FastAPI
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.logging_config import setup_logging
# Конфигурация
from app.core.config import settings as config 

# Роутеры FastAPI
from app.routers import auth, user, catalog, cart, order, admin as admin_router, cms as cms_router
from app.routers import settings as settings_router
from app.routers.webhooks import wc_router, telegram_router 

# Логика бота
from app.bot.core import bot, dp
from app.bot.handlers.user import user_router
from app.bot.handlers.admin_dialogs import admin_dialog_router
from app.bot.handlers.admin_actions import admin_actions_router

# Фоновые задачи
from app.services.user_levels import update_all_user_levels
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
     # 1. Сначала регистрируем роутер с состояниями FSM.
    #    Его хендлеры самые специфичные.
    dp.include_router(admin_dialog_router) 
    
    # 2. Затем роутер с конкретными админскими командами.
    dp.include_router(admin_actions_router)
    
    # 3. В самом конце регистрируем "общий" роутер для обычных пользователей.
    dp.include_router(user_router)
    
    # Установка веб-хука
    webhook_url = f"{config.BASE_WEBHOOK_URL}{config.TELEGRAM_WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url, secret_token=config.TELEGRAM_WEBHOOK_SECRET)
    logger.info(f"Telegram webhook set to: {webhook_url}")
    
    # Запуск планировщика
    scheduler.add_job(update_all_user_levels, 'cron', hour=3, minute=0)
    scheduler.start()
    logger.info("Scheduler started...")
    
    yield
    
    # Корректное завершение работы
    await bot.delete_webhook()
    logger.info("Telegram webhook deleted.")
    
    scheduler.shutdown()
    logger.info("Scheduler shut down.")

app = FastAPI(
    title="Telegram Mini App Service",
    description="Backend for Frontend service for Telegram e-commerce app",
    version="0.1.0",
    lifespan=lifespan
)

# --- Порядок подключения роутеров FastAPI (не так важен, но лучше соблюдать логику) ---
# 1. Публичные и пользовательские
# Создаем "родительский" роутер для API
api_router = APIRouter(prefix="/api/v1")

# Подключаем все наши роутеры к нему
api_router.include_router(auth.router, tags=["Authentication"])
api_router.include_router(user.router, tags=["Users"])
api_router.include_router(catalog.router, tags=["Catalog"])
api_router.include_router(cart.router, tags=["Cart & Favorites"])
api_router.include_router(order.router, tags=["Orders"])
api_router.include_router(settings_router.router, tags=["Settings"])
api_router.include_router(admin_router.router, prefix="/admin", tags=["Admin"])

# Подключаем главный роутер к приложению
app.include_router(api_router)


# --- ВЕБ-ХУКИ ОСТАЮТСЯ В КОРНЕ ---
# Веб-хуки - это "системные" эндпоинты, их лучше не прятать под /api/v1
# WordPress и Telegram ожидают их по простым, предсказуемым путям.
app.include_router(telegram_router, tags=["Telegram Bot"])
app.include_router(wc_router, prefix="/internal/webhooks", tags=["Internal Webhooks"])