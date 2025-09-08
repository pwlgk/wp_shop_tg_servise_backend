# app/crud/loyalty.py
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.loyalty import LoyaltyTransaction
from datetime import datetime

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

def get_user_transactions(db: Session, user_id: int) -> List[LoyaltyTransaction]:
    """Получает все активные (не сгоревшие) транзакции пользователя."""
    return db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.user_id == user_id,
        (LoyaltyTransaction.expires_at == None) | (LoyaltyTransaction.expires_at > datetime.utcnow())
    ).order_by(LoyaltyTransaction.created_at.desc()).all()

def get_total_referral_earnings(db: Session, user_id: int) -> int:
    """Подсчитывает общую сумму баллов, заработанных пользователем на реферальной программе."""
    total_earned = db.query(func.sum(LoyaltyTransaction.points)).filter(
        LoyaltyTransaction.user_id == user_id,
        LoyaltyTransaction.type == "referral_earn"
    ).scalar()
    
    # scalar() может вернуть None, если транзакций нет, поэтому обрабатываем этот случай
    return total_earned or 0