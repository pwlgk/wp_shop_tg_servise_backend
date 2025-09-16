# Dockerfile

# --- Этап 1: Сборка зависимостей (остается без изменений) ---
FROM python:3.12-slim as builder
WORKDIR /app
RUN apt-get update && apt-get install -y build-essential
COPY ./requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


# --- Этап 2: Создание финального, легковесного образа ---
FROM python:3.12-slim

WORKDIR /app

# Копируем виртуальное окружение
COPY --from=builder /opt/venv /opt/venv
# Активируем его
ENV PATH="/opt/venv/bin:$PATH"

# --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
# Копируем ВСЕ необходимое для работы в рабочую директорию /app
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini .
# -------------------------

# Копируем entrypoint (если вы его используете)
COPY ./entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

# Старую команду CMD можно закомментировать или удалить, так как exec используется в entrypoint.sh
# CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "app.main:app"]