# app/crud/loyalty.py

from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from datetime import datetime, timedelta, timezone

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
    ТРЕБУЕТ ВНЕШНЕГО ВЫЗОВА db.commit().
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

# --- Логика для Сгорания и Уведомлений (Полностью Переработана) ---

def get_unspent_expiring_soon(db: Session, user_id: int, days_left: int) -> int:
    """
    Рассчитывает, сколько баллов у пользователя РЕАЛЬНО сгорит через `days_left` дней,
    используя FIFO-логику.
    """
    target_date = (datetime.now(timezone.utc) + timedelta(days=days_left)).date()

    # 1. Получаем все транзакции пользователя в хронологическом порядке
    all_transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.asc()).all()

    # 2. Моделируем FIFO
    balance = 0
    expiring_on_target_date = 0
    for t in all_transactions:
        balance += t.points
        if t.points > 0 and t.expires_at and t.expires_at.date() == target_date:
            # Эта транзакция должна сгореть в целевую дату.
            # Сколько от нее ОСТАЛОСЬ на текущий момент?
            unspent_part = min(t.points, max(0, balance))
            expiring_on_target_date += unspent_part
            
    return expiring_on_target_date


def calculate_points_to_expire_for_user(db: Session, user_id: int) -> int:
    """
    Рассчитывает, сколько баллов должно сгореть у пользователя ПРЯМО СЕЙЧАС,
    используя FIFO-логику.
    """
    # 1. Получаем все транзакции пользователя в хронологическом порядке
    all_transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.asc()).all()

    # 2. Моделируем FIFO
    balance = 0
    total_to_expire = 0
    now = datetime.now(timezone.utc)

    for t in all_transactions:
        balance += t.points
        # Проверяем, должна ли была эта транзакция сгореть
        if t.points > 0 and t.expires_at and t.expires_at < now:
            # Какая часть от этой "сгоревшей" транзакции осталась не потраченной к моменту ее сгорания?
            # `balance` на этом шаге - это и есть остаток.
            # Мы можем сжечь не больше, чем сама транзакция, и не больше, чем было на балансе.
            unspent_part = min(t.points, max(0, balance))
            total_to_expire += unspent_part
            # "Сжигаем" эту часть из нашего симулированного баланса
            balance -= unspent_part
            
    return total_to_expire


def get_users_with_potentially_expired_points(db: Session) -> List[Tuple[int]]:
    """
    Возвращает ID пользователей, у которых есть положительные транзакции,
    у которых `expires_at` уже в прошлом.
    """
    return db.query(LoyaltyTransaction.user_id).filter(
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.expires_at.isnot(None),
        LoyaltyTransaction.expires_at < datetime.now(timezone.utc)
    ).distinct().all()

def mark_positive_transactions_as_processed(db: Session, user_id: int):
    """
    Убирает `expires_at` у всех просроченных положительных транзакций пользователя,
    чтобы они не участвовали в будущих расчетах сгорания.
    """
    db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id,
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.expires_at.isnot(None),
        LoyaltyTransaction.expires_at < datetime.now(timezone.utc)
    ).update({"expires_at": None})