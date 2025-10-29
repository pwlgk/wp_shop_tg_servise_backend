# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.db.session import Base
from app.models import user, loyalty # Импортируем все модели для создания таблиц

# Используем in-memory SQLite для тестов - это быстро и изолированно
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session() -> Session:
    """
    Фикстура для создания чистой базы данных для каждого теста.
    """
    Base.metadata.create_all(bind=engine) # Создаем все таблицы
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine) # Очищаем все после теста