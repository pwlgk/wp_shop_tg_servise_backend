# scripts/test_points_expiration.py

import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

# Хак, чтобы Python мог найти наши модули в папке `app`
sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.crud import loyalty as crud_loyalty
from app.crud import user as crud_user
from app.models.user import User
from app.models.loyalty import LoyaltyTransaction
from app.services.points_expiration import expire_points_task

# --- НАСТРОЙКИ ТЕСТА ---
# Укажите Telegram ID вашего тестового пользователя
TEST_USER_TELEGRAM_ID = 7507311166 # ЗАМЕНИТЕ НА ID ВАШЕГО ТЕСТОВОГО ПОЛЬЗОВАТЕЛЯ


def setup_test_data(db: Session, user: User):
    """Подготавливает тестовые данные в БД для сценария FIFO."""
    print("--- 1. Setting up test data ---")
    
    # Очищаем все старые транзакции этого пользователя для чистоты теста
    db.query(LoyaltyTransaction).filter(LoyaltyTransaction.user_id == user.id).delete()
    print(f"Cleared all previous loyalty transactions for user ID: {user.id}.")
    
    now = datetime.now(timezone.utc)
    
    # Создаем тестовый сценарий (транзакции добавляются в сессию)
    crud_loyalty.create_transaction(
        db, user_id=user.id, points=100, type='order_earn', order_id_wc=101,
        expires_at=now - timedelta(days=10)
    )
    crud_loyalty.create_transaction(
        db, user_id=user.id, points=50, type='order_earn', order_id_wc=102,
        expires_at=now - timedelta(days=5)
    )
    crud_loyalty.create_transaction(
        db, user_id=user.id, points=-70, type='order_spend', order_id_wc=201
    )
    crud_loyalty.create_transaction(
        db, user_id=user.id, points=200, type='order_earn', order_id_wc=103,
        expires_at=now + timedelta(days=30)
    )

    # Коммитим все одним махом
    db.commit()

    print("\nCreated test transactions:")
    print("  - A: +100 points (expired 10 days ago)")
    print("  - B: +50 points (expired 5 days ago)")
    print("  - C: -70 points (spend)")
    print("  - D: +200 points (expires in 30 days)")
    
    initial_balance = crud_loyalty.get_user_balance(db, user_id=user.id)
    print(f"\nInitial balance (from get_user_balance): {initial_balance} (Expected: 130)")
    print("--------------------------\n")


def verify_results(db: Session, user: User):
    """Проверяет результаты в БД после выполнения задачи сгорания."""
    print("\n--- 3. Verifying results ---")
    
    # Обновляем сессию, чтобы увидеть изменения, сделанные в другой сессии (внутри expire_points_task)
    db.expire_all() 
    
    all_transactions = crud_loyalty.get_all_user_transactions_chronological(db, user_id=user.id)
    
    expired_trans_found = False
    total_expired_points = 0
    
    print("\nAll transactions after expiration task:")
    for trans in all_transactions:
        print(f"  - ID: {trans.id}, Type: {trans.type:<22}, Points: {trans.points:>4}, Expires: {trans.expires_at}")
        if trans.type == 'expired':
            expired_trans_found = True
            total_expired_points += abs(trans.points)
            
    final_balance = crud_loyalty.get_user_balance(db, user_id=user.id)
    
    expected_expired_points = 80
    expected_balance = 200
    
    print("\nVerification Summary:")
    if expired_trans_found and total_expired_points == expected_expired_points:
        print(f"✅ SUCCESS: Correct amount of points expired ({total_expired_points}).")
    else:
        print(f"❌ FAILURE: Expected {expected_expired_points} points to expire, but got {total_expired_points}.")

    if final_balance == expected_balance:
        print(f"✅ SUCCESS: Final balance is correct ({final_balance}).")
    else:
        print(f"❌ FAILURE: Expected final balance to be {expected_balance}, but got {final_balance}.")
        
    print("--------------------------")


async def run_test():
    """Главная функция-оркестратор теста."""
    db: Session = SessionLocal()
    try:
        test_user = crud_user.get_user_by_telegram_id(db, TEST_USER_TELEGRAM_ID)
        if not test_user:
            print(f"ERROR: Test user with Telegram ID {TEST_USER_TELEGRAM_ID} not found in DB.")
            return

        setup_test_data(db, test_user)
        
        print("--- 2. Running expire_points_task ---")
        # Вызываем задачу БЕЗ сессии, чтобы она работала автономно, как в продакшене
        await expire_points_task()
        print("--- ...expire_points_task finished ---")

        verify_results(db, test_user)

    finally:
        db.close()

if __name__ == "__main__":
    if not TEST_USER_TELEGRAM_ID:
        print("Please set TEST_USER_TELEGRAM_ID at the top of the script.")
    else:
        asyncio.run(run_test())