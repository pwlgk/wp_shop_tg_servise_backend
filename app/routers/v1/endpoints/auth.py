# app/routers/v1/auth.py

from fastapi import APIRouter, Depends, Request # <--- Убедитесь, что Request импортирован
from sqlalchemy.orm import Session
from app.schemas.user import TelegramLoginData, Token
from app.services.auth import authenticate_telegram_user, get_db
from app.core.limiter import limiter

router = APIRouter()

@router.get("/")
def read_root():
    return {"status": "ok"}


# --- ФИНАЛЬНАЯ ВЕРСИЯ С ДЕКОРАТОРОМ ---
@router.post("/auth/telegram", response_model=Token)
@limiter.limit("5/minute") # <-- Применяем лимитер как декоратор
async def login_via_telegram(
    request: Request, # <--- request ОБЯЗАТЕЛЕН, чтобы декоратор работал
    login_data: TelegramLoginData,
    db: Session = Depends(get_db)
):
    """
    Аутентифицирует пользователя с помощью Telegram InitData.
    Защищено лимитом в 5 запросов в минуту с одного IP.
    """
    # Мы больше не вызываем лимитер вручную здесь
    token = await authenticate_telegram_user(login_data.init_data, db)
    return token