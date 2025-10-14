# scripts/import_legacy_data.py

import csv
import logging
import sys
import os
from datetime import datetime, timedelta

# Хак для импорта наших модулей из родительской директории
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import SessionLocal
from app.models.user import User
from app.models.referral import Referral
from app.models.loyalty import LoyaltyTransaction
from app.services.user_levels import LEVEL_THRESHOLDS, determine_level
from app.crud import user as crud_user
import secrets

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Константы ---
USERS_CSV_PATH = 'users.csv'
ORDERS_CSV_PATH = 'orders.csv'


def import_users(db):
    """
    Импортирует пользователей из users.csv в нашу локальную базу данных,
    если они еще не существуют.
    """
    logger.info("--- Starting User Import ---")
    
    imported_count = 0
    skipped_count = 0

    # --- ИСПРАВЛЕНИЕ: Открываем файл с `encoding='utf-8-sig'` ---
    # `utf-8-sig` автоматически обрабатывает и игнорирует BOM, если он есть.
    with open(USERS_CSV_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        
        # --- ОТЛАДКА: Выводим названия колонок, которые "видит" Python ---
        logger.info(f"CSV Headers found: {reader.fieldnames}")
        # ----------------------------------------------------------------

        # --- ИСПРАВЛЕНИЕ: Используем `get()` для безопасного доступа ---
        # Проверяем наличие обоих возможных имен колонки
        user_id_column_name = None
        if 'ID' in reader.fieldnames:
            user_id_column_name = 'ID'
        elif 'user_id' in reader.fieldnames:
            user_id_column_name = 'user_id'
            
        if not user_id_column_name:
            logger.error("FATAL: Could not find a column for user ID ('ID' or 'user_id') in users.csv. Aborting.")
            return
        # -------------------------------------------------------------

        for row in reader:
            wp_user_id_str = row.get(user_id_column_name)
            if not wp_user_id_str or not wp_user_id_str.isdigit():
                logger.warning(f"Skipping row due to invalid or missing user ID: {row}")
                continue
            
            wp_user_id = int(wp_user_id_str)
            email = row.get('user_email', '')
            username = row.get('user_nicename', '')
            first_name = row.get('first_name', '')
            last_name = row.get('last_name', '')
            
            # Пытаемся найти пользователя в нашей БД по wordpress_id
            existing_user = db.query(User).filter(User.wordpress_id == wp_user_id).first()
            
            if existing_user:
                skipped_count += 1
                # Опционально: можно обновить ФИО, если нужно
                # existing_user.first_name = first_name
                # existing_user.last_name = last_name
                continue

            # Если пользователя нет, создаем его
            telegram_id = None
            if '@telegram.user' in email:
                try:
                    telegram_id = int(email.split('@')[0].replace('tg_', ''))
                except (ValueError, IndexError):
                    logger.warning(f"Could not parse telegram_id from email: {email}")
                    continue

            if not telegram_id:
                logger.warning(f"Skipping user {username} (WP ID: {wp_user_id}) because telegram_id could not be determined.")
                continue

            # Генерируем уникальный реферальный код
            referral_code = secrets.token_urlsafe(8)
            while crud_user.get_user_by_referral_code(db, code=referral_code):
                referral_code = secrets.token_urlsafe(8)

            new_user = User(
                telegram_id=telegram_id,
                wordpress_id=wp_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                referral_code=referral_code
            )
            db.add(new_user)
            imported_count += 1
            logger.info(f"Importing new user: {username} (WP ID: {wp_user_id})")

    db.commit()
    logger.info(f"--- User Import Finished ---")
    logger.info(f"Imported: {imported_count}, Skipped (already exist): {skipped_count}")


def process_orders_and_update_levels(db):
    """
    Обрабатывает orders.csv для расчета общей суммы покупок
    и обновления уровней лояльности.
    """
    logger.info("--- Starting Order Processing and Level Update ---")
    
    user_spending = {} # Словарь {wordpress_id: total_spent}

    with open(ORDERS_CSV_PATH, mode='r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            status = row['status']
            customer_id = int(row['customer_id']) if row['customer_id'].isdigit() else 0
            order_total = float(row['order_total']) if row['order_total'] else 0.0
            order_date_str = row['order_date']

            # Нас интересуют только выполненные заказы от реальных пользователей
            if status != 'completed' or customer_id == 0 or not order_date_str:
                continue

            # Проверяем, что заказ был в течение последнего года (для системы лояльности)
            order_date = datetime.strptime(order_date_str, '%Y-%m-%d %H:%M:%S')
            if order_date < (datetime.now() - timedelta(days=365)):
                continue

            # Суммируем потраченные деньги для каждого пользователя
            if customer_id not in user_spending:
                user_spending[customer_id] = 0.0
            user_spending[customer_id] += order_total

    logger.info(f"Calculated spending for {len(user_spending)} unique customers.")

    # Теперь обновляем уровни в нашей базе
    updated_count = 0
    for wp_user_id, total_spent in user_spending.items():
        user = db.query(User).filter(User.wordpress_id == wp_user_id).first()
        if user:
            new_level = determine_level(total_spent)
            if user.level != new_level:
                user.level = new_level
                updated_count += 1
                logger.info(f"Updating user {user.username} (WP ID: {wp_user_id}) to level '{new_level}' with total spending {total_spent}")

    db.commit()
    logger.info(f"--- Level Update Finished ---")
    logger.info(f"Updated levels for {updated_count} users.")


def main():
    """Главная функция-оркестратор."""
    
    db = SessionLocal()
    try:
        # 1. Импортируем пользователей
        import_users(db)
        
        # 2. Обрабатываем заказы и обновляем уровни
        process_orders_and_update_levels(db)
        
        logger.info("Data import and processing completed successfully!")
    except Exception as e:
        logger.error("An error occurred during the import process.", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()