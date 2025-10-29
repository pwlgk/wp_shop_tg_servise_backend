# app/clients/woocommerce.py

import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class WooCommerceClient:
    """
    Асинхронный клиент для взаимодействия с REST API WooCommerce.
    Использует аутентификацию через Application Passwords.
    """
    def __init__(self, base_url: str, app_user: str, app_pass: str):
        self.base_url = f"{base_url}/wp-json"
        self.auth = (app_user, app_pass)
        timeouts = httpx.Timeout(20.0, read=60.0)        
        self.async_client = httpx.AsyncClient(
            auth=self.auth, 
            base_url=self.base_url,
            timeout=timeouts
        )

    async def get(self, endpoint: str, params: dict = None) -> httpx.Response:
        """
        Выполняет GET-запрос. В случае успеха возвращает объект Response.
        В случае HTTP-ошибки (4xx/5xx) выбрасывает исключение.
        """
        try:
            response = await self.async_client.get(endpoint, params=params)
            response.raise_for_status() # <--- Ключевой момент: проверяем на ошибки
            return response
        except httpx.RequestError as e:
            logger.error(f"Network error during GET request to {e.request.url!r}.", exc_info=True)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during GET request to {e.request.url!r}: {e.response.text}", exc_info=True)
            raise

    async def post(self, endpoint: str, json: dict) -> dict:
        """
        Выполняет POST-запрос. В случае успеха возвращает JSON-ответ (dict).
        В случае HTTP-ошибки (4xx/5xx) выбрасывает исключение.
        """
        try:
            response = await self.async_client.post(endpoint, json=json)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            logger.error(f"Network error during POST request to {e.request.url!r}.", exc_info=True)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during POST request to {e.request.url!r}: {e.response.text}", exc_info=True)
            raise

    async def delete(self, endpoint: str, params: dict = None) -> httpx.Response:
        """
        Выполняет DELETE-запрос. В случае успеха возвращает объект Response.
        В случае HTTP-ошибки (4xx/5xx) выбрасывает исключение.
        """
        try:
            response = await self.async_client.delete(endpoint, params=params)
            response.raise_for_status()
            return response
        except httpx.RequestError as e:
            logger.error(f"Network error during DELETE request to {e.request.url!r}.", exc_info=True)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during DELETE request to {e.request.url!r}: {e.response.text}", exc_info=True)
            raise

# Создаем синглтон
wc_client = WooCommerceClient(
    base_url=settings.WP_URL,
    app_user=settings.WP_APP_USER,
    app_pass=settings.WP_APP_PASSWORD
)