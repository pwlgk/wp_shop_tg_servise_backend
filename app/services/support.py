# app/services/support.py

import math
import logging
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

# --- НЕДОСТАЮЩИЕ ИМПОРТЫ ---
from app.core.config import settings
from app.bot.core import bot
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
# ---------------------------

from app.crud import dialogue as crud_dialogue
from app.models.user import User
from app.schemas.admin import (
    DialogueListItem, PaginatedAdminDialogues, DialogueDetails, DialogueUser, DialogueMessageItem
)
from app.bot.services import notification as bot_notification_service
from app.bot.utils.user_display import get_display_name
from app.clients.woocommerce import wc_client

logger = logging.getLogger(__name__)

async def get_paginated_dialogues(
    db: Session, 
    page: int, 
    size: int, 
    status: str | None
) -> PaginatedAdminDialogues:
    """
    Собирает пагинированный список диалогов для админ-панели.
    """
    skip = (page - 1) * size
    
    dialogues_db = crud_dialogue.get_dialogues(db, skip=skip, limit=size, status=status)
    total_items = crud_dialogue.count_dialogues(db, status=status)
    total_pages = math.ceil(total_items / size) if total_items > 0 else 1

    items = []
    for dialogue in dialogues_db:
        # Пытаемся получить актуальное имя из WooCommerce для отображения
        try:
            wc_user_data = (await wc_client.get(f"wc/v3/customers/{dialogue.user.wordpress_id}")).json()
            display_name = get_display_name(wc_user_data, dialogue.user)
        except Exception:
            display_name = dialogue.user.username or f"ID {dialogue.user.telegram_id}"

        items.append(DialogueListItem(
            id=dialogue.id,
            status=dialogue.status,
            last_message_at=dialogue.last_message_at,
            last_message_snippet=dialogue.last_message_snippet,
            user=DialogueUser(
                id=dialogue.user.id,
                telegram_id=dialogue.user.telegram_id,
                display_name=display_name
            )
        ))

    return PaginatedAdminDialogues(
        total_items=total_items,
        total_pages=total_pages,
        current_page=page,
        size=size,
        items=items
    )

async def get_dialogue_details(db: Session, dialogue_id: int) -> DialogueDetails:
    """
    Собирает полную информацию о диалоге, включая всю историю сообщений.
    """
    dialogue = crud_dialogue.get_dialogue_by_id(db, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="Dialogue not found")
        
    # Формируем информацию о пользователе
    try:
        wc_user_data = (await wc_client.get(f"wc/v3/customers/{dialogue.user.wordpress_id}")).json()
        user_display_name = get_display_name(wc_user_data, dialogue.user)
    except Exception:
        user_display_name = dialogue.user.username or f"ID {dialogue.user.telegram_id}"
    
    dialogue_user = DialogueUser(
        id=dialogue.user.id,
        telegram_id=dialogue.user.telegram_id,
        display_name=user_display_name
    )
    
    # Формируем список сообщений, обогащая их информацией об отправителе
    message_items = []
    for msg in sorted(dialogue.messages, key=lambda m: m.created_at): # Сортируем сообщения по времени
        sender_user = db.get(User, msg.sender_id)
        if sender_user:
            sender_display_name = f"Admin ID {sender_user.id}" # Имя по умолчанию
            if msg.sender_type == 'admin':
                try:
                    # Если админ есть в WC, берем имя оттуда
                    wc_sender_data = (await wc_client.get(f"wc/v3/customers/{sender_user.wordpress_id}")).json()
                    sender_display_name = get_display_name(wc_sender_data, sender_user)
                except Exception:
                    # Иначе используем его имя из Telegram
                    sender_display_name = sender_user.first_name or sender_user.username or f"Admin ID {sender_user.id}"
            else: # Если отправитель - user
                sender_display_name = user_display_name
            
            sender_info = DialogueUser(
                id=sender_user.id,
                telegram_id=sender_user.telegram_id,
                display_name=sender_display_name
            )
            
            message_items.append(DialogueMessageItem(
                id=msg.id,
                sender_type=msg.sender_type,
                sender=sender_info,
                text=msg.text,
                media_type=msg.media_type,
                media_url=msg.media_url,
                file_name=msg.file_name,
                created_at=msg.created_at
            ))
            
    return DialogueDetails(
        id=dialogue.id,
        status=dialogue.status,
        user=dialogue_user,
        messages=message_items
    )

async def reply_to_dialogue(db: Session, dialogue_id: int, admin_user: User, text: str):
    """
    Обрабатывает ответ администратора в диалог.
    """
    dialogue = crud_dialogue.get_dialogue_by_id(db, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="Dialogue not found")
        
    # 1. Добавляем сообщение в БД
    crud_dialogue.add_message_to_dialogue(
        db=db,
        dialogue=dialogue,
        sender=admin_user,
        text=text,
        sender_type="admin"
    )
    
    # 2. Отправляем сообщение пользователю через Telegram
    user_to_reply = dialogue.user
    message_to_send = f"💬 **Сообщение от поддержки:**\n\n{text}"
    
    success, reason = await bot_notification_service._send_message(db, user_to_reply, message_to_send)
    
    if not success:
        # Если отправить не удалось, это не критично для самого диалога,
        # но мы должны сообщить об этом админу.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Message was saved, but failed to send to user via Telegram: {reason}"
        )

async def request_user_contact(db: Session, dialogue_id: int, admin_user: User):
    """
    Инициирует нативный запрос контакта у пользователя через Reply-клавиатуру.
    """
    dialogue = crud_dialogue.get_dialogue_by_id(db, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="Dialogue not found")
        
    user_to_request = dialogue.user
    
    message_text = (
        f"Здравствуйте! Менеджер {admin_user.first_name or 'поддержки'} "
        f"хотел бы связаться с вами для уточнения деталей. Пожалуйста, нажмите на кнопку ниже, "
        f"чтобы поделиться вашим номером телефона."
    )
    
    # Используем Reply-клавиатуру для нативного UX
    builder = ReplyKeyboardBuilder()
    builder.button(text="📞 Поделиться контактом", request_contact=True)
    
    try:
        await bot.send_message(
            chat_id=user_to_request.telegram_id,
            text=message_text,
            # Клавиатура исчезнет после одного нажатия
            reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
        )
    except Exception as e:
        logger.error(f"Failed to send contact request to user {user_to_request.id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send request to user via Telegram.")