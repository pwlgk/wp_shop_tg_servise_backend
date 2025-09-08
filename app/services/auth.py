# app/services/auth.py
import json
import secrets
from datetime import timedelta, datetime
from typing import Dict, Any

from fastapi import Depends, HTTPException, status
import httpx
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from app.clients.woocommerce import wc_client
from app.core.config import settings
from app.crud import user as crud_user
from app.crud import referral as crud_referral
# --- ИСПРАВЛЕНИЯ ЗДЕСЬ ---
from app.crud import loyalty as crud_loyalty
from app.services import settings as settings_service
from app.bot.services import notification as notification_service
from app.core.redis import redis_client
# -------------------------
from app.models.user import User
from app.schemas.user import Token
from app.utils.telegram import validate_init_data
from app.db.session import SessionLocal
import logging

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Создает JWT токен."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def register_or_get_user(
    db: Session,
    user_info: Dict[str, Any],
    referral_code: str | None = None
) -> User:
    telegram_id = user_info.get("id")
    if not telegram_id:
        raise ValueError("Telegram ID is missing in user_info")

    db_user = crud_user.get_user_by_telegram_id(db, telegram_id=telegram_id)

    if db_user:
        # Если пользователь уже есть в нашей БД, все в порядке.
        return db_user

    # --- НОВАЯ УМНАЯ ЛОГИКА ---
    # Пользователя нет в нашей БД. Попробуем создать его в WooCommerce.
    logger.info(f"User with telegram_id {telegram_id} not found in local DB. Attempting to create/find in WooCommerce.")
    
    try:
        new_wc_user_data = {
            "email": f"{telegram_id}@telegram.user",
            "username": str(telegram_id),
            "first_name": user_info.get("first_name", ""),
            "last_name": user_info.get("last_name", ""),
        }
        created_wc_user = await wc_client.post("wc/v3/customers", json=new_wc_user_data)
        wordpress_id = created_wc_user["id"]
        
    except httpx.HTTPStatusError as e:
        # Проверяем, не является ли ошибка "email уже существует"
        if e.response.status_code == 400:
            response_json = e.response.json()
            if response_json.get("code") == "registration-error-email-exists":
                logger.info("User already exists in WooCommerce. Attempting to sync.")
                # Пользователь уже есть в WP! Найдем его по email.
                existing_wc_user_response = await wc_client.get("wc/v3/customers", params={"email": f"{telegram_id}@telegram.user"})
                existing_wc_users = existing_wc_user_response.json()
                if existing_wc_users:
                    wordpress_id = existing_wc_users[0]["id"]
                else:
                    # Очень странная ситуация, email есть, а найти не можем. Падаем.
                    raise HTTPException(status_code=500, detail="User sync failed: WC user exists but could not be retrieved.")
            else:
                # Другая ошибка 400 от WooCommerce
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not create user in WordPress due to a client error.")
        else:
            # Любая другая ошибка (5xx, таймаут и т.д.)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not create user in WordPress.")
    
    # К этому моменту у нас гарантированно есть wordpress_id, либо новый, либо найденный
    
    # Генерируем реферальный код
    new_referral_code = secrets.token_urlsafe(8)
    while crud_user.get_user_by_referral_code(db, code=new_referral_code):
        new_referral_code = secrets.token_urlsafe(8)
    
    # Создаем пользователя в нашей БД
    db_user = crud_user.create_user(
        db, telegram_id=telegram_id, wordpress_id=wordpress_id,
        username=user_info.get("username"), referral_code=new_referral_code
    )
    shop_settings = await settings_service.get_shop_settings(redis_client) # Нужен доступ к Redis
    if shop_settings.is_welcome_bonus_active and shop_settings.welcome_bonus_amount > 0:
        crud_loyalty.create_transaction(
            db, user_id=db_user.id, points=shop_settings.welcome_bonus_amount, type="promo_welcome"
        )
        await notification_service.send_welcome_bonus(db, db_user, shop_settings.welcome_bonus_amount)
    # 4. Если был передан код пригласившего, создаем реферальную связь
    if referral_code:
        referrer = crud_user.get_user_by_referral_code(db, code=referral_code)
        # Проверяем, что реферер существует и пользователь не пригласил сам себя
        if referrer and referrer.id != db_user.id:
            crud_referral.create_referral(db, referrer_id=referrer.id, referred_id=db_user.id)
            logger.info(f"Referral link created: referrer_id={referrer.id} -> referred_id={db_user.id}")

    return db_user

async def authenticate_telegram_user(init_data: str, db: Session = Depends(get_db)) -> Token:
    """
    Функция для эндпоинта /auth/telegram.
    Валидирует initData, регистрирует/находит пользователя и возвращает JWT.
    """
    is_valid, user_data_from_tg = validate_init_data(init_data)
    
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData",
        )
    
    user_info_str = user_data_from_tg.get("user", "{}")
    user_info = json.loads(user_info_str)
    
    # Реферальный код может быть передан в start_param
    start_param = user_data_from_tg.get("start_param")
    referral_code = None
    if start_param and start_param.startswith("ref_"):
        referral_code = start_param.split("ref_")[1]
    
    # Вызываем нашу основную функцию для регистрации/получения пользователя
    db_user = await register_or_get_user(db, user_info=user_info, referral_code=referral_code)

    # Генерируем JWT токен
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(db_user.id), "tg_id": str(db_user.telegram_id)},
        expires_delta=access_token_expires,
    )

    return Token(access_token=access_token)