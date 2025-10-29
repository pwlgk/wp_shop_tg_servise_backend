# app/models/broadcast.py
from sqlalchemy import Column, Integer, String, Text, DateTime, func, ForeignKey
from sqlalchemy.orm import relationship
from app.db.session import Base

class Broadcast(Base):
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True, index=True)
    message_text = Column(Text, nullable=False)
    
    # --- НОВЫЕ ПОЛЯ ---
    photo_file_id = Column(String, nullable=True) # ID файла в Telegram, если рассылка с фото
    image_url = Column(String, nullable=True) # URL, если фото добавляется через админку по ссылке
    button_text = Column(String, nullable=True) # Текст для инлайн-кнопки
    button_url = Column(String, nullable=True)  # Относительная ссылка для Mini App
    scheduled_at = Column(DateTime(timezone=True), nullable=True) # Время для отложенного запуска
    # --- КОНЕЦ НОВЫХ ПОЛЕЙ ---

    target_level = Column(String, nullable=True)
    
    # Статус теперь может быть 'pending', 'scheduled', 'processing', 'completed', 'failed', 'cancelled'
    status = Column(String, default="pending", nullable=False, index=True)
    
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    
    # Связь с получателями
    recipients = relationship("BroadcastRecipient", back_populates="broadcast", cascade="all, delete-orphan")


# --- НОВАЯ МОДЕЛЬ ДЛЯ ОТСЛЕЖИВАНИЯ СООБЩЕНИЙ ---
class BroadcastRecipient(Base):
    """
    Хранит информацию о каждом конкретном сообщении, отправленном в рамках рассылки.
    Это необходимо для возможности отзыва (удаления) сообщений.
    """
    __tablename__ = "broadcast_recipients"
    id = Column(Integer, primary_key=True)
    broadcast_id = Column(Integer, ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, nullable=False) # ID сообщения в чате с пользователем

    broadcast = relationship("Broadcast", back_populates="recipients")
    user = relationship("User")