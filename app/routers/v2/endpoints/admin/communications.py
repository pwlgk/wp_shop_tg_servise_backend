# app/routers/v1/admin/communications.py

import json
import logging
import math
import asyncio
from datetime import datetime
from typing import List, Optional, Literal

from fastapi import (
    APIRouter, Depends, Query, HTTPException, 
    status, BackgroundTasks, Response, Form, File, UploadFile
)
from sqlalchemy import select, func
from sqlalchemy.orm import Session

# Импортируем зависимости и модели
from app.dependencies import get_db, get_admin_user
from app.models.user import User
from app.models.broadcast import Broadcast
from app.models.channel_post import ChannelPost

# Импортируем все необходимые схемы
from app.schemas.admin import (
    BroadcastCreate,
    BroadcastDetails,
    PaginatedAdminBroadcasts,
    ChannelPostCreate,
    ChannelPostListItem,
    PaginatedAdminChannelPosts,
    ChannelPostButton,
)
from app.core.config import settings

# Импортируем сервисы и фоновые задачи
from app.bot.services.broadcast import process_broadcast, retract_broadcast
from app.services import channel_publisher as channel_service, storage as storage_service


logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля.
# Префиксы будут добавлены на уровне выше в admin/__init__.py
router = APIRouter()


# --- БЛОК УПРАВЛЕНИЯ РАССЫЛКАМИ (BROADCASTS) ---

@router.post("/broadcasts", response_model=BroadcastDetails, status_code=status.HTTP_202_ACCEPTED)
async def create_broadcast(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    message_text: str = Form(...),
    target_level: Literal["all", "bronze", "silver", "gold"] = Form("all"),
    photo_file: Optional[UploadFile] = File(None),
    button_text: Optional[str] = Form(None),
    button_url: Optional[str] = Form(None),
    scheduled_at: Optional[datetime] = Form(None),
):
    """
    [АДМИН] Создает и запускает (возможно, отложенно) задачу на рассылку.
    """
    image_url_from_s3 = None
    if photo_file:
        try:
            image_url_from_s3 = await storage_service.upload_file_to_s3(
                file=photo_file, 
                bucket_name=settings.S3_BUCKET_NAME
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload image: {e}")

    new_broadcast = Broadcast(
        message_text=message_text,
        target_level=target_level,
        image_url=image_url_from_s3,
        button_text=button_text,
        button_url=button_url,
        scheduled_at=scheduled_at,
        status="scheduled" if scheduled_at else "pending",
    )
    db.add(new_broadcast)
    db.commit()
    db.refresh(new_broadcast)
    
    background_tasks.add_task(process_broadcast, broadcast_id=new_broadcast.id)
    
    return new_broadcast


@router.get("/broadcasts", response_model=PaginatedAdminBroadcasts)
def get_broadcasts_history(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """[АДМИН] Получает пагинированную историю всех рассылок."""
    skip = (page - 1) * size
    query = select(Broadcast).order_by(Broadcast.created_at.desc())
    
    total_items = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    total_pages = math.ceil(total_items / size) if total_items > 0 else 1
    
    items = db.execute(query.offset(skip).limit(size)).scalars().all()

    return PaginatedAdminBroadcasts(
        total_items=total_items, total_pages=total_pages,
        current_page=page, size=size, items=items
    )


@router.get("/broadcasts/{broadcast_id}", response_model=BroadcastDetails)
def get_broadcast_details(broadcast_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Получает детальную информацию о конкретной рассылке."""
    broadcast = db.get(Broadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broadcast not found")
    return broadcast


@router.delete("/broadcasts/{broadcast_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_scheduled_broadcast(broadcast_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Отменяет запланированную (еще не запущенную) рассылку."""
    broadcast = db.get(Broadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broadcast not found")
    if broadcast.status not in ["pending", "scheduled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot cancel a broadcast with status '{broadcast.status}'")
    
    broadcast.status = "cancelled"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/broadcasts/{broadcast_id}/retract", status_code=status.HTTP_202_ACCEPTED)
def retract_sent_broadcast(
    broadcast_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """[АДМИН] Запускает фоновую задачу по удалению уже отправленной рассылки."""
    broadcast = db.get(Broadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broadcast not found")
    if broadcast.status != "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can only retract broadcasts that have been completed.")

    background_tasks.add_task(retract_broadcast, broadcast_id=broadcast_id)
    return {"status": "accepted", "message": "Broadcast retraction process has been started."}


# --- БЛОК УПРАВЛЕНИЯ ПУБЛИКАЦИЯМИ В КАНАЛЕ ---

@router.post("/channel/posts", response_model=ChannelPostListItem, status_code=status.HTTP_201_CREATED)
async def create_channel_post(
    db: Session = Depends(get_db),
    title: Optional[str] = Form(None),
    message_text: str = Form(...),
    media_files: List[UploadFile] = File([]),
    button: Optional[str] = Form(None),
):
    """
    [АДМИН] Публикует новый пост в Telegram-канал.
    """
    uploaded_media_urls = []
    if media_files:
        if len(media_files) > 10:
            raise HTTPException(status_code=400, detail="Cannot upload more than 10 media files.")
        
        upload_tasks = [storage_service.upload_file_to_s3(file, settings.S3_BUCKET_NAME) for file in media_files]
        try:
            uploaded_media_urls = await asyncio.gather(*upload_tasks)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload one or more files: {e}")

    parsed_button: Optional[ChannelPostButton] = None
    if button:
        try:
            button_data = json.loads(button)
            if "type" not in button_data and "url" in button_data:
                button_data["type"] = "web_app" if button_data["url"].startswith('/') else "external"
            parsed_button = ChannelPostButton.model_validate(button_data)
        except (json.JSONDecodeError, Exception) as e:
            raise HTTPException(status_code=400, detail=f"Invalid format for 'button'. Error: {e}")

    post_data = ChannelPostCreate(title=title, message_text=message_text, button=parsed_button)

    try:
        new_post = await channel_service.publish_post_to_channel(db=db, post_data=post_data, media_urls=uploaded_media_urls)
        return new_post
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/channel/posts", response_model=PaginatedAdminChannelPosts)
def get_channel_posts_history(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """[АДМИН] Получает пагинированную историю постов в канале."""
    skip = (page - 1) * size
    query = select(ChannelPost).order_by(ChannelPost.published_at.desc())
    total_items = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    total_pages = math.ceil(total_items / size) if total_items > 0 else 1
    items = db.execute(query.offset(skip).limit(size)).scalars().all()
    return PaginatedAdminChannelPosts(total_items=total_items, total_pages=total_pages, current_page=page, size=size, items=items)


@router.delete("/channel/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel_post(
    post_id: int,
    db: Session = Depends(get_db),
):
    """[АДМИН] Удаляет (отзывает) пост из канала."""
    success = await channel_service.delete_post_from_channel(db, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found in database.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)