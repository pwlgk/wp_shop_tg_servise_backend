# app/models/notification.py
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func, Boolean
from app.db.session import Base
from sqlalchemy.orm import relationship

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Тип уведомления: 'points_earned', 'order_status_update', 'promo', etc.
    type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    image_url = Column(String, nullable=True) # URL изображения для уведомления

    # ID связанной сущности (например, ID заказа или транзакции)
    related_entity_id = Column(String, nullable=True) 
    action_url = Column(String, nullable=True) 

    is_read = Column(Boolean, default=False, nullable=False, server_default='false')
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")