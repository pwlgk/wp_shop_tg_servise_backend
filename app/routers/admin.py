# app/routers/admin.py

from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks, Response
from sqlalchemy.orm import Session
from typing import Any, List
from app.core.redis import redis_client
from typing import Literal
from app.dependencies import get_db, get_admin_user
from app.models.user import User
from app.schemas.admin import (
    PaginatedAdminOrders, PaginatedAdminPromos, PaginatedAdminUsers, AdminUserDetails, AdminSendMessageRequest, 
    AdminAdjustPointsRequest, BroadcastCreate
)
from app.crud import notification as crud_notification
from app.schemas.product import PaginatedOrders, PaginatedResponse
from app.schemas.settings import ShopSettings
from app.services import admin as admin_service
from app.crud import user as crud_user
from app.crud import loyalty as crud_loyalty
from app.services import loyalty as loyalty_service
from app.bot.services import notification as bot_notification_service
from app.models.broadcast import Broadcast
from app.bot.services.broadcast import process_broadcast

# Применяем защиту ко всем эндпоинтам в этом роутере.
# Любой запрос сюда сначала пройдет через get_admin_user.
router = APIRouter(dependencies=[Depends(get_admin_user)])


@router.get("/dashboard")
async def get_admin_dashboard(db: Session = Depends(get_db)):
    """
    [АДМИН] Возвращает сводную статистику для главного экрана админ-панели.
    """
    return await admin_service.get_dashboard_stats(db)


@router.get("/users", response_model=PaginatedAdminUsers)
async def get_users_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    level: str | None = Query(None, description="Фильтр по уровню: bronze, silver, gold"),
    bot_blocked: bool | None = Query(None, description="Фильтр по статусу блокировки бота"),
    search: str | None = Query(None, description="Поиск по ID, TG ID, username или ФИО"), # <-- Новый параметр
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Возвращает пагинированный список пользователей с фильтрами и поиском.
    """
    filters = {
        "level": level if level and level != 'all' else None, 
        "bot_blocked": bot_blocked,
        "search": search # <-- Передаем поисковый запрос
    }
    # Удаляем пустые фильтры
    active_filters = {k: v for k, v in filters.items() if v is not None}
    
    return await admin_service.get_paginated_users(db, page, size, **active_filters)

@router.get("/users/{user_id}", response_model=AdminUserDetails)
async def get_user_details_endpoint(
    user_id: int, 
    orders_page: int = Query(1, ge=1, description="Страница для истории заказов"),
    points_page: int = Query(1, ge=1, description="Страница для истории баллов"),
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Возвращает детальную информацию о конкретном пользователе.
    Поддерживает пагинацию для истории заказов и баллов.
    """
    user_details = await admin_service.get_user_details(db, user_id, orders_page, points_page)
    if not user_details:
        raise HTTPException(status_code=404, detail="User not found")
    return user_details


@router.post("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
def block_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Блокирует пользователя."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_blocked = True
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/unblock", status_code=status.HTTP_204_NO_CONTENT)
def unblock_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    """[АДМИН] Разблокирует пользователя."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_blocked = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/message")
async def send_message_to_user_endpoint(
    user_id: int, 
    request_data: AdminSendMessageRequest,
    db: Session = Depends(get_db)
):
    """[АДМИН] Отправляет сообщение пользователю от имени бота."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    success, reason = await bot_notification_service._send_message(db, user, request_data.message_text)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to send message: {reason}")
    return {"status": "ok"}



@router.post("/users/{user_id}/points")
async def adjust_user_points_endpoint( # <-- Делаем эндпоинт асинхронным
    user_id: int,
    request_data: AdminAdjustPointsRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user) # Получаем админа, чтобы знать, кто это сделал
):
    """[АДМИН] Начисляет или списывает баллы пользователю и отправляет уведомление."""
    user = crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    comment = request_data.comment or f"Корректировка от админа {admin_user.telegram_id}"
    transaction_type = f"admin_adjust_{'add' if request_data.points >= 0 else 'sub'}"
    
    # Создаем транзакцию
    crud_loyalty.create_transaction(
        db, user_id=user.id, points=request_data.points, type=transaction_type
    )
    
    # --- ОТПРАВКА УВЕДОМЛЕНИЙ ---
    # 1. Уведомление в Mini App
    title = "Баланс баллов изменен"
    message = f"Администратор изменил ваш баланс на {request_data.points} баллов. Комментарий: {comment}"
    crud_notification.create_notification(
        db, user_id=user.id, type="points_update", title=title, message=message
    )
    
    # 2. Уведомление в Telegram-бот
    await bot_notification_service.send_manual_points_update(
        db, user, request_data.points, request_data.comment
    )
    # ---------------------------
    
    new_balance = loyalty_service.get_user_balance(db, user)
    return {"status": "ok", "new_balance": new_balance}


@router.post("/broadcasts", status_code=status.HTTP_202_ACCEPTED)
def create_broadcast_task(
    broadcast_data: BroadcastCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    [АДМИН] Создает и запускает задачу на рассылку сообщений.
    """
    new_broadcast = Broadcast(
        message_text=broadcast_data.message_text,
        target_level=broadcast_data.target_level
    )
    db.add(new_broadcast)
    db.commit()
    db.refresh(new_broadcast)
    
    background_tasks.add_task(process_broadcast, broadcast_id=new_broadcast.id)
    
    return {"status": "accepted", "broadcast_id": new_broadcast.id}


@router.get("/orders", response_model=PaginatedAdminOrders)
async def get_orders_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    
    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    status: str | None = Query(default=None, description="Фильтр по статусу: pending, processing, on-hold, ..."),
    search: str | None = Query(default=None, description="Поиск по номеру заказа, email или имени клиента"),
    # --------------------------

    db: Session = Depends(get_db)
):
    """
    [АДМИН] Возвращает пагинированный список всех заказов в упрощенном формате.
    """
    filters = {"status": status, "search": search}
    active_filters = {k: v for k, v in filters.items() if v}
    return await admin_service.get_paginated_orders(db, page, size, **active_filters)


@router.get("/settings", response_model=ShopSettings)
async def get_shop_settings_endpoint():
    """[АДМИН] Возвращает текущие настройки магазина из ACF."""
    return await admin_service.get_current_shop_settings()


@router.get("/promos", response_model=PaginatedAdminPromos) # <-- Меняем response_model
async def get_promos_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    """[АДМИН] Возвращает пагинированный список всех акций."""
    return await admin_service.get_paginated_promos(page, size)


@router.post("/cache/clear", status_code=200)
async def clear_cache_endpoint(
    target: Literal["all", "settings", "catalog"] = "all"
):
    """
    [АДМИН] Принудительно очищает кеш в Redis.
    - target='all': Очищает ВСЕ.
    - target='settings': Очищает только кеш настроек.
    - target='catalog': Очищает кеш каталога и баннеров.
    """
    if target == "all":
        await redis_client.flushall()
        return {"status": "ok", "message": "All Redis cache has been cleared."}
    
    keys_to_delete = []
    if target == "settings":
        keys_to_delete = await redis_client.keys("shop_settings")
    elif target == "catalog":
        keys_to_delete.extend(await redis_client.keys("product:*"))
        keys_to_delete.extend(await redis_client.keys("products:*"))
        keys_to_delete.extend(await redis_client.keys("categories:*"))
        keys_to_delete.extend(await redis_client.keys("cms:*"))
        
    if keys_to_delete:
        await redis_client.delete(*keys_to_delete)
        return {"status": "ok", "message": f"{len(keys_to_delete)} keys for '{target}' have been cleared."}
        
    return {"status": "ok", "message": "No keys to clear."}