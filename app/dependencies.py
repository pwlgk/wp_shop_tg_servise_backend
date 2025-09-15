# app/dependencies.py

from typing import Iterator, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from contextlib import contextmanager

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User

# Эта схема будет использоваться для ОБЯЗАТЕЛЬНОЙ аутентификации.
# Если токена нет, она сама вызовет ошибку 403.
strict_bearer_scheme = HTTPBearer(auto_error=True)

# Эта схема будет использоваться для ОПЦИОНАЛЬНОЙ аутентификации.
# Если токена нет, она вернет None и не вызовет ошибку.
optional_bearer_scheme = HTTPBearer(auto_error=False) 

def get_db_session_instance() -> Session:
    """Создает и возвращает экземпляр сессии БД."""
    return SessionLocal()

def get_db() -> Iterator[Session]:
    """
    Основная зависимость FastAPI для получения сессии БД.
    Это генератор, который корректно работает с `Depends`.
    """
    db = get_db_session_instance()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def get_db_context() -> Iterator[Session]:
    """
    Контекстный менеджер для получения сессии БД вне FastAPI.
    Используется для поллинга и фоновых задач.
    """
    db = get_db_session_instance()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(strict_bearer_scheme), 
    db: Session = Depends(get_db)
) -> User:
    """
    ОБЯЗАТЕЛЬНАЯ зависимость. 
    Требует валидный токен. Если его нет - вызывает ошибку 401.
    Используется для корзины, заказов, профиля и т.д.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
            
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
        
    return user


def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    ОПЦИОНАЛЬНАЯ зависимость.
    Если токен предоставлен и валиден - возвращает пользователя.
    Если токен не предоставлен или невалиден - возвращает None.
    Используется для каталога, чтобы обогащать данные флагом is_favorite.
    """
    if not credentials:
        return None
        
    try:
        # Здесь мы дублируем логику из get_current_user, но не вызываем HTTPException,
        # а просто возвращаем None в случае ошибки.
        token = credentials.credentials
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    return user # Вернет либо пользователя, либо None, если не найден

def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Зависимость для защиты админских эндпоинтов.
    Проверяет, является ли текущий пользователь администратором.
    """
    if current_user.telegram_id not in settings.ADMIN_TELEGRAM_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this resource."
        )
    return current_user