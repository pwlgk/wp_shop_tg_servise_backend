# app/services/user_updater.py
import asyncio
import logging
from app.db.session import SessionLocal
from app.models.user import User
from app.bot.core import bot
from app.services.user import update_user_profile_from_telegram

logger = logging.getLogger(__name__)

async def update_all_usernames_task():
    logger.info("--- Starting scheduled job: Update All Usernames ---")
    with SessionLocal() as db:
        users_to_update = db.query(User).all()
        updated_count = 0
        
        for user in users_to_update:
            try:
                # Делаем запрос к Telegram API
                chat_info = await bot.get_chat(chat_id=user.telegram_id)
                # Преобразуем в словарь
                telegram_user_data = chat_info.model_dump()
                
                # Используем нашу уже готовую функцию
                update_user_profile_from_telegram(db, user, telegram_user_data)
                updated_count += 1
                
                # Пауза, чтобы не превысить лимиты API (не более 30 запросов в сек)
                await asyncio.sleep(0.1) 
            except Exception as e:
                logger.warning(f"Could not update username for user {user.id}: {e}")
                
    logger.info(f"--- Finished username update. Processed {updated_count} users. ---")