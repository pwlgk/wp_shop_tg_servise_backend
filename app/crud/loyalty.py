# app/crud/loyalty.py

from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func, select, update, cast, Date
from collections import deque
from datetime import datetime, timedelta, timezone

from app.models.loyalty import LoyaltyTransaction
from app.models.user import User


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
    # db.flush() можно использовать, чтобы получить ID до коммита, если нужно
    # db.refresh(transaction)
    return transaction


def get_all_user_transactions_chronological(db: Session, user_id: int) -> List[LoyaltyTransaction]:
    """Получает ВСЕ транзакции пользователя в хронологическом порядке."""
    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.asc()).all()


def get_user_balance(db: Session, user_id: int) -> int:
    """
    Подсчитывает итоговый баланс пользователя путем простого суммирования
    всех его транзакций (положительных и отрицательных).
    
    Эта функция полагается на то, что фоновая задача `expire_points_task`
    корректно создает "сжигающие" ('expired') отрицательные транзакции
    для поддержания баланса в актуальном состоянии.
    """
    
    # Выполняем один-единственный SQL-запрос: SUM(points)
    balance = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id
    ).scalar()
    
    # scalar() вернет None, если у пользователя нет транзакций.
    # В этом случае баланс равен 0.
    return balance or 0


def get_user_transactions(
    db: Session, 
    user_id: int, 
    skip: int = 0, 
    limit: int = 10
) -> List[LoyaltyTransaction]:
    """Получает пагинированный список ВСЕХ транзакций пользователя."""
    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id
    ).order_by(LoyaltyTransaction.created_at.desc()).offset(skip).limit(limit).all()


def count_user_transactions(db: Session, user_id: int) -> int:
    """Подсчитывает общее количество транзакций у пользователя."""
    return db.query(LoyaltyTransaction).filter(LoyaltyTransaction.user_id == user_id).count()


def get_total_referral_earnings(db: Session, user_id: int) -> int:
    """Подсчитывает общую сумму баллов, заработанных пользователем на реферальной программе."""
    total_earned = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id,
        LoyaltyTransaction.type == "referral_earn"
    ).scalar()
    return total_earned or 0


def get_expired_positive_transactions(db: Session) -> list[LoyaltyTransaction]:
    """
    Находит все "положительные" транзакции, срок действия которых истек,
    и для которых еще не была создана "сжигающая" транзакция.
    """
    subquery = select(LoyaltyTransaction.related_transaction_id).where(
        LoyaltyTransaction.type == 'expired',
        LoyaltyTransaction.related_transaction_id.isnot(None)
    )

    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.type.in_(['order_earn', 'promo_welcome', 'referral_earn', 'promo_birthday']),
        LoyaltyTransaction.expires_at.isnot(None),
        LoyaltyTransaction.expires_at < datetime.now(timezone.utc),
        LoyaltyTransaction.id.not_in(subquery)
    ).all()


def get_transactions_expiring_soon(db: Session, days_before_expiration: int) -> list:
    """
    Находит сгруппированные по пользователю суммы баллов, которые сгорят
    через указанное количество дней.
    """
    target_date = (datetime.now(timezone.utc) + timedelta(days=days_before_expiration)).date()
    
    query = db.query(
        LoyaltyTransaction.user_id,
        func.sum(LoyaltyTransaction.points).label('total_points_expiring')
    ).filter(
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.type != 'expired',
        LoyaltyTransaction.expires_at.isnot(None),
        cast(LoyaltyTransaction.expires_at, Date) == target_date
    ).group_by(
        LoyaltyTransaction.user_id
    ).all()
    
    return query


def get_spend_transaction_by_order_id(db: Session, user_id: int, order_id_wc: int) -> LoyaltyTransaction | None:
    """Находит транзакцию списания баллов ('order_spend') для конкретного заказа."""
    return db.query(LoyaltyTransaction).filter_by(
        user_id=user_id, order_id_wc=order_id_wc, type="order_spend"
    ).first()


def get_users_with_expiring_points(db: Session) -> List[int]:
    """Возвращает ID пользователей, у которых есть транзакции с датой сгорания."""
    return db.query(LoyaltyTransaction.user_id).filter(
        LoyaltyTransaction.expires_at.isnot(None)
    ).distinct().all()