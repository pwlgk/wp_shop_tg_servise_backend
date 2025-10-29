# app/crud/dialogue.py

from sqlalchemy.orm import Session
from sqlalchemy import select, func
from typing import Optional, List

from app.models.dialogue import Dialogue, DialogueMessage
from app.models.user import User

def get_open_dialogue_by_user(db: Session, user_id: int) -> Optional[Dialogue]:
    """Находит открытый диалог для конкретного пользователя."""
    return db.query(Dialogue).filter(Dialogue.user_id == user_id, Dialogue.status == "open").first()

# --- ФУНКЦИЯ №1 ДЛЯ ИСПРАВЛЕНИЯ ---
def create_dialogue(
    db: Session, 
    user: User, 
    first_message_text: str,
    # --- Добавляем недостающие аргументы ---
    media_type: Optional[str] = None, 
    media_url: Optional[str] = None, 
    file_name: Optional[str] = None
) -> Dialogue:
    """Создает новый диалог и первое сообщение в нем."""
    # Создаем диалог
    new_dialogue = Dialogue(
        user_id=user.id,
        status="open",
        last_message_snippet=first_message_text[:100] if first_message_text else file_name or "Медиафайл"
    )
    db.add(new_dialogue)
    db.flush() # Получаем ID диалога до коммита

    # Создаем первое сообщение, передавая все поля
    first_message = DialogueMessage(
        dialogue_id=new_dialogue.id,
        sender_type="user",
        sender_id=user.id,
        text=first_message_text,
        media_type=media_type,
        media_url=media_url,
        file_name=file_name
    )
    db.add(first_message)
    db.commit()
    db.refresh(new_dialogue)
    return new_dialogue

# --- ФУНКЦИЯ №2 ДЛЯ ИСПРАВЛЕНИЯ ---
def add_message_to_dialogue(
    db: Session, 
    dialogue: Dialogue, 
    sender: User, 
    text: str, 
    sender_type: str,
    # --- Добавляем недостающие аргументы ---
    media_type: Optional[str] = None, 
    media_url: Optional[str] = None, 
    file_name: Optional[str] = None
) -> DialogueMessage:
    """Добавляет новое сообщение в существующий диалог."""
    
    # Обновляем поля диалога
    dialogue.last_message_at = func.now()
    dialogue.last_message_snippet = text[:100] if text else file_name or "Медиафайл"
    dialogue.status = "open"
    
    # Создаем новое сообщение, передавая все поля
    new_message = DialogueMessage(
        dialogue_id=dialogue.id,
        sender_type=sender_type,
        sender_id=sender.id,
        text=text,
        media_type=media_type,
        media_url=media_url,
        file_name=file_name
    )
    db.add(new_message)
    
    db.commit()
    db.refresh(new_message)
    return new_message

# ... (остальные CRUD-функции без изменений) ...

def get_dialogues(db: Session, skip: int, limit: int, status: Optional[str]) -> List[Dialogue]:
    query = db.query(Dialogue).order_by(Dialogue.last_message_at.desc())
    if status:
        query = query.filter(Dialogue.status == status)
    return query.offset(skip).limit(limit).all()

def count_dialogues(db: Session, status: Optional[str]) -> int:
    query = db.query(func.count(Dialogue.id))
    if status:
        query = query.filter(Dialogue.status == status)
    return query.scalar()

def get_dialogue_by_id(db: Session, dialogue_id: int) -> Optional[Dialogue]:
    return db.query(Dialogue).filter(Dialogue.id == dialogue_id).first()