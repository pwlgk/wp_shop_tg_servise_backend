# app/models/referral.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.db.session import Base

class Referral(Base):
    __tablename__ = "referrals"
    id = Column(Integer, primary_key=True, index=True)
    
    # ID того, кто пригласил
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # ID того, кого пригласили
    referred_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    # 'pending' - зарегистрировался, 'completed' - совершил первую покупку
    status = Column(String, default="pending", nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- СВЯЗИ ДЛЯ УДОБСТВА ---
    referrer = relationship("User", foreign_keys=[referrer_id], back_populates="referrals")
    referred = relationship("User", foreign_keys=[referred_id], back_populates="referrer_link")