#!/bin/bash

# Выход из скрипта при любой ошибке
set -e

echo "Running Alembic migrations..."
# Применяем миграции
alembic upgrade head

echo "Starting Gunicorn..."
# Запускаем основной процесс (команду из Dockerfile)
exec gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 app.main:app