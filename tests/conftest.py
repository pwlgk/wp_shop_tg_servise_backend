# tests/conftest.py

import asyncio
from typing import AsyncGenerator, Generator

import httpx
from httpx import AsyncClient
import pytest
import pytest_asyncio  # <--- Импортируем pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.db.session import Base
from app.dependencies import get_db
# Импортируем все модели, чтобы Base.metadata знало о них
from app.models import *
from app.models.user import User
from app.main import app
from app.services.auth import create_access_token

# --- Настройка тестовой БД ---
engine = create_engine(settings.DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- ФИНАЛЬНАЯ ВЕРСЯ ФИКСТУРЫ ДЛЯ БД ---
@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """
    Фикстура, которая создает таблицы, запускает тест в транзакции и откатывает ее.
    """
    # Удаляем все таблицы перед началом (на случай, если что-то осталось от прошлого запуска)
    Base.metadata.drop_all(bind=engine)
    # Создаем все таблицы
    Base.metadata.create_all(bind=engine)
    
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()
    
# --- ФИНАЛЬНАЯ ВЕРСЯ ФИКСТУРЫ ДЛЯ КЛИЕНТА ---
@pytest_asyncio.fixture(scope="function")
async def client(db_session: Session) -> AsyncGenerator[AsyncClient, None]:
    """
    Фикстура для создания асинхронного тестового клиента FastAPI.
    """
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    
    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # Мы передаем ASGI-приложение в аргумент `transport`, а не `app`.
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    # -------------------------
    
    del app.dependency_overrides[get_db]


# --- Фикстуры для тестовых данных (без изменений) ---

@pytest.fixture(scope="function")
def test_user(db_session: Session) -> User:
    user = User(
        telegram_id=12345678,
        wordpress_id=101,
        username="testuser",
        first_name="Test",
        last_name="User",
        referral_code="testref" # Добавим, чтобы избежать NotNull constraint
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture(scope="function")
def test_admin(db_session: Session) -> User:
    admin = User(
        telegram_id=settings.ADMIN_TELEGRAM_IDS[0],
        wordpress_id=1,
        username="testadmin",
        first_name="Admin",
        last_name="Test",
        referral_code="adminref" # Добавим, чтобы избежать NotNull constraint
    )
    db_session.add(admin)
    db_session.commit()
    return admin


@pytest.fixture(scope="function")
def admin_auth_headers(test_admin: User) -> dict:
    # Используем правильный способ получения UTC-времени
    from datetime import datetime, timezone
    token = create_access_token(data={"sub": str(test_admin.id)}, expires_delta=None)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_wc_client(mocker):
    # Патчим клиент там, где он импортирован и используется.
    # Это может быть несколько мест. Нужно указать все.
    mock = mocker.patch("app.services.admin.wc_client")
    
    # Также патчим его в других сервисах, если они вызываются
    mocker.patch("app.bot.handlers.user.wc_client")
    
    mock.get = mocker.AsyncMock()
    mock.post = mocker.AsyncMock()
    return mock