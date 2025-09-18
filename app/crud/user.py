# app/crud/user.py
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.user import User
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import extract


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Получает пользователя по его первичному ключу (ID в нашей БД)."""
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_telegram_id(db: Session, telegram_id: int) -> User | None:
    """Получает пользователя по его Telegram ID."""
    return db.query(User).filter(User.telegram_id == telegram_id).first()

def get_user_by_referral_code(db: Session, code: str) -> User | None:
    return db.query(User).filter(User.referral_code == code).first()

def create_user(
    db: Session, 
    telegram_id: int, 
    wordpress_id: int, 
    username: str | None, 
    referral_code: str,
    first_name: str | None, # <-- Новый аргумент
    last_name: str | None   # <-- Новый аргумент
) -> User:
    """Создает нового пользователя в нашей БД."""
    db_user = User(
        telegram_id=telegram_id,
        wordpress_id=wordpress_id,
        username=username,
        referral_code=referral_code,
        first_name=first_name, # <-- Новое поле
        last_name=last_name    # <-- Новое поле
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
    """Ищет пользователей по ID, telegram_id, username или ФИО."""
    search_query = f"%{query.lstrip('@')}%"
    
    # Строим универсальный фильтр
    filter_conditions = [
        User.username.ilike(search_query),
        # Ищем по полному имени (Иван Петров)
        (User.first_name + ' ' + User.last_name).ilike(search_query),
    ]
    
    if query.isdigit():
        # Если запрос - число, ищем еще и по ID
        filter_conditions.append(User.id == int(query))
        filter_conditions.append(User.telegram_id == int(query))
        
    return db.query(User).filter(or_(*filter_conditions)).limit(limit).all()

def get_users(
    db: Session, 
    skip: int = 0, 
    limit: int = 20, 
    level: str | None = None, 
    bot_blocked: bool | None = None,
    search: str | None = None # <-- Новый параметр
) -> list[User]:
    """
    Получает пагинированный список пользователей с фильтрами и поиском.
    """
    query = db.query(User)
    
    # Применяем фильтры
    if level and level != 'all':
        query = query.filter(User.level == level)
    if bot_blocked is not None:
        # bot_blocked=True -> ищем тех, у кого bot_accessible = False
        # bot_blocked=False -> ищем тех, у кого bot_accessible = True
        query = query.filter(User.bot_accessible != bot_blocked)

    # Применяем поиск, если он есть
    if search:
        search_query = f"%{search}%"
        search_filter = [
            User.username.ilike(search_query),
            (User.first_name + ' ' + User.last_name).ilike(search_query),
            (User.last_name + ' ' + User.first_name).ilike(search_query),
        ]
        if search.isdigit():
            search_filter.append(User.id == int(search))
            search_filter.append(User.telegram_id == int(search))
        
        query = query.filter(or_(*search_filter))
        
    return query.order_by(User.id.desc()).offset(skip).limit(limit).all()


def count_users_with_filters(
    db: Session, 
    level: str | None = None, 
    bot_blocked: bool | None = None,
    search: str | None = None # <-- Новый параметр
) -> int:
    """Подсчитывает общее количество пользователей с учетом фильтров и поиска."""
    query = db.query(func.count(User.id)) # Оптимизация: считаем только ID
    
    # Применяем те же самые фильтры и поиск
    if level and level != 'all':
        query = query.filter(User.level == level)
    if bot_blocked is not None:
        query = query.filter(User.bot_accessible != bot_blocked)

    if search:
        search_query = f"%{search}%"
        search_filter = [
            User.username.ilike(search_query),
            (User.first_name + ' ' + User.last_name).ilike(search_query),
            (User.last_name + ' ' + User.first_name).ilike(search_query),
        ]
        if search.isdigit():
            search_filter.append(User.id == int(search))
            search_filter.append(User.telegram_id == int(search))
        
        query = query.filter(or_(*search_filter))
        
    return query.scalar()


def count_new_users_today(db: Session) -> int:
    """Считает количество пользователей, зарегистрированных сегодня."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(User).filter(User.created_at >= today_start).count()

def get_users_with_birthday_today(db: Session) -> list[User]:
    """Находит всех пользователей, у которых сегодня день рождения."""
    today = datetime.utcnow()
    return db.query(User).filter(
        extract('month', User.birth_date) == today.month,
        extract('day', User.birth_date) == today.day
    ).all()

def get_user_by_wordpress_id(db: Session, wordpress_id: int) -> User | None:
    """Получает пользователя по его ID из WordPress."""
    return db.query(User).filter(User.wordpress_id == wordpress_id).first()

def update_user_phone(db: Session, user: User, phone: str) -> User:
    """Обновляет номер телефона пользователя в локальной БД."""
    user.phone = phone
    db.commit()
    db.refresh(user)
    return user