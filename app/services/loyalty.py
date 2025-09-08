# app/services/loyalty.py
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.models.loyalty import LoyaltyTransaction
from app.schemas.loyalty import LoyaltyHistory
from app.crud import loyalty as crud_loyalty
from app.models.user import User
from app.core.config import settings
import logging
from sqlalchemy.orm import Session
from sqlalchemy import select, func # <-- Добавляем импорт
from fastapi import HTTPException, status # <-- Добавляем импорт

logger = logging.getLogger(__name__)
def get_user_balance(db: Session, user: User) -> int:
    return crud_loyalty.get_user_balance(db, user_id=user.id)
    
def add_cashback_for_order(db: Session, user: User, order_total: float, order_id_wc: int):
    """Начисляет кешбэк за выполненный заказ."""
    user_level = user.level
    # Используем настройки из settings
    level_settings = settings.LOYALTY_SETTINGS.get(user_level, settings.LOYALTY_SETTINGS["bronze"])
    
    cashback_percent = level_settings["cashback_percent"]
    points_to_add = int(order_total * (cashback_percent / 100))

    if points_to_add > 0:
        # Используем настройки из settings
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
    Безопасно списывает баллы, используя блокировку строк для предотвращения "гонки состояний".
    """
    if points_to_spend <= 0:
        return True # Нечего списывать

    # --- АТОМАРНЫЙ БЛОК ---
    # `with_for_update()` блокирует строки, которые мы выбираем, до конца транзакции.
    # Любой другой параллельный запрос, который попытается прочитать эти же строки
    # с блокировкой, будет ждать, пока текущая транзакция не завершится.
    
    # В PostgreSQL это `SELECT ... FOR UPDATE`
    active_transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user.id,
        (LoyaltyTransaction.expires_at == None) | (LoyaltyTransaction.expires_at > datetime.utcnow())
    ).with_for_update().all()

    current_balance = sum(t.points for t in active_transactions)
    
    if points_to_spend > current_balance:
        # Если здесь не хватает баланса, это означает, что другой процесс
        # уже успел списать баллы. Откатываем транзакцию и сообщаем об ошибке.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Недостаточно бонусных баллов. Возможно, вы уже потратили их в другом заказе."
        )

    # Если баланса хватает, создаем транзакцию на списание
    crud_loyalty.create_transaction(
        db=db,
        user_id=user.id,
        points=-points_to_spend, # Отрицательное число
        type="order_spend",
        order_id_wc=order_id_wc
    )
    
    # Коммит всей транзакции (включая списание) будет сделан в вызывающей функции
    return True