# app/routers/v1/admin/tasks.py

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session

# Импортируем зависимости и модели
from app.dependencies import get_db, get_admin_user
from app.db.session import SessionLocal
from app.models.user import User

# Импортируем схемы, связанные с задачами
from app.schemas.admin import TaskInfo, TaskRunRequest, CleanupTaskRequest

# Импортируем реестр задач и конкретные функции-обертки
from app.tasks_registry import TASKS, get_tasks_list, run_cleanup_s3

logger = logging.getLogger(__name__)

# Создаем роутер для этого модуля.
# Префикс /tasks будет добавлен на уровне выше в admin/__init__.py
router = APIRouter()


@router.get("", response_model=List[TaskInfo])
def get_tasks_list_endpoint():
    """
    [АДМИН] Возвращает список всех доступных для ручного запуска фоновых задач.
    """
    return get_tasks_list()


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_task_endpoint(
    request_data: TaskRunRequest,
    background_tasks: BackgroundTasks
):
    """
    [АДМИН] Запускает одну конкретную фоновую задачу (без параметров) или все сразу.
    """
    task_name_to_run = request_data.task_name
    
    if task_name_to_run == "all":
        # Запускаем все задачи из реестра
        for name, data in TASKS.items():
            task_function = data["function"]
            # FastAPI сам разберется, как запустить sync/async функцию
            background_tasks.add_task(task_function)
        
        message = "All background tasks have been scheduled to run."
        logger.info("All background tasks were manually triggered.")

    elif task_name_to_run in TASKS:
        # Запускаем одну конкретную задачу
        task_data = TASKS[task_name_to_run]
        task_function = task_data["function"]
        
        background_tasks.add_task(task_function)
        message = f"Task '{task_name_to_run}' has been scheduled to run."
        logger.info(f"Background task '{task_name_to_run}' was manually triggered.")
    else:
        # Этот код практически недостижим благодаря валидации Pydantic `Literal`
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_name_to_run}' not found.")

    return {"status": "accepted", "message": message}


@router.post("/run/cleanup-s3", status_code=status.HTTP_202_ACCEPTED)
async def run_cleanup_s3_task_endpoint(
    request_data: CleanupTaskRequest,
    background_tasks: BackgroundTasks,
    admin_user: User = Depends(get_admin_user) # Зависимость для получения ID админа
):
    """
    [АДМИН] Запускает фоновую задачу по очистке старых медиафайлов в S3-хранилище.
    Требует указания количества дней.
    """
    # ВАЖНО: Создаем новую, независимую сессию БД специально для фоновой задачи.
    # Это предотвращает проблемы, связанные с закрытием сессии основного запроса.
    db_session = SessionLocal()
    
    background_tasks.add_task(
        run_cleanup_s3,
        db_session=db_session,
        older_than_days=request_data.older_than_days,
        admin_user_id=admin_user.telegram_id
    )
    
    message = (
        f"Task to clean up files older than {request_data.older_than_days} days has been started. "
        f"A report will be sent to you in Telegram upon completion."
    )
    logger.info(f"S3 cleanup task triggered by admin {admin_user.id} for files older than {request_data.older_than_days} days.")
    
    return {"status": "accepted", "message": message}