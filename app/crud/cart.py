# app/crud/cart.py
from sqlalchemy.orm import Session
from app.models.cart import CartItem, FavoriteItem

# --- CRUD для Корзины ---

def get_cart_items(db: Session, user_id: int):
    """Получает все товары в корзине пользователя."""
    return db.query(CartItem).filter(CartItem.user_id == user_id).all()

def add_or_update_cart_item(db: Session, user_id: int, product_id: int, quantity: int, variation_id: int | None = None):
    """
    Добавляет товар (или его вариацию) в корзину или обновляет количество.
    """
    # Ищем позицию в корзине с учетом и товара, и его вариации
    item = db.query(CartItem).filter_by(
        user_id=user_id, 
        product_id=product_id, 
        variation_id=variation_id
    ).first()
    
    if item:
        # Если найдено - обновляем количество
        item.quantity = quantity
    else:
        # Если не найдено - создаем новую запись
        item = CartItem(
            user_id=user_id, 
            product_id=product_id, 
            quantity=quantity, 
            variation_id=variation_id
        )
        db.add(item)
    db.commit()
    db.refresh(item)
    return item

def remove_cart_item(db: Session, user_id: int, product_id: int, variation_id: int | None = None) -> bool:
    """
    Удаляет товар (или его конкретную вариацию) из корзины.
    """
    # Ищем позицию для удаления с учетом и товара, и его вариации
    item = db.query(CartItem).filter_by(
        user_id=user_id, 
        product_id=product_id, 
        variation_id=variation_id
    ).first()
    
    if item:
        db.delete(item)
        db.commit()
        return True
    return False

def clear_cart(db: Session, user_id: int):
    """Полностью очищает корзину пользователя."""
    db.query(CartItem).filter_by(user_id=user_id).delete()
    db.commit()

# --- CRUD для Избранного (без изменений) ---

def get_favorite_items(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    return db.query(FavoriteItem).filter(FavoriteItem.user_id == user_id).offset(skip).limit(limit).all()

def get_favorite_items_count(db: Session, user_id: int) -> int:
    """Подсчитывает общее количество избранных товаров у пользователя."""
    return db.query(FavoriteItem).filter(FavoriteItem.user_id == user_id).count()

def add_favorite_item(db: Session, user_id: int, product_id: int):
    existing_item = db.query(FavoriteItem).filter_by(user_id=user_id, product_id=product_id).first()
    if existing_item:
        return existing_item
    
    item = FavoriteItem(user_id=user_id, product_id=product_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

def remove_favorite_item(db: Session, user_id: int, product_id: int) -> bool:
    item = db.query(FavoriteItem).filter_by(user_id=user_id, product_id=product_id).first()
    if item:
        db.delete(item)
        db.commit()
        return True
    return False

def get_favorite_item(db: Session, user_id: int, product_id: int) -> FavoriteItem | None:
    """Проверяет, находится ли КОНКРЕТНЫЙ товар в избранном у пользователя."""
    return db.query(FavoriteItem).filter_by(user_id=user_id, product_id=product_id).first()