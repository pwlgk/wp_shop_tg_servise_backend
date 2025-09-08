# app/services/user_levels.py
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.session import SessionLocal
from app.models.user import User
from app.clients.woocommerce import wc_client
import logging

logger = logging.getLogger(__name__)
# Настройки порогов для уровней. Можно вынести в config.py
LEVEL_THRESHOLDS = {
    "gold": 50000,
    "silver": 10000,
    "bronze": 0
}

async def get_total_spending_for_user(wordpress_id: int) -> float:
    """
    Получает общую сумму выполненных заказов пользователя за последние 365 дней,
    корректно обрабатывая пагинацию для сбора ВСЕХ заказов.
    """
    one_year_ago = (datetime.utcnow() - timedelta(days=365)).isoformat()
    total_spent = 0.0
    current_page = 1
    
    # --- НОВАЯ ЛОГИКА С ЦИКЛОМ И ПАГИНАЦИЕЙ ---
    while True:
        params = {
            "customer": wordpress_id,
            "status": "completed",
            "after": one_year_ago,
            "per_page": 100, # Максимальное количество на страницу
            "page": current_page
        }
        
        try:
            logger.info(f"Fetching orders for user {wordpress_id}, page {current_page}...")
            response = await wc_client.get("wc/v3/orders", params=params)
            orders_on_page = response.json()
            
            # Если страница пустая, значит, заказы закончились
            if not orders_on_page:
                break
                
            # Суммируем стоимость заказов на текущей странице
            total_spent += sum(float(order['total']) for order in orders_on_page)
            
            # Проверяем, есть ли еще страницы
            total_pages_header = response.headers.get("X-WP-TotalPages")
            if total_pages_header and int(total_pages_header) <= current_page:
                # Мы на последней странице
                break
            
            # Переходим к следующей странице
            current_page += 1

        except Exception as e:
            logger.error(f"Error fetching orders for user {wordpress_id} on page {current_page}", exc_info=True)
            # В случае ошибки прекращаем сбор, чтобы не зациклиться,
            # но возвращаем уже собранную сумму.
            break
            
    # -----------------------------------------------
            
    logger.info(f"Total spending for user {wordpress_id} over the last year: {total_spent}")
    return total_spent

def determine_level(total_spent: float) -> str:
    """Определяет уровень пользователя на основе потраченной суммы."""
    # Идем от самого высокого порога к низкому
    for level, threshold in LEVEL_THRESHOLDS.items():
        if total_spent >= threshold:
            return level
    return "bronze"

async def update_all_user_levels():
    """
    Основная задача планировщика.
    Проходит по всем пользователям и обновляет их уровни лояльности.
    """
    logger.info("--- Starting scheduled job: Update User Levels ---")
    db: Session = SessionLocal()
    try:
        all_users = db.query(User).all()
        
        for user in all_users:
            total_spent = await get_total_spending_for_user(user.wordpress_id)
            new_level = determine_level(total_spent)
            
            if user.level != new_level:
                logger.info(f"Updating user {user.id} level from '{user.level}' to '{new_level}' (spent: {total_spent})")
                user.level = new_level
        
        db.commit()
    finally:
        db.close()
    logger.info("--- Finished scheduled job: Update User Levels ---")