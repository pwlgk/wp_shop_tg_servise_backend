# alembic/env.py

import sys
from os.path import abspath, dirname
# Добавляем путь к нашему проекту, чтобы импорты работали
sys.path.insert(0, abspath(dirname(dirname(__file__))))

from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy.pool import NullPool
from alembic import context

# --- НАШИ ИЗМЕНЕНИЯ ---

# 1. Импортируем наш объект настроек, который умеет читать .env
from app.core.config import settings
# 2. Импортируем базовый класс моделей
from app.db.session import Base
# 3. Импортируем все модели
from app.models.user import User
from app.models.cart import CartItem, FavoriteItem
from app.models.loyalty import LoyaltyTransaction
from app.models.referral import Referral
from app.models.broadcast import Broadcast

# 4. Указываем Alembic на метаданные наших моделей
target_metadata = Base.metadata

# --- КОНЕЦ ИЗМЕНЕНИЙ ---

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    # Используем наш settings объект для получения URL
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    # --- ОКОНЧАТЕЛЬНОЕ ИСПРАВЛЕНИЕ ---
    # Мы не будем ничего брать из объекта 'config'.
    # Вместо этого, создадим словарь для engine_from_config вручную,
    # используя наш надежный объект 'settings'.
    connectable = engine_from_config(
        {"sqlalchemy.url": settings.DATABASE_URL}, # <--- Ключевое изменение
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()