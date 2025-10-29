# app/services/loyalty.py

import logging
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.crud import loyalty as crud_loyalty
from app.models.user import User
from app.models.loyalty import LoyaltyTransaction
from app.schemas.loyalty import LoyaltyHistory
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_user_balance(db: Session, user: User) -> int:
    """Подсчитывает текущий баланс пользователя как простую сумму ВСЕХ его транзакций."""
    return crud_loyalty.get_user_balance(db, user_id=user.id)
    
def add_cashback_for_order(db: Session, user: User, order_total: float, order_id_wc: int) -> int:
    """Начисляет кешбэк за выполненный заказ."""
    user_level = user.level
    level_settings = settings.LOYALTY_SETTINGS.get(user_level, settings.LOYALTY_SETTINGS.get("bronze", {}))
    
    cashback_percent = level_settings.get("cashback_percent", 0)
    points_to_add = int(order_total * (cashback_percent / 100))

    if points_to_add > 0:
        expires_at = datetime.utcnow() + timedelta(days=settings.POINTS_LIFETIME_DAYS)
        crud_loyalty.create_transaction(
            db=db, user_id=user.id, points=points_to_add, type="order_earn",
            order_id_wc=order_id_wc, expires_at=expires_at
        )
        logger.info(f"Added {points_to_add} points to user {user.id} for order {order_id_wc}")
    return points_to_add

def get_user_loyalty_history(db: Session, user: User) -> LoyaltyHistory:
    """Собирает полную историю по программе лояльности для пользователя."""
    balance = get_user_balance(db, user)
    transactions = crud_loyalty.get_user_transactions(db, user_id=user.id, limit=50)
    
    return LoyaltyHistory(balance=balance, level=user.level, transactions=transactions)

def spend_points(
    db: Session,
    user: User,
    points_to_spend: int,
    order_id_wc: int | None,
    is_pending: bool = False
) -> LoyaltyTransaction:
    """
    Безопасно списывает или резервирует баллы, используя блокировку строк
    для предотвращения "гонки состояний", и возвращает созданную транзакцию.
    """
    if points_to_spend <= 0:
        raise ValueError("Количество списываемых баллов должно быть положительным.")

    # --- НАЧАЛО ИСПРАВЛЕНИЯ ---
    # Шаг 1: Выбираем и БЛОКИРУЕМ все транзакции пользователя.
    # Это создаст SQL-запрос `SELECT ... FOR UPDATE`.
    all_transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user.id
    ).with_for_update().all()

    # Шаг 2: Считаем баланс на стороне Python, работая с уже заблокированными данными.
    current_balance = sum(t.points for t in all_transactions)
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
    
    if points_to_spend > current_balance:
        # Откатывать транзакцию не нужно, так как мы еще ничего не изменили.
        # Просто вызываем исключение, которое приведет к откату на верхнем уровне.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Недостаточно бонусных баллов. Возможно, ваш баланс изменился."
        )

    transaction_type = "order_pending_spend" if is_pending else "order_spend"

    transaction = crud_loyalty.create_transaction(
        db=db,
        user_id=user.id,
        points=-points_to_spend,
        type=transaction_type,
        order_id_wc=order_id_wc
    )
    
    db.flush()
    
    logger.info(
        f"Created transaction for user {user.id}: {transaction_type} of {-points_to_spend} points. "
        f"Balance before: {current_balance}, Balance after (uncommitted): {current_balance - points_to_spend}"
    )
    
    return transaction