# app/services/bot_status_updater.py
import logging
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.user import User
from app.bot.services.notification import ping_user # Импортируем наш пинг

logger = logging.getLogger(__name__)

async def check_inactive_bots_task():
    """
    Фоновая задача, которая пингует пользователей, помеченных как
    недоступные, чтобы проверить, не разблокировали ли они бота.
    """
    logger.info("--- Starting scheduled job: Check Inactive Bots ---")
    
    with SessionLocal() as db:
        try:
            # Находим всех, у кого бот помечен как недоступный
            users_to_check = db.query(User).filter(User.bot_accessible == False).all()
            
            if not users_to_check:
                logger.info("No inactive bots to check.")
                return

            logger.info(f"Found {len(users_to_check)} users with inactive bots to ping.")
            
            for user in users_to_check:
                # Пингуем каждого. Функция ping_user сама обновит статус в БД, если нужно.
                await ping_user(db, user)
        
        except Exception as e:
            logger.error("An error occurred during inactive bots check task", exc_info=True)

    logger.info("--- Finished scheduled job: Check Inactive Bots ---")