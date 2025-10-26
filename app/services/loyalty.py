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
    """
    Подсчитывает текущий баланс пользователя как простую сумму ВСЕХ его транзакций.
    """
    return crud_loyalty.get_user_balance(db, user_id=user.id)
    
def add_cashback_for_order(db: Session, user: User, order_total: float, order_id_wc: int) -> int:
    """
    Начисляет кешбэк за выполненный заказ.
    """
    user_level = user.level
    level_settings = settings.LOYALTY_SETTINGS.get(user_level, settings.LOYALTY_SETTINGS.get("bronze", {}))
    
    cashback_percent = level_settings.get("cashback_percent", 0)
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
    """
    Собирает полную историю по программе лояльности для пользователя.
    """
    balance = get_user_balance(db, user)
    # Предполагаем, что crud_loyalty.get_user_transactions существует и пагинирует
    transactions = crud_loyalty.get_user_transactions(db, user_id=user.id, limit=50) # Ограничим для истории
    
    return LoyaltyHistory(
        balance=balance,
        level=user.level,
        transactions=transactions
    )

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
    
    Args:
        db: Сессия SQLAlchemy.
        user: Объект пользователя.
        points_to_spend: Количество баллов для списания (положительное число).
        order_id_wc: ID заказа WooCommerce.
        is_pending: Если True, создает транзакцию 'order_pending_spend', иначе 'order_spend'.
    
    Returns:
        Созданный объект LoyaltyTransaction.
        
    Raises:
        HTTPException: Если на балансе недостаточно баллов.
    """
    if points_to_spend <= 0:
        # Это не должно происходить, если логика на уровне выше корректна,
        # но добавим защиту на всякий случай.
        raise ValueError("Количество списываемых баллов должно быть положительным.")

    # --- АТОМАРНЫЙ БЛОК ДЛЯ ПРОВЕРКИ БАЛАНСА ---
    # `with_for_update()` блокирует строки, которые мы выбираем, до конца транзакции.
    # Это гарантирует, что никакой другой параллельный процесс не сможет изменить баланс,
    # пока мы принимаем решение о списании.
    
    # Считаем текущий баланс с блокировкой
    current_balance = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user.id
    ).with_for_update().scalar() or 0
    
    # Проверяем, достаточно ли баллов
    if points_to_spend > current_balance:
        # Откатывать транзакцию здесь не нужно, вызывающая функция обработает HTTPException
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Недостаточно бонусных баллов. Возможно, ваш баланс изменился."
        )

    # Определяем тип транзакции на основе флага is_pending
    transaction_type = "order_pending_spend" if is_pending else "order_spend"

    # Создаем транзакцию на списание
    transaction = crud_loyalty.create_transaction(
        db=db,
        user_id=user.id,
        points=-points_to_spend, # Отрицательное число
        type=transaction_type,
        order_id_wc=order_id_wc
    )
    
    # Используем flush, чтобы получить ID транзакции, не завершая транзакцию.
    # Это позволит вызывающей функции работать с созданным объектом.
    db.flush()
    
    logger.info(
        f"Created transaction for user {user.id}: {transaction_type} of {-points_to_spend} points. "
        f"Balance before: {current_balance}, Balance after (uncommitted): {current_balance - points_to_spend}"
    )
    
    return transaction