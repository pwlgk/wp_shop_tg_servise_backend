# tests/test_loyalty_expiration.py

import pytest
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.services.points_expiration import expire_points_task
from app.crud import loyalty as crud_loyalty
from app.models.user import User

# Настройка логирования для тестов
logger = logging.getLogger(__name__)

# --- ВАШИ ТЕСТОВЫЕ ДАННЫЕ ---
TEST_USER_TG_ID = 1126153026  # Замените на любой TG ID
TEST_USER_WP_ID = 1198        # Замените на любой WP ID
# -----------------------------
@pytest.fixture
def test_user(db_session):
    user = User(telegram_id=TEST_USER_TG_ID, wordpress_id=TEST_USER_WP_ID)
    db_session.add(user)
    db_session.commit()
    logger.info(f"--- CREATED TEST USER (ID: {user.id}, TG_ID: {user.telegram_id}, WP_ID: {user.wordpress_id}) ---")
    return user

@pytest.mark.asyncio
async def test_simple_expiration(db_session, test_user, mocker):
    """Тест-кейс 1: Простое сгорание (100 баллов, ничего не потрачено)."""
    logger.info("--- SCENARIO 1: Simple Expiration Test ---")
    
    mocker.patch('app.bot.services.notification.send_points_expired_notification', new_callable=AsyncMock)
    
    # --- НАЧАЛО ИСПРАВЛЕНИЯ ---
    # 1. Создаем транзакцию стандартным способом
    tx1 = crud_loyalty.create_transaction(
        db_session, user_id=test_user.id, points=100, type='order_earn',
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db_session.flush() # Получаем объект в сессии, но не коммитим
    
    # 2. Модифицируем дату создания для имитации прошлого
    tx1.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.commit()
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    initial_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Initial State: Balance = {initial_balance}. Expected = 100.")
    assert initial_balance == 100

    logger.info("ACTION: Running expire_points_task...")
    await expire_points_task()
    logger.info("ACTION: expire_points_task finished.")

    final_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Final State: Balance = {final_balance}. Expected = 0.")
    assert final_balance == 0

    transactions = crud_loyalty.get_all_user_transactions_chronological(db_session, test_user.id)
    assert len(transactions) == 2
    
    expired_tx = transactions[1]
    logger.info(f"Checking 'expired' transaction: Points = {expired_tx.points}, Type = '{expired_tx.type}'. Expected = -100.")
    assert expired_tx.type == 'expired'
    assert expired_tx.points == -100

    original_tx = db_session.get(type(transactions[0]), transactions[0].id)
    logger.info(f"Checking original transaction: expires_at = {original_tx.expires_at}. Expected = None.")
    assert original_tx.expires_at is None
    logger.info("--- SCENARIO 1 PASSED ---")

@pytest.mark.asyncio
async def test_partial_expiration(db_session, test_user, mocker):
    """Тест-кейс 2: Частичное сгорание (начислено 100, потрачено 30, сгореть должно 70)."""
    logger.info("--- SCENARIO 2: Partial Expiration Test ---")

    mocker.patch('app.bot.services.notification.send_points_expired_notification', new_callable=AsyncMock)

    # --- ИСПРАВЛЕНИЕ ---
    tx1 = crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=100, type='order_earn', expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    tx2 = crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=-30, type='order_spend')
    db_session.flush()
    
    tx1.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    tx2.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    db_session.commit()
    # -------------------

    initial_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Initial State: Balance = {initial_balance}. Expected = 70.")
    assert initial_balance == 70

    logger.info("ACTION: Running expire_points_task...")
    await expire_points_task()
    logger.info("ACTION: expire_points_task finished.")

    final_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Final State: Balance = {final_balance}. Expected = 0.")
    assert final_balance == 0

    transactions = crud_loyalty.get_all_user_transactions_chronological(db_session, test_user.id)
    assert len(transactions) == 3 # earn, spend, expired
    expired_tx = transactions[2]
    logger.info(f"Checking 'expired' transaction: Points = {expired_tx.points}. Expected = -70.")
    assert expired_tx.points == -70
    logger.info("--- SCENARIO 2 PASSED ---")

@pytest.mark.asyncio
async def test_no_expiration_if_spent(db_session, test_user, mocker):
    """Тест-кейс 3: Ничего не сгорает, если "сгораемые" баллы уже потрачены."""
    logger.info("--- SCENARIO 3: No Expiration (Spent) Test ---")
    
    mocker.patch('app.bot.services.notification.send_points_expired_notification', new_callable=AsyncMock)

    # --- ИСПРАВЛЕНИЕ ---
    tx1 = crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=100, type='order_earn', expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    tx2 = crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=50, type='admin_adjust')
    tx3 = crud_loyalty.create_transaction(db_session, user_id=test_user.id, points=-120, type='order_spend')
    db_session.flush()

    tx1.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    tx2.created_at = datetime.now(timezone.utc) - timedelta(days=8)
    tx3.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    db_session.commit()
    # -------------------

    initial_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Initial State: Balance = {initial_balance}. Expected = 30.")
    assert initial_balance == 30

    logger.info("ACTION: Running expire_points_task...")
    await expire_points_task()
    logger.info("ACTION: expire_points_task finished.")

    final_balance = crud_loyalty.get_user_balance(db_session, test_user.id)
    logger.info(f"Final State: Balance = {final_balance}. Expected = 30.")
    assert final_balance == 30

    transactions = crud_loyalty.get_all_user_transactions_chronological(db_session, test_user.id)
    logger.info(f"Final transaction count = {len(transactions)}. Expected = 3 (no 'expired' transaction).")
    assert len(transactions) == 3
    
    original_tx = db_session.get(type(transactions[0]), transactions[0].id)
    logger.info(f"Checking original transaction: expires_at = {original_tx.expires_at}. Expected = None.")
    assert original_tx.expires_at is None
    logger.info("--- SCENARIO 3 PASSED ---")