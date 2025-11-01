# app/routers/auth.py
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.schemas.user import TelegramLoginData, Token
from app.services.auth import authenticate_telegram_user, get_db
from app.core.limiter import limiter
router = APIRouter()

@router.get("/")
def read_root():
    return {"status": "ok"}


@router.post("/auth/telegram", response_model=Token)
async def login_via_telegram(
    request: Request, # <--- Добавляем request как аргумент
    login_data: TelegramLoginData,
    db: Session = Depends(get_db)
):
    """
    Аутентифицирует пользователя с помощью Telegram InitData.
    Защищено лимитом в 5 запросов в минуту с одного IP.
    """
    # --- ЯВНЫЙ ВЫЗОВ ЛИМИТЕРА ---
    # Получаем доступ к лимитеру через состояние приложения
    limiter_instance = request.app.state.limiter
    # Вызываем проверку вручную
    await limiter_instance.limit("5/minute")(request)
    # -----------------------------
    
    # Дальнейшая логика остается без изменений
    token = await authenticate_telegram_user(login_data.init_data, db)
    return token