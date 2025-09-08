# app/crud/cart.py
from sqlalchemy.orm import Session
from app.models.cart import CartItem, FavoriteItem

# --- CRUD для Корзины ---

def get_cart_items(db: Session, user_id: int):
    return db.query(CartItem).filter(CartItem.user_id == user_id).all()

def add_or_update_cart_item(db: Session, user_id: int, product_id: int, quantity: int):
    item = db.query(CartItem).filter_by(user_id=user_id, product_id=product_id).first()
    if item:
        item.quantity = quantity
    else:
        item = CartItem(user_id=user_id, product_id=product_id, quantity=quantity)
        db.add(item)
    db.commit()
    db.refresh(item)
    return item

def remove_cart_item(db: Session, user_id: int, product_id: int):
    item = db.query(CartItem).filter_by(user_id=user_id, product_id=product_id).first()
    if item:
        db.delete(item)
        db.commit()
        return True
    return False

def clear_cart(db: Session, user_id: int):
    db.query(CartItem).filter_by(user_id=user_id).delete()
    db.commit()

# --- CRUD для Избранного ---

def get_favorite_items(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    return db.query(FavoriteItem).filter(FavoriteItem.user_id == user_id).offset(skip).limit(limit).all()

def get_favorite_items_count(db: Session, user_id: int) -> int:
    """Подсчитывает общее количество избранных товаров у пользователя."""
    return db.query(FavoriteItem).filter(FavoriteItem.user_id == user_id).count()

def add_favorite_item(db: Session, user_id: int, product_id: int):
    # Проверяем, нет ли уже такого товара в избранном
    existing_item = db.query(FavoriteItem).filter_by(user_id=user_id, product_id=product_id).first()
    if existing_item:
        return existing_item # Просто возвращаем, ничего не делая
    
    item = FavoriteItem(user_id=user_id, product_id=product_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

def remove_favorite_item(db: Session, user_id: int, product_id: int):
    item = db.query(FavoriteItem).filter_by(user_id=user_id, product_id=product_id).first()
    if item:
        db.delete(item)
        db.commit()
        return True
    return False