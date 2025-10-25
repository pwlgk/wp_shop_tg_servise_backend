import pytest
from datetime import datetime, timedelta, timezone
from app.services.points_expiration import expire_points_task
from app.crud import loyalty as crud_loyalty
from app.models.user import User

# Фикстура для создания тестового пользователя
@pytest.fixture
def test_user(db_session):
    user = User(telegram_id=123, wordpress_id=123)
    db_session.add(user)
    db_session.commit()
    return user

@pytest.mark.asyncio
async def test_partial_expiration(db_session, test_user):
    # 1. Настройка (создаем транзакции через CRUD)
    crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=100, type='order_earn', created_at=datetime.now(timezone.utc) - timedelta(days=10), expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=-30, type='order_spend', created_at=datetime.now(timezone.utc) - timedelta(days=5))
    db_session.commit()

    # Проверяем начальный баланс
    initial_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    assert initial_balance == 70

    # 2. Действие
    await expire_points_task()

    # 3. Проверка
    final_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    assert final_balance == 0

    transactions = crud_loyalty.get_all_user_transactions_chronological(db_session, test_user.id)
    assert len(transactions) == 3
    assert transactions[2].type == 'expired'
    assert transactions[2].points == -70
    