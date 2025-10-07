# app/crud/loyalty.py
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.loyalty import LoyaltyTransaction
from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy import Date, cast

def create_transaction(db: Session, user_id: int, points: int, type: str, order_id_wc: int = None, expires_at: datetime = None) -> LoyaltyTransaction:
    transaction = LoyaltyTransaction(
        user_id=user_id,
        points=points,
        type=type,
        order_id_wc=order_id_wc,
        expires_at=expires_at
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction

def get_user_balance(db: Session, user_id: int) -> int:
    """Подсчитывает текущий баланс пользователя, учитывая сгоревшие баллы."""
    balance = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id,
        # Условие, что баллы еще не сгорели
        (LoyaltyTransaction.expires_at == None) | (LoyaltyTransaction.expires_at > datetime.utcnow())
    ).scalar()
    
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
    
    # scalar() может вернуть None, если транзакций нет, поэтому обрабатываем этот случай
    return total_earned or 0

def get_expired_positive_transactions(db: Session) -> list[LoyaltyTransaction]:
    """
    Находит все "положительные" транзакции, срок действия которых истек,
    и для которых еще не была создана "сжигающая" транзакция.
    """
    # Подзапрос, чтобы найти ID всех транзакций, которые уже были "списаны"
    # как сгоревшие. Мы ищем по order_id_wc, если он есть, чтобы связать
    # начисление и сгорание.
    subquery = select(LoyaltyTransaction.order_id_wc).where(
        LoyaltyTransaction.type == 'expired',
        LoyaltyTransaction.order_id_wc.isnot(None)
    )

    return db.query(LoyaltyTransaction).filter(
        # Ищем только "зарабатывающие" транзакции
        LoyaltyTransaction.type.in_(['order_earn', 'promo_welcome', 'referral_earn']),
        # У которых есть дата сгорания
        LoyaltyTransaction.expires_at.isnot(None),
        # И эта дата уже в прошлом
        LoyaltyTransaction.expires_at < datetime.utcnow(),
        # И для которых еще не было "сжигания"
        LoyaltyTransaction.order_id_wc.not_in(subquery)
    ).all()

def get_transactions_expiring_soon(db: Session, days_before_expiration: int) -> list:
    """
    Находит сгруппированные по пользователю суммы баллов, которые сгорят
    через указанное количество дней.
    """
    target_date = datetime.utcnow().date() + timedelta(days=days_before_expiration)
    
    # Мы группируем транзакции по user_id и дате сгорания, чтобы отправить
    # одно сообщение, если у пользователя сгорают баллы из разных заказов в один день.
    query = db.query(
        LoyaltyTransaction.user_id,
        func.sum(LoyaltyTransaction.points).label('total_points_expiring')
    ).filter(
        # Ищем только "положительные" транзакции, которые еще не сгорели
        LoyaltyTransaction.points > 0,
        LoyaltyTransaction.type != 'expired',
        # Где дата сгорания равна целевой дате (сегодня + X дней)
        cast(LoyaltyTransaction.expires_at, Date) == target_date
    ).group_by(
        LoyaltyTransaction.user_id
    ).all()
    
    return query


def get_spend_transaction_by_order_id(db: Session, user_id: int, order_id_wc: int) -> LoyaltyTransaction | None:
    """
    Находит транзакцию списания баллов ('order_spend') для конкретного заказа.
    """
    return db.query(LoyaltyTransaction).filter_by(
        user_id=user_id,
        order_id_wc=order_id_wc,
        type="order_spend"
    ).first()