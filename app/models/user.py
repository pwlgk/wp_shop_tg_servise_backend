# app/models/user.py

from sqlalchemy import Column, Date, Integer, String, Boolean, BIGINT, DateTime, func
from sqlalchemy.orm import relationship
from .referral import Referral
from app.db.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BIGINT, unique=True, index=True, nullable=False)
    wordpress_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, unique=True, nullable=True) # Username может быть опциональным
    
    is_blocked = Column(Boolean, default=False, nullable=False, server_default='false')
    level = Column(String, default="bronze", nullable=False, server_default='bronze')
    bot_accessible = Column(Boolean, default=True, nullable=False, server_default='true')
    
    referral_code = Column(String, unique=True, index=True, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    # Связи для реферальной системы
    # Кто пригласил этого пользователя
    referrer_link = relationship("Referral", foreign_keys="Referral.referred_id", back_populates="referred", uselist=False)
    # Кого пригласил этот пользователь
    referrals = relationship("Referral", foreign_keys="Referral.referrer_id", back_populates="referrer")
    birth_date = Column(Date, nullable=True) # Храним только дату, без времени
    phone = Column(String, nullable=True)
