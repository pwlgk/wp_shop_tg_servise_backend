# app/models/broadcast.py
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.db.session import Base

class Broadcast(Base):
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True, index=True)
    message_text = Column(Text, nullable=False)
    target_level = Column(String, nullable=True) # e.g., "all", "bronze", "silver", "gold"
    
    # 'pending', 'processing', 'completed', 'failed'
    status = Column(String, default="pending", nullable=False)
    
    # Статистика
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    photo_file_id = Column(String, nullable=True)