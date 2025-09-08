# app/clients/woocommerce.py
import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
class WooCommerceClient:
    def __init__(self, base_url: str, app_user: str, app_pass: str):
        # Базовый URL теперь будет просто /wp-json/, так как мы обращаемся к разным неймспейсам
        self.base_url = f"{base_url}/wp-json"
        
        # --- НОВЫЙ СПОСОБ АУТЕНТИФИКАЦИИ ---
        # Basic Auth с именем пользователя и паролем приложения
        self.auth = (app_user, app_pass) 
        
        # Создаем асинхронный клиент httpx
        # Он будет автоматически добавлять заголовок 'Authorization: Basic ...'
        self.async_client = httpx.AsyncClient(auth=self.auth, base_url=self.base_url)

    async def get(self, endpoint: str, params: dict = None):
        try:
            # Теперь endpoint должен содержать полный путь от /wp-json/
            # например, 'wc/v3/products'
            response = await self.async_client.get(endpoint, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            logger.error(f"Error response {e.response.status_code} while requesting {e.request.url!r}.")
            raise

    async def post(self, endpoint: str, json: dict):
        try:
            response = await self.async_client.post(endpoint, json=json)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Error response {e.response.status_code} while requesting {e.request.url!r}.")
            logger.error(f"Response body: {e.response.text}")
            raise

# Создаем синглтон-экземпляр с новой аутентификацией
wc_client = WooCommerceClient(
    base_url=settings.WP_URL,
    app_user=settings.WP_APP_USER,
    app_pass=settings.WP_APP_PASSWORD
)