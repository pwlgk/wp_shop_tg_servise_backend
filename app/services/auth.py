# app/services/auth.py

import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict

import httpx
from fastapi import Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.bot.services import notification as bot_notification_service

# Импорты сгруппированы для читаемости
from app.clients.woocommerce import wc_client
from app.core.config import settings
from app.core.redis import redis_client
from app.crud import (
    loyalty as crud_loyalty,
    notification as crud_notification,
    referral as crud_referral,
    user as crud_user,
)
from app.db.session import SessionLocal
from app.models.loyalty import LoyaltyTransaction
from app.models.user import User
from app.schemas.user import Token
from app.services import settings as settings_service
from app.services import user as user_service
from app.utils.telegram import validate_init_data

logger = logging.getLogger(__name__)


def get_db():
    """Зависимость FastAPI для получения сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Создает JWT токен."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def register_or_get_user(
    db: Session,
    user_info: Dict[str, Any],
    referral_code: str | None = None
) -> User:
    """
    Основная логика регистрации/получения пользователя.
    Использует переданную сессию `db` для всех операций.
    """
    telegram_id = user_info.get("id")
    if not telegram_id:
        raise ValueError("Telegram ID is missing in user_info")

    db_user = crud_user.get_user_by_telegram_id(db, telegram_id=telegram_id)
    is_new_user = not bool(db_user)

    if is_new_user:
        logger.info(f"User with telegram_id {telegram_id} not found in local DB. Creating new user.")
        
        first_name = user_info.get("first_name", "")
        last_name = user_info.get("last_name", "")
            
        try:
            new_wc_user_data = {
                "email": f"{telegram_id}@telegram.user",
                "username": str(telegram_id),
                "first_name": first_name,
                "last_name": last_name,
            }
            created_wc_user = await wc_client.post("wc/v3/customers", json=new_wc_user_data)
            wordpress_id = created_wc_user["id"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and e.response.json().get("code") == "registration-error-email-exists":
                logger.warning("User already exists in WooCommerce. Attempting to sync.")
                existing_wc_user_response = await wc_client.get("wc/v3/customers", params={"email": f"{telegram_id}@telegram.user"})
                existing_wc_users = existing_wc_user_response.json()
                if existing_wc_users:
                    wordpress_id = existing_wc_users[0]["id"]
                else:
                    # Этого никогда не должно произойти, но лучше обработать
                    raise HTTPException(status_code=500, detail="User sync failed: WC user exists but could not be retrieved.")
            else:
                logger.error(f"Failed to create user in WooCommerce: {e.response.text}", exc_info=True)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not create user in WordPress.")
            
        new_referral_code = secrets.token_urlsafe(8)
        while crud_user.get_user_by_referral_code(db, code=new_referral_code):
            new_referral_code = secrets.token_urlsafe(8)
        
        db_user = crud_user.create_user(
            db, telegram_id=telegram_id, wordpress_id=wordpress_id,
            username=user_info.get("username"), referral_code=new_referral_code,
            first_name=first_name, last_name=last_name
        )

        if referral_code:
            referrer = crud_user.get_user_by_referral_code(db, code=referral_code)
            if referrer and referrer.id != db_user.id:
                crud_referral.create_referral(db, referrer_id=referrer.id, referred_id=db_user.id)
                logger.info(f"Referral link created: referrer_id={referrer.id} -> referred_id={db_user.id}")

    if is_new_user:
        try:
            shop_settings = await settings_service.get_shop_settings(redis_client)
            referral_link = crud_referral.get_referral_by_referred_id(db, referred_id=db_user.id)
            
            bonus_amount = 0
            bonus_type = None
            bonus_title = ""
            bonus_message = ""
            
            if referral_link and shop_settings.referral_welcome_bonus > 0:
                bonus_amount = shop_settings.referral_welcome_bonus
                bonus_type = "promo_referral_welcome"
                bonus_title = "Приветственный бонус!"
                bonus_message = f"Вы получили {bonus_amount} баллов за регистрацию по приглашению. Добро пожаловать!"
                logger.info(f"Granting REFERRAL welcome bonus ({bonus_amount}) to user {db_user.id}")
            
            elif shop_settings.is_welcome_bonus_active and shop_settings.welcome_bonus_amount > 0:
                bonus_amount = shop_settings.welcome_bonus_amount
                bonus_type = "promo_welcome"
                bonus_title = "Добро пожаловать!"
                bonus_message = f"Вам начислен приветственный бонус: {bonus_amount} баллов!"
                logger.info(f"Granting GENERAL welcome bonus ({bonus_amount}) to user {db_user.id}")
            
            if bonus_amount > 0 and bonus_type:
                crud_loyalty.create_transaction(db, user_id=db_user.id, points=bonus_amount, type=bonus_type)
                crud_notification.create_notification(db, user_id=db_user.id, type="points_earned", title=bonus_title, message=bonus_message)
                await bot_notification_service.send_welcome_bonus(db, db_user, bonus_amount)
        except Exception as e:
            logger.error(f"Failed during bonus granting for new user {db_user.id}", exc_info=True)

    return db_user


async def authenticate_telegram_user(init_data: str, db: Session = Depends(get_db)) -> Token:
    """
    Функция для эндпоинта /auth/telegram.
    Валидирует initData, регистрирует/находит пользователя, обновляет профиль и возвращает JWT.
    """
    is_valid, user_data_from_tg = validate_init_data(init_data)
    
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram initData")
    
    user_info = json.loads(user_data_from_tg.get("user", "{}"))
    
    start_param = user_data_from_tg.get("start_param")
    referral_code = None
    if start_param and start_param.startswith("ref_"):
        referral_code = start_param.split("ref_")[1]
    
    # --- ИСПРАВЛЕНИЕ: Передаем сессию `db` ---
    db_user = await register_or_get_user(db, user_info=user_info, referral_code=referral_code)
    
    user_service.update_user_profile_from_telegram(db, db_user, user_info)
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(db_user.id), "tg_id": str(db_user.telegram_id)},
        expires_delta=access_token_expires,
    )

    return Token(access_token=access_token)