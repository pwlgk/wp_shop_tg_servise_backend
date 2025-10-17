# app/routers/auth.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.schemas.user import TelegramLoginData, Token
from app.services.auth import authenticate_telegram_user, get_db

router = APIRouter()

@router.get("/")
def read_root():
    return {"status": "ok"}


@router.post("/auth/telegram", response_model=Token)
async def login_via_telegram(
    login_data: TelegramLoginData,
    db: Session = Depends(get_db)
):
    """
    Аутентифицирует пользователя с помощью Telegram InitData.
    """
    token = await authenticate_telegram_user(login_data.init_data, db)
    return token