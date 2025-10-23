# app/services/loyalty.py
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.models.loyalty import LoyaltyTransaction
from app.schemas.loyalty import LoyaltyHistory
from app.crud import loyalty as crud_loyalty
from app.models.user import User
from app.core.config import settings
import logging
from sqlalchemy import func
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

def get_user_balance(db: Session, user: User) -> int:
    """
    Подсчитывает текущий баланс пользователя как сумму ВСЕХ его транзакций.
    Задача expire_points_task отвечает за создание компенсирующих транзакций.
    """
    # --- ИСПРАВЛЕНИЕ: Убираем фильтрацию по дате ---
    balance = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user.id
    ).scalar()
    
    return balance or 0

def add_cashback_for_order(db: Session, user: User, order_total: float, order_id_wc: int) -> int:
    """Начисляет кешбэк за выполненный заказ."""
    user_level = user.level
    level_settings = settings.LOYALTY_SETTINGS.get(user_level, settings.LOYALTY_SETTINGS["bronze"])
    
    cashback_percent = level_settings["cashback_percent"]
    points_to_add = int(order_total * (cashback_percent / 100))

    if points_to_add > 0:
        expires_at = datetime.utcnow() + timedelta(days=settings.POINTS_LIFETIME_DAYS)
        
        crud_loyalty.create_transaction(
            db=db,
            user_id=user.id,
            points=points_to_add,
            type="order_earn",
            order_id_wc=order_id_wc,
            expires_at=expires_at
        )
        logger.info(f"Added {points_to_add} points to user {user.id} for order {order_id_wc}")
    return points_to_add

def get_user_loyalty_history(db: Session, user: User) -> LoyaltyHistory:
    """Собирает полную историю по программе лояльности для пользователя."""
    balance = get_user_balance(db, user)
    transactions = crud_loyalty.get_user_transactions(db, user_id=user.id)
    
    return LoyaltyHistory(
        balance=balance,
        level=user.level,
        transactions=transactions
    )

def spend_points(db: Session, user: User, points_to_spend: int, order_id_wc: int) -> bool:
    """
    Безопасно списывает баллы, используя блокировку строк.
    Баланс считается как простая сумма всех транзакций.
    """
    if points_to_spend <= 0:
        return True

    # --- ИСПРАВЛЕНИЕ: Убираем фильтрацию по дате из запроса на блокировку ---
    # Блокируем все транзакции пользователя, чтобы получить точный SUM
    all_transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user.id
    ).with_for_update().all()

    current_balance = sum(t.points for t in all_transactions)
    
    if points_to_spend > current_balance:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Недостаточно бонусных баллов. Возможно, вы уже потратили их в другом заказе."
        )

    crud_loyalty.create_transaction(
        db=db,
        user_id=user.id,
        points=-points_to_spend,
        type="order_spend",
        order_id_wc=order_id_wc
    )
    
    return True