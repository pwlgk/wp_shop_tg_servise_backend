# app/dependencies.py

import logging
from typing import Optional, Iterator
from contextlib import contextmanager

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from fastapi import Request
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User

# --- Инициализация логгера ---
logger = logging.getLogger(__name__)

# --- Схемы аутентификации ---
strict_bearer_scheme = HTTPBearer(auto_error=True)
optional_bearer_scheme = HTTPBearer(auto_error=False)

# --- Управление сессией БД ---
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
    Контекстный менеджер для получения сессии БД вне FastAPI (для бота и фоновых задач).
    """
    db = get_db_session_instance()
    try:
        yield db
    finally:
        db.close()

# --- Зависимости аутентификации и авторизации ---

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(strict_bearer_scheme), 
    db: Session = Depends(get_db)
) -> User:
    """
    ОБЯЗАТЕЛЬНАЯ зависимость. 
    Требует валидный токен. Если его нет или он невалиден - вызывает ошибку 401.
    """
    logger.debug("Dependency 'get_current_user' starting...")
    
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
            logger.warning("Token payload is missing 'sub' (user_id).")
            raise credentials_exception
            
    except JWTError as e:
        logger.warning(f"JWT Error during token decoding: {e}")
        raise credentials_exception
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        logger.warning(f"User with ID {user_id} from token not found in DB.")
        raise credentials_exception
    request.state.user = user
    logger.info(f"Successfully authenticated user ID: {user.id} (TG ID: {user.telegram_id})")
    return user


def get_optional_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    ОПЦИОНАЛЬНАЯ зависимость.
    Если токен предоставлен и валиден - возвращает пользователя.
    Если токен не предоставлен или невалиден - возвращает None.
    """
    logger.debug("Dependency 'get_optional_current_user' starting...")
    
    if not credentials:
        logger.debug("No token provided, returning None.")
        return None
        
    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning("Optional token payload is missing 'sub'.")
            return None
    except JWTError:
        logger.warning("Optional token is invalid.")
        return None
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    request.state.user = user
    if user:
        logger.info(f"Successfully authenticated optional user ID: {user.id} (TG ID: {user.telegram_id})")
    else:
        logger.warning(f"Optional user with ID {user_id} from token not found in DB.")
        
    return user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Зависимость для защиты админских эндпоинтов.
    Проверяет, является ли текущий аутентифицированный пользователь администратором.
    """
    logger.info(f"Checking admin permissions for user with TG ID: {current_user.telegram_id}")
    
    if current_user.telegram_id not in settings.ADMIN_TELEGRAM_IDS:
        logger.warning(
            f"Permission denied for user TG ID {current_user.telegram_id}. "
            f"Not in ADMIN_TELEGRAM_IDS: {settings.ADMIN_TELEGRAM_IDS}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this resource."
        )
        
    logger.info(f"Admin access GRANTED for user with TG ID: {current_user.telegram_id}.")
    return current_user