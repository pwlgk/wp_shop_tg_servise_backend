# app/crud/referral.py
from sqlalchemy.orm import Session
from app.models.referral import Referral

def create_referral(db: Session, referrer_id: int, referred_id: int) -> Referral:
    """Создает новую реферальную связь."""
    db_referral = Referral(referrer_id=referrer_id, referred_id=referred_id)
    db.add(db_referral)
    db.commit()
    db.refresh(db_referral)
    return db_referral

def get_referral_by_referred_id(db: Session, referred_id: int) -> Referral | None:
    """Находит реферальную связь по ID приглашенного пользователя."""
    return db.query(Referral).filter(Referral.referred_id == referred_id).first()

# --- НОВАЯ ФУНКЦИЯ, КОТОРУЮ НУЖНО ДОБАВИТЬ ---
def count_referrals_by_status(db: Session, referrer_id: int, status: str) -> int:
    """Подсчитывает количество рефералов пользователя с определенным статусом."""
    return db.query(Referral).filter(
        Referral.referrer_id == referrer_id,
        Referral.status == status
    ).count()
# ---------------------------------------------