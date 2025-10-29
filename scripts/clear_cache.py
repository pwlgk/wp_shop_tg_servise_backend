# scripts/clear_cache.py

import os
import sys
import redis
from dotenv import load_dotenv

# Хак для корректной работы, если скрипт запускается из другой директории
# Он ищет .env файл в текущей и родительской директориях
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()


def clear_redis_cache():
    """
    Подключается к Redis, используя переменные из .env файла,
    и полностью очищает текущую базу данных.
    """
    print("--- Redis Cache Clear Script ---")

    # 1. Получаем настройки из переменных окружения
    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")

    if not redis_host or not redis_port:
        print("❌ ERROR: Переменные REDIS_HOST и REDIS_PORT не найдены в вашем .env файле.")
        print("Пожалуйста, убедитесь, что файл .env существует и содержит эти переменные.")
        sys.exit(1)

    print(f"ℹ️  Попытка подключения к Redis по адресу: {redis_host}:{redis_port}")

    try:
        # 2. Создаем клиент Redis. decode_responses=True для удобства.
        # Используем стандартный синхронный клиент, так как asyncio здесь не нужен.
        r = redis.Redis(host=redis_host, port=int(redis_port), db=0, decode_responses=True)
        
        # 3. Проверяем соединение с помощью команды PING
        r.ping()
        print("✅ Соединение с Redis успешно установлено.")

        # 4. Получаем количество ключей перед очисткой
        keys_count = r.dbsize()
        if keys_count == 0:
            print("✅ Кеш уже пуст. Делать нечего.")
            sys.exit(0)
            
        print(f"🔥 Найденo {keys_count} ключей. Начинаю очистку...")

        # 5. Выполняем команду FLUSHDB для удаления всех ключей в текущей БД
        r.flushdb()
        
        # 6. Проверяем результат
        final_keys_count = r.dbsize()
        if final_keys_count == 0:
            print(f"✅ Успешно удалено {keys_count} ключей. Кеш полностью очищен!")
        else:
            print(f"⚠️  Что-то пошло не так. В базе осталось {final_keys_count} ключей.")

    except redis.exceptions.ConnectionError as e:
        print(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ: Не удалось подключиться к Redis.")
        print(f"   Убедитесь, что Redis сервер запущен и доступен по адресу {redis_host}:{redis_port}.")
        print(f"   Детали ошибки: {e}")
        sys.exit(1)
    except ValueError:
        print(f"❌ ОШИБКА КОНФИГУРАЦИИ: Значение REDIS_PORT ('{redis_port}') не является числом.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Произошла непредвиденная ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    clear_redis_cache()