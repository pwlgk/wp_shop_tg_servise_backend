# app/bot/utils/user_display.py
from typing import Dict, Any
from app.models.user import User as DBUser

def get_display_name(wc_user_data: Dict[str, Any], db_user: DBUser) -> str:
    """
    Формирует лучшее возможное имя для отображения админу.
    Приоритет: ФИО -> Username -> Telegram ID.
    """
    first_name = wc_user_data.get("first_name", "")
    last_name = wc_user_data.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip()
    
    # Считаем имя "валидным", если в нем больше 2 символов, исключая пробелы и точки
    if full_name and len(full_name.replace(" ", "").replace(".", "")) > 2:
        return full_name
        
    if db_user.username:
        return f"@{db_user.username}"
        
    return f"Пользователь #{db_user.telegram_id}"