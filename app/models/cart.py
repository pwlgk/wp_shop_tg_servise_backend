# app/models/cart.py
from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.schema import UniqueConstraint

from app.db.session import Base


class CartItem(Base):
    __tablename__ = "cart_items"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, nullable=False)
    
    # --- НОВОЕ ПОЛЕ ---
    # ID вариации товара из WooCommerce. NULL для простых товаров.
    variation_id = Column(Integer, nullable=True) 
    
    quantity = Column(Integer, nullable=False, default=1)

    user = relationship("User")

    # --- ОБНОВЛЕННЫЙ CONSTRAINT ---
    # Теперь уникальной является комбинация товара и его вариации для одного пользователя
    __table_args__ = (UniqueConstraint('user_id', 'product_id', 'variation_id', name='_user_product_variation_uc'),)
class FavoriteItem(Base):
    __tablename__ = "favorite_items"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, nullable=False) # ID товара из WooCommerce

    user = relationship("User")

    __table_args__ = (UniqueConstraint('user_id', 'product_id', name='_user_favorite_product_uc'),)