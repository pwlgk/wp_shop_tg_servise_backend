# tests/v1/admin/test_orders.py

import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock

from app.models.user import User

pytestmark = pytest.mark.asyncio

ORDER_ID = 123

async def test_get_order_details(
    client: AsyncClient,
    admin_auth_headers: dict,
    mock_wc_client: MagicMock,
    test_user: User # Нам нужен пользователь, которому "принадлежит" заказ
):
    # 1. Настраиваем мок-ответ от WooCommerce
    mock_order_data = {
        "id": ORDER_ID,
        "status": "processing",
        "customer_id": test_user.wordpress_id,
        "billing": {"email": "test@test.com", "phone": "123"},
        # ... другие обязательные поля из схемы Order ...
        "number": "WC-123", "date_created": "2025-10-20T10:00:00", "total": "100.00",
        "payment_method_title": "Test Pay", "line_items": [], "payment_url":""
    }
    mock_response = MagicMock()
    mock_response.json.return_value = mock_order_data
    mock_wc_client.get.return_value = mock_response

    # 2. Делаем запрос
    response = await client.get(f"/api/v1/admin/orders/{ORDER_ID}", headers=admin_auth_headers)

    # 3. Проверяем
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == ORDER_ID
    # Проверяем, что данные обогатились
    assert data["customer_info"]["user_id"] == test_user.id
    assert data["customer_info"]["telegram_id"] == test_user.telegram_id

async def test_update_order_status(
    client: AsyncClient,
    admin_auth_headers: dict,
    mock_wc_client: MagicMock
):
    # 1. Настраиваем мок
    mock_wc_client.post.return_value = {"id": ORDER_ID, "status": "completed"}

    # 2. Делаем запрос
    payload = {"status": "completed"}
    response = await client.put(f"/api/v1/admin/orders/{ORDER_ID}/status", json=payload, headers=admin_auth_headers)
    
    # 3. Проверяем
    assert response.status_code == 200
    # Проверяем, что наш сервис вызвал WooCommerce API с правильными данными
    mock_wc_client.post.assert_called_once_with(
        f"wc/v3/orders/{ORDER_ID}", 
        json={"status": "completed"}
    )