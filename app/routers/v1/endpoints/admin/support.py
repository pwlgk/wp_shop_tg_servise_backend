# app/routers/v1/admin/support.py

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

# Импортируем зависимости и модели
from app.dependencies import get_db, get_admin_user
from app.models.user import User

# Импортируем схемы, связанные с диалогами
from app.schemas.admin import (
    PaginatedAdminDialogues,
    DialogueDetails,
    DialogueReplyRequest
)
from app.crud import dialogue as crud_dialogue
from app.services import support as support_service

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля.
# Префикс /dialogues будет добавлен на уровне выше
router = APIRouter()


@router.get("", response_model=PaginatedAdminDialogues)
async def get_dialogues_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Фильтр по статусу: 'open' или 'closed'"),
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Получает пагинированный список диалогов с пользователями.
    """
    return await support_service.get_paginated_dialogues(db, page, size, status)


@router.get("/{dialogue_id}", response_model=DialogueDetails)
async def get_dialogue_details_endpoint(
    dialogue_id: int,
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Получает всю историю сообщений для конкретного диалога.
    """
    return await support_service.get_dialogue_details(db, dialogue_id)


@router.post("/{dialogue_id}/reply", status_code=status.HTTP_201_CREATED)
async def reply_to_dialogue_endpoint(
    dialogue_id: int,
    reply_data: DialogueReplyRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    [АДМИН] Отправляет ответ пользователю в рамках диалога.
    """
    await support_service.reply_to_dialogue(
        db=db,
        dialogue_id=dialogue_id,
        admin_user=admin_user,
        text=reply_data.text
    )
    return {"status": "ok", "message": "Reply sent successfully."}


@router.post("/{dialogue_id}/close", status_code=status.HTTP_200_OK)
def close_dialogue_endpoint(
    dialogue_id: int,
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Закрывает диалог.
    """
    dialogue = crud_dialogue.get_dialogue_by_id(db, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="Dialogue not found")
    dialogue.status = "closed"
    db.commit()
    return {"status": "ok", "message": "Dialogue closed."}


@router.post("/{dialogue_id}/request-contact", status_code=status.HTTP_202_ACCEPTED)
async def request_contact_from_user_endpoint(
    dialogue_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    [АДМИН] Отправляет пользователю запрос на предоставление контакта.
    """
    await support_service.request_user_contact(db, dialogue_id, admin_user)
    return {"status": "ok", "message": "Contact request sent to the user."}