# app/core/limiter.py

import logging
from typing import Optional

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.dependencies import get_optional_current_user # Используем опциональную зависимость
from app.models.user import User

logger = logging.getLogger(__name__)

# --- Функция-ключ для идентификации запросов ---

def key_func(request: Request) -> str:
    """
    Определяет, как идентифицировать запрос для применения лимита.
    """
    # Пытаемся получить пользователя из состояния, если он был установлен зависимостью
    user: Optional[User] = getattr(request.state, "user", None)

    if user and user.id:
        return str(user.id)
    
    # Если пользователя нет (неаутентифицированный эндпоинт), используем IP
    return get_remote_address(request)

# --- Создание и конфигурация лимитера ---

# 1. Создаем экземпляр Limiter, передавая ему нашу функцию-ключ
limiter = Limiter(key_func=key_func)

# 2. Настраиваем хранилище для счетчиков.
#    Мы будем использовать наш существующий асинхронный клиент Redis.
#    URL берется из настроек. `async` в URI указывает на использование асинхронного драйвера.
limiter.storage_uri = f"async+{settings.REDIS_URL}"

# 3. Устанавливаем стратегию, которая будет использоваться при превышении лимита.
#    'moving-window' - это гибкий и эффективный алгоритм.
limiter.strategy = "moving-window"