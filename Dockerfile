# Dockerfile

# --- Этап 1: Сборка зависимостей ---
# Используем официальный образ Python как основу.
# Указание версии гарантирует предсказуемость.
FROM python:3.12-slim as builder

# Устанавливаем рабочую директорию внутри образа
WORKDIR /app

# Устанавливаем build-essentials для компиляции некоторых Python-пакетов
RUN apt-get update && apt-get install -y build-essential

# Копируем только файл с зависимостями
COPY ./requirements.txt .

# Создаем виртуальное окружение и устанавливаем зависимости в него.
# Это хорошая практика, чтобы не засорять системный Python.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


# --- Этап 2: Создание финального, легковесного образа ---
FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем виртуальное окружение, созданное на предыдущем этапе
COPY --from=builder /opt/venv /opt/venv

# Копируем весь код нашего приложения
COPY ./app ./app

# Активируем виртуальное окружение
ENV PATH="/opt/venv/bin:$PATH"

# Указываем команду, которая будет выполняться при запуске контейнера.
# Используем gunicorn как более надежный и производительный сервер для продакшена.
# UvicornWorker позволяет gunicorn работать с асинхронными ASGI-приложениями.
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "app.main:app"]