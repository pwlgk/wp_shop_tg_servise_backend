# app/crud/user.py
from sqlalchemy.orm import Session
from app.models.user import User
from sqlalchemy import func
from sqlalchemy import or_


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Получает пользователя по его первичному ключу (ID в нашей БД)."""
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_telegram_id(db: Session, telegram_id: int) -> User | None:
    """Получает пользователя по его Telegram ID."""
    return db.query(User).filter(User.telegram_id == telegram_id).first()

def get_user_by_referral_code(db: Session, code: str) -> User | None:
    return db.query(User).filter(User.referral_code == code).first()

def create_user(db: Session, telegram_id: int, wordpress_id: int, username: str | None, referral_code: str) -> User:
    db_user = User(
        telegram_id=telegram_id,
        wordpress_id=wordpress_id,
        username=username,
        referral_code=referral_code
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def count_all_users(db: Session) -> int:
    return db.query(User).count()

def count_users_by_level(db: Session, level: str) -> int:
    return db.query(User).filter(User.level == level).count()

def count_users_with_bot_blocked(db: Session) -> int:
    return db.query(User).filter(User.bot_accessible == False).count()

def find_users(db: Session, query: str, limit: int = 10) -> list[User]:
    """Ищет пользователей по ID, username или telegram_id."""
    if query.isdigit():
        # Ищем по ID или telegram_id
        return db.query(User).filter(
            or_(User.id == int(query), User.telegram_id == int(query))
        ).limit(limit).all()
    else:
        # Ищем по username (без учета регистра)
        search_query = query.lstrip('@')
        return db.query(User).filter(User.username.ilike(f"%{search_query}%")).limit(limit).all()
def get_users(
    db: Session, 
    skip: int = 0, 
    limit: int = 5, 
    level: str | None = None, 
    bot_blocked: bool | None = None
) -> list[User]:
    """Получает пагинированный список пользователей с фильтрами."""
    query = db.query(User)
    if level and level != 'all':
        query = query.filter(User.level == level)
    if bot_blocked is not None:
        query = query.filter(User.bot_accessible != bot_blocked)
        
    return query.order_by(User.id.desc()).offset(skip).limit(limit).all()

def count_users_with_filters(
    db: Session, 
    level: str | None = None, 
    bot_blocked: bool | None = None
) -> int:
    """Подсчитывает общее количество пользователей с учетом фильтров."""
    query = db.query(User)
    if level and level != 'all':
        query = query.filter(User.level == level)
    if bot_blocked is not None:
        query = query.filter(User.bot_accessible != bot_blocked)
        
    return query.count()