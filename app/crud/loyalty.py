# app/crud/loyalty.py

from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from datetime import datetime, timezone

from app.models.loyalty import LoyaltyTransaction

# --- Базовые CRUD-операции ---

def create_transaction(
    db: Session, 
    user_id: int, 
    points: int, 
    type: str, 
    order_id_wc: int = None, 
    expires_at: datetime = None,
    related_transaction_id: int = None
) -> LoyaltyTransaction:
    """
    Создает объект транзакции и добавляет его в сессию.
    Требует внешнего вызова db.commit().
    """
    transaction = LoyaltyTransaction(
        user_id=user_id,
        points=points,
        type=type,
        order_id_wc=order_id_wc,
        expires_at=expires_at,
        related_transaction_id=related_transaction_id
    )
    db.add(transaction)
    return transaction

def get_user_transactions(
    db: Session, 
    user_id: int, 
    skip: int = 0, 
    limit: int = 20
) -> List[LoyaltyTransaction]:
    """Получает пагинированный список ВСЕХ транзакций пользователя (от новых к старым)."""
    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.desc()).offset(skip).limit(limit).all()

def count_user_transactions(db: Session, user_id: int) -> int:
    """Подсчитывает общее количество транзакций у пользователя."""
    return db.query(LoyaltyTransaction).filter(LoyaltyTransaction.user_id == user_id).count()

def get_spend_transaction_by_order_id(db: Session, user_id: int, order_id_wc: int) -> LoyaltyTransaction | None:
    """Находит транзакцию списания баллов ('order_spend') для конкретного заказа."""
    return db.query(LoyaltyTransaction).filter_by(
        user_id=user_id, order_id_wc=order_id_wc, type="order_spend"
    ).first()

# --- Расчетные CRUD-функции ---

def get_user_balance(db: Session, user_id: int) -> int:
    """Подсчитывает текущий баланс пользователя как простую сумму ВСЕХ его транзакций."""
    balance = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id
    ).scalar()
    return balance or 0

def get_total_referral_earnings(db: Session, user_id: int) -> int:
    """Подсчитывает общую сумму баллов, заработанных пользователем на реферальной программе."""
    total_earned = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id,
        LoyaltyTransaction.type == "referral_earn"
    ).scalar()
    return total_earned or 0

# --- Функции для Сгорания и Уведомлений (Максимально Упрощены) ---

def get_all_user_transactions_chronological(db: Session, user_id: int) -> List[LoyaltyTransaction]:
    """Получает ВСЕ транзакции пользователя в хронологическом порядке (от старых к новым)."""
    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.asc()).all()

def get_users_with_potentially_expired_points(db: Session) -> List[Tuple[int]]:
    """
    Возвращает ID пользователей, у которых есть положительные транзакции с датой сгорания в прошлом.
    Это "кандидаты" для проверки на сгорание.
    """
    return db.query(LoyaltyTransaction.user_id).filter(
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.expires_at.isnot(None),
        LoyaltyTransaction.expires_at < datetime.now(timezone.utc)
    ).distinct().all()

def mark_positive_transactions_as_processed(db: Session, user_id: int):
    """
    "Закрывает" просроченные положительные транзакции, убирая у них дату сгорания (`expires_at = NULL`),
    чтобы они больше не участвовали в будущих расчетах сгорания.
    """
    db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id,
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.expires_at.isnot(None),
        LoyaltyTransaction.expires_at < datetime.now(timezone.utc)
    ).update({"expires_at": None}, synchronize_session=False)


def find_pending_spend(db: Session, user_id: int) -> LoyaltyTransaction | None:
    """Находит самую последнюю 'резервную' транзакцию для пользователя."""
    return db.query(LoyaltyTransaction).filter_by(
        user_id=user_id,
        type='order_pending_spend'
    ).order_by(LoyaltyTransaction.id.desc()).first()