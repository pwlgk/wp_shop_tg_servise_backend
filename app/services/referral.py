# app/services/referral.py
import secrets # <-- Убедитесь, что импорт есть
from sqlalchemy.orm import Session
from app.crud import referral as crud_referral
from app.crud import loyalty as crud_loyalty
from app.crud import user as crud_user # <-- Добавляем CRUD для пользователя
from app.models.user import User
from app.schemas.referral import ReferralInfo
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
def get_user_referral_info(db: Session, user: User) -> ReferralInfo:
    """Собирает полную статистику по реферальной программе для пользователя."""
    
    # --- ИСПРАВЛЕННАЯ ЛОГИКА ГЕНЕРАЦИИ ССЫЛКИ ---
    
    # 1. Проверяем, есть ли у пользователя реферальный код
    if not user.referral_code:
        # 2. Если кода нет, генерируем его и сохраняем в БД
        logger.info(f"User {user.id} has no referral code. Generating a new one.")
        new_code = secrets.token_urlsafe(8)
        # Убедимся, что код уникален (крайне маловероятная коллизия, но для надежности)
        while crud_user.get_user_by_referral_code(db, code=new_code):
            new_code = secrets.token_urlsafe(8)
        
        user.referral_code = new_code
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Assigned new referral code '{new_code}' to user {user.id}")

    # 3. Формируем ссылку, только если код точно есть
    referral_link = ""
    if user.referral_code:
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "your_bot")
        referral_link = f"https://t.me/{bot_username}?start=ref_{user.referral_code}"
    
    # -----------------------------------------------
    
    # Остальной код остается без изменений
    pending_count = crud_referral.count_referrals_by_status(db, referrer_id=user.id, status="pending")
    completed_count = crud_referral.count_referrals_by_status(db, referrer_id=user.id, status="completed")
    total_earned = crud_loyalty.get_total_referral_earnings(db, user_id=user.id)
    
    return ReferralInfo(
        referral_link=referral_link,
        pending_referrals=pending_count,
        completed_referrals=completed_count,
        total_earned=total_earned
    )