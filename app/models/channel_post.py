# app/models/channel_post.py
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.db.session import Base

class ChannelPost(Base):
    __tablename__ = "channel_posts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=True)
    message_text = Column(Text, nullable=False)
    media_urls_json = Column(Text, nullable=True) 
    button_json = Column(Text, nullable=True) 
    
    # --- ИСПРАВЛЕНИЕ: Удаляем старое поле, оставляем только правильное ---
    # channel_message_id = Column(Integer, nullable=True, index=True) # <-- Удалить эту строку
    channel_message_ids_json = Column(Text, nullable=True)
    
    status = Column(String, default="published", nullable=False)
    published_at = Column(DateTime(timezone=True), server_default=func.now())