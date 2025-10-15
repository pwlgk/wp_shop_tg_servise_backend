# run_tasks_manually.py
import asyncio
import logging
import sys
import os

# Хак для корректной работы импортов
sys.path.append(os.getcwd())

# --- Импортируем все наши асинхронные задачи ---
from app.services.user_levels import update_all_user_levels
from app.services.points_expiration import expire_points_task, notify_about_expiring_points_task
from app.services.bot_status_updater import check_inactive_bots_task
from app.services.notification_cleanup import cleanup_old_notifications_task
from app.services.birthday_greeter import check_birthdays_task
from app.services.customer_engagement import activate_new_users_task, reactivate_sleeping_users_task

# --- Импортируем синхронные задачи, если они есть ---
# (в нашем случае `cleanup_old_notifications_task` синхронная)

async def main():
    """
    Основная функция для поочередного запуска всех фоновых задач.
    """
    print("--- Manual Task Runner ---")
    print("Starting tasks sequentially...\n")

    # --- ЗАПУСК АСИНХРОННЫХ ЗАДАЧ ---
    
    print("\n[1/7] Running: update_all_user_levels...")
    await update_all_user_levels()
    print("Done.")

    print("\n[2/7] Running: notify_about_expiring_points_task...")
    await notify_about_expiring_points_task()
    print("Done.")
    
    print("\n[3/7] Running: expire_points_task...")
    await expire_points_task()
    print("Done.")

    print("\n[4/7] Running: check_inactive_bots_task...")
    await check_inactive_bots_task()
    print("Done.")

    print("\n[5/7] Running: check_birthdays_task...")
    await check_birthdays_task()
    print("Done.")

    print("\n[6/7] Running: activate_new_users_task...")
    await activate_new_users_task()
    print("Done.")
    
    print("\n[7/7] Running: reactivate_sleeping_users_task...")
    await reactivate_sleeping_users_task()
    print("Done.")
    
    # --- ЗАПУСК СИНХРОННЫХ ЗАДАЧ ---
    # Синхронные задачи нужно запускать в `asyncio.to_thread`
    
    print("\n[8/8] Running: cleanup_old_notifications_task...")
    # `to_thread` выполняет синхронную функцию в отдельном потоке,
    # не блокируя основной event loop.
    await asyncio.to_thread(cleanup_old_notifications_task)
    print("Done.")
    
    print("\n--- All tasks completed! ---")


if __name__ == "__main__":
    # Настраиваем логирование, чтобы видеть вывод от наших сервисов
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")