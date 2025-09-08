# app/core/redis.py
import redis.asyncio as redis
from app.core.config import settings

# Создаем асинхронный клиент Redis
# decode_responses=True автоматически декодирует ответы из байтов в строки
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

async def get_redis_client():
    """
    Зависимость для получения клиента Redis в эндпоинтах.
    """
    return redis_client