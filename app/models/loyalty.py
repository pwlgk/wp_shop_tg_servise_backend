# app/models/loyalty.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship


from app.db.session import Base

class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Положительное число - начисление, отрицательное - списание
    points = Column(Integer, nullable=False)
    
    # 'order_earn', 'order_spend', 'promo', 'expired'
    type = Column(String, nullable=False)
    
    # ID заказа в WooCommerce, к которому привязана транзакция
    order_id_wc = Column(Integer, nullable=True, index=True)
    user = relationship("User")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True) # Для сгораемых баллов