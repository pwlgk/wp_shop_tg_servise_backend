# app/routers/admin.py
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.schemas.admin import BroadcastCreate
from app.models.broadcast import Broadcast
from app.bot.services.broadcast import process_broadcast

# TODO: Добавить зависимость для проверки, что пользователь - админ

router = APIRouter()

@router.post("/broadcasts", status_code=202) # 202 Accepted - "Принято к обработке"
def create_broadcast_task(
    broadcast_data: BroadcastCreate,
    background_tasks: BackgroundTasks, # FastAPI зависимость для фоновых задач
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Создает и запускает задачу на рассылку сообщений.
    """
    # 1. Создаем запись о рассылке в БД
    new_broadcast = Broadcast(
        message_text=broadcast_data.message_text,
        target_level=broadcast_data.target_level
    )
    db.add(new_broadcast)
    db.commit()
    db.refresh(new_broadcast)
    
    # 2. Добавляем "тяжелую" задачу в фон
    background_tasks.add_task(process_broadcast, broadcast_id=new_broadcast.id)
    
    return {"status": "accepted", "broadcast_id": new_broadcast.id}