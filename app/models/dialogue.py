# app/models/dialogue.py

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, func, ForeignKey
from sqlalchemy.orm import relationship, Mapped
from app.db.session import Base
from typing import List

from app.models.user import User

class Dialogue(Base):
    __tablename__ = "dialogues"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Статусы: 'open' - активен, 'closed' - закрыт админом
    status: Mapped[str] = Column(String, default="open", nullable=False, index=True)
    
    # Поля для удобной сортировки и предпросмотра
    last_message_at: Mapped[datetime] = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_message_snippet: Mapped[str] = Column(String(100), nullable=True)

    created_at: Mapped[datetime] = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Связи
    user: Mapped["User"] = relationship()
    messages: Mapped[List["DialogueMessage"]] = relationship(back_populates="dialogue", cascade="all, delete-orphan")


class DialogueMessage(Base):
    __tablename__ = "dialogue_messages"
    
    id: Mapped[int] = Column(Integer, primary_key=True)
    dialogue_id: Mapped[int] = Column(Integer, ForeignKey("dialogues.id", ondelete="CASCADE"), nullable=False, index=True)
    
    sender_type: Mapped[str] = Column(String, nullable=False)
    sender_id: Mapped[int] = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
    text: Mapped[str] = Column(Text, nullable=True) # Текст теперь может быть пустым (например, для фото с подписью)
    
    # Тип вложения: 'photo', 'video', 'document'
    media_type: Mapped[str] = Column(String(50), nullable=True)
    # URL вложения на нашем S3
    media_url: Mapped[str] = Column(String, nullable=True)
    # Оригинальное имя файла
    file_name: Mapped[str] = Column(String, nullable=True)
    # -----------------------
    
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), server_default=func.now())

    dialogue: Mapped["Dialogue"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship()