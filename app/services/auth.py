from datetime import datetime, timedelta
import json
import logging
import secrets
from typing import Any, Dict
import httpx
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from app.clients.woocommerce import wc_client
from app.core.config import settings
from app.core.redis import redis_client
from app.crud import user as crud_user
from app.crud import referral as crud_referral
from app.crud import loyalty as crud_loyalty
from app.crud import notification as crud_notification
from app.dependencies import get_db_context
from app.models.loyalty import LoyaltyTransaction
from app.models.user import User
from app.schemas.user import Token
from app.utils.telegram import validate_init_data
from app.db.session import SessionLocal
from app.services import settings as settings_service
from app.bot.services import notification as bot_notification_service

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
    user_info: Dict[str, Any],
    referral_code: str | None = None
) -> User:
    """
    Основная логика регистрации/получения пользователя.
    Использует собственную сессию БД для надежности.
    """
    telegram_id = user_info.get("id")
    if not telegram_id:
        raise ValueError("Telegram ID is missing in user_info")

    # --- ИСПОЛЬЗУЕМ ИЗОЛИРОВАННУЮ СЕССИЮ БД ---
    with get_db_context() as db:
        db_user = crud_user.get_user_by_telegram_id(db, telegram_id=telegram_id)
        is_new_user = False

        if not db_user:
            is_new_user = True
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
                        raise HTTPException(status_code=500, detail="User sync failed.")
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

        # --- БЛОК НАЧИСЛЕНИЯ БОНУСОВ ---
        # Выполняется для всех, кто был приглашен, но еще не получил бонус,
        # а также для всех новых, если активна общая акция.
        try:
            shop_settings = await settings_service.get_shop_settings(redis_client)
            
            # 1. Бонус за регистрацию по реферальной ссылке
            referral_link = crud_referral.get_referral_by_referred_id(db, referred_id=db_user.id)
            has_referral_bonus = db.query(LoyaltyTransaction).filter_by(user_id=db_user.id, type="promo_referral_welcome").first()
            if referral_link and not has_referral_bonus:
                bonus_amount = shop_settings.referral_welcome_bonus
                if bonus_amount > 0:
                    logger.info(f"Granting REFERRAL welcome bonus ({bonus_amount}) to user {db_user.id}")
                    crud_loyalty.create_transaction(db, user_id=db_user.id, points=bonus_amount, type="promo_referral_welcome")
                    crud_notification.create_notification(db, user_id=db_user.id, type="points_earned", title="Приветственный бонус!", message=f"Вы получили {bonus_amount} баллов за регистрацию по приглашению. Добро пожаловать!")
                    await bot_notification_service.send_welcome_bonus(db, db_user, bonus_amount)
            
            # 2. Общий приветственный бонус для всех новых пользователей
            if is_new_user and shop_settings.is_welcome_bonus_active:
                bonus_amount = shop_settings.welcome_bonus_amount
                if bonus_amount > 0:
                    logger.info(f"Granting GENERAL welcome bonus ({bonus_amount}) to user {db_user.id}")
                    crud_loyalty.create_transaction(db, user_id=db_user.id, points=bonus_amount, type="promo_welcome")
                    crud_notification.create_notification(db, user_id=db_user.id, type="points_earned", title="Добро пожаловать!", message=f"Вам начислен приветственный бонус: {bonus_amount} баллов!")
                    await bot_notification_service.send_welcome_bonus(db, db_user, bonus_amount)

        except Exception as e:
            # Теперь `db_user` гарантированно определен
            logger.error(f"Failed during bonus granting for new user {db_user.id}", exc_info=True)

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
    db_user = await register_or_get_user(user_info=user_info, referral_code=referral_code)
    # Генерируем JWT токен
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(db_user.id), "tg_id": str(db_user.telegram_id)},
        expires_delta=access_token_expires,
    )

    return Token(access_token=access_token)