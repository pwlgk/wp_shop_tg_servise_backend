# app/tasks_registry.py

from app.services import (
    user_levels,
    points_expiration,
    bot_status_updater,
    notification_cleanup,
    birthday_greeter,
    customer_engagement,
    user_updater,)
from app.db.session import SessionLocal
from sqlalchemy.orm import Session

# --- Определяем "обертки", которые создают сессию БД для каждой задачи ---
# Это более надежный подход, чем передача сессии через BackgroundTasks.

async def run_update_user_levels():
    await user_levels.update_all_user_levels()

async def run_expire_points():
    await points_expiration.expire_points_task()

async def run_notify_expiring_points():
    await points_expiration.notify_about_expiring_points_task()
    
async def run_check_inactive_bots():
    await bot_status_updater.check_inactive_bots_task()

def run_cleanup_old_notifications():
    # Эта задача синхронная, поэтому у нее нет await
    notification_cleanup.cleanup_old_notifications_task()

async def run_greet_birthdays():
    await birthday_greeter.check_birthdays_task()

async def run_activate_new_users():
    await customer_engagement.activate_new_users_task()

async def run_reactivate_sleeping_users():
    await customer_engagement.reactivate_sleeping_users_task()
    
async def run_update_all_usernames():
    await user_updater.update_all_usernames_task()



# --- Словарь-реестр всех задач, доступных для ручного запуска ---
# Ключ - уникальное имя задачи, которое будет использоваться в API.
# 'function' - сама функция для вызова.
# 'description' - описание для отображения в админском Mini App.
# 'is_async' - флаг, чтобы FastAPI знал, как запускать задачу.

TASKS = {
    "update_user_levels": {
        "function": run_update_user_levels,
        "description": "Пересчитывает уровни лояльности (Bronze, Silver, Gold) для всех пользователей.",
        "is_async": True,
    },
    "expire_points": {
        "function": run_expire_points,
        "description": "Списывает (сжигает) бонусные баллы, у которых истек срок действия.",
        "is_async": True,
    },
    "notify_expiring_points": {
        "function": run_notify_expiring_points,
        "description": "Отправляет пользователям уведомления о баллах, которые скоро сгорят.",
        "is_async": True,
    },
    "greet_birthdays": {
        "function": run_greet_birthdays,
        "description": "Находит именинников и начисляет им подарочные бонусы.",
        "is_async": True,
    },
    "check_inactive_bots": {
        "function": run_check_inactive_bots,
        "description": "Проверяет пользователей, заблокировавших бота, чтобы узнать, не разблокировали ли они его.",
        "is_async": True,
    },
    "activate_new_users": {
        "function": run_activate_new_users,
        "description": "Отправляет стимулирующее сообщение новым пользователям без покупок.",
        "is_async": True,
    },
    "reactivate_sleeping_users": {
        "function": run_reactivate_sleeping_users,
        "description": "Отправляет реактивационное сообщение 'спящим' пользователям.",
        "is_async": True,
    },
    "update_all_usernames": {
        "function": run_update_all_usernames,
        "description": "Обновляет локальные данные (имя, username) из Telegram для всех пользователей.",
        "is_async": True,
    },
    "cleanup_old_notifications": {
        "function": run_cleanup_old_notifications,
        "description": "Удаляет старые прочитанные уведомления из базы данных.",
        "is_async": False, # <-- Отмечаем как синхронную
    },
}

# Отдельная функция для получения списка задач для API
def get_tasks_list():
    return [
        {"task_name": name, "description": data["description"]} 
        for name, data in TASKS.items()
    ]