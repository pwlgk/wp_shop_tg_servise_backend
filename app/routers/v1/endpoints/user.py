# app/routers/user.py
from fastapi import APIRouter, Depends
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.user import UserProfile, UserUpdate
from app.services import user as user_service
from app.dependencies import get_current_user, get_db # Возвращаем get_db
from sqlalchemy.orm import Session
from app.services import loyalty as loyalty_service # <-- Добавляем импорт
from pydantic import BaseModel # <-- Добавляем импорт
from app.schemas.loyalty import LoyaltyHistory, UserDashboard
from app.schemas.referral import ReferralInfo # <-- Импортируем новую схему
from app.services import loyalty as loyalty_service 
from app.services import referral as referral_service

router = APIRouter()


class UserBalanceResponse(BaseModel):
    balance: int
    level: str

@router.get("/users/me", response_model=UserProfile)
async def read_users_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db) # <-- Добавляем db
):
    return await user_service.get_user_profile(db, current_user) # <-- Передаем db


@router.put("/users/me", response_model=UserProfile)
async def update_users_me(
    user_update_data: UserUpdate,
    db: Session = Depends(get_db), # <-- Убедитесь, что эта зависимость есть
    current_user: User = Depends(get_current_user)
):
    """
    Обновление информации о текущем авторизованном пользователе.
    """
    # Передаем все три необходимых аргумента
    return await user_service.update_user_profile(db, current_user, user_update_data)

@router.get("/users/me/balance", response_model=UserBalanceResponse)
def get_user_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получение баланса бонусных баллов и уровня лояльности."""
    balance = loyalty_service.get_user_balance(db, current_user)
    return UserBalanceResponse(balance=balance, level=current_user.level)
    
@router.get("/users/me/loyalty-history", response_model=LoyaltyHistory)
def get_user_loyalty_details(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получение детализированной истории по программе лояльности,
    включая транзакции и сроки сгорания баллов.
    """
    return loyalty_service.get_user_loyalty_history(db, current_user)

@router.get("/users/me/dashboard", response_model=UserDashboard)
async def get_user_dashboard_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получение сводной информации ("приборная панель") для стартового экрана.
    Возвращает только самые необходимые данные для быстрой загрузки.
    """
    return await user_service.get_user_dashboard(db, current_user)

@router.get("/users/me/referral-info", response_model=ReferralInfo)
def get_referral_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получение информации и статистики по реферальной программе пользователя.
    """
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Вызываем функцию из правильного сервиса
    return referral_service.get_user_referral_info(db, current_user)