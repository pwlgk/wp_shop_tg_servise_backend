from sqlalchemy import Column, Integer, String, Boolean, BIGINT
from app.db.session import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BIGINT, unique=True, index=True, nullable=False)
    wordpress_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, unique=True)
    is_blocked = Column(Boolean, default=False)
    level = Column(String, default="bronze", nullable=False) # Уровни: bronze, silver, gold
    bot_accessible = Column(Boolean, default=True, nullable=False) # Задел на будущее для бота
    # Уникальный код для реферальной ссылки
    referral_code = Column(String, unique=True, index=True, nullable=True)
    
    # --- НОВЫЕ СВЯЗИ ---
    # Кто пригласил этого пользователя
    referrer_link = relationship("Referral", foreign_keys="Referral.referred_id", back_populates="referred", uselist=False)
    # Кого пригласил этот пользователь
    referrals = relationship("Referral", foreign_keys="Referral.referrer_id", back_populates="referrer")
