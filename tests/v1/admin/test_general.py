# tests/v1/admin/test_general.py

import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock

# pytestmark = pytest.mark.asyncio

async def test_get_admin_dashboard(
    client: AsyncClient, 
    admin_auth_headers: dict, 
    mock_wc_client: MagicMock,
    db_session, # Подключаем, чтобы crud-функции работали
    test_user # Создаем одного юзера для статистики
):
    # 1. Настраиваем мок-ответ от WooCommerce
    mock_response = MagicMock()
    mock_response.headers = {"X-WP-Total": "5"} # Для подсчета заказов
    mock_response.json.return_value = [{"total_sales": "12345.67"}] # Для выручки
    mock_wc_client.get.return_value = mock_response

    # 2. Делаем запрос к нашему API
    response = await client.get("/api/v1/admin/dashboard", headers=admin_auth_headers)

    # 3. Проверяем результат
    assert response.status_code == 200
    data = response.json()
    assert data["total_users_count"] == 1 # Мы создали одного юзера фикстурой
    assert data["sales_today"] == 12345.67
    assert data["on_hold_orders_count"] == 5 # Значение из хедера