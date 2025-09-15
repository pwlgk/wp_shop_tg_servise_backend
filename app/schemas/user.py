# app/schemas/user.py
from datetime import date
from pydantic import BaseModel, EmailStr, Field, model_validator
from typing import Optional


# Схема для данных, которые мы получаем от фронтенда
class TelegramLoginData(BaseModel):
    init_data: str # Та самая строка initData от Telegram

# Схема для пользователя, которую мы будем использовать внутри системы
class UserBase(BaseModel):
    id: int
    telegram_id: int
    wordpress_id: int
    username: str | None = None

class User(UserBase):
    is_blocked: bool
    
    class Config:
        from_attributes = True # Раньше было orm_mode = True

# Схема для ответа с токеном
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Схема для адреса (соответствует структуре WooCommerce)
class AddressSchema(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    company: Optional[str] = ""
    address_1: Optional[str] = ""
    address_2: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    postcode: Optional[str] = ""
    country: Optional[str] = ""
    # Позволяем Pydantic принимать пустую строку и преобразовывать ее в None
    email: Optional[EmailStr] = Field(default=None) 
    phone: Optional[str] = ""

    # Валидатор, который будет применяться ко всей модели
    @model_validator(mode='before')
    def empty_str_to_none(cls, values):
        # Проходим по всем полям и заменяем пустые строки на None
        # Это сделает данные более чистыми и предсказуемыми
        if values:
            for k, v in values.items():
                if v == "":
                    values[k] = None
        return values

class UserCounters(BaseModel):
    cart_items_count: int
    favorite_items_count: int

# Схема для полного профиля пользователя, который мы отдаем клиенту
class UserProfile(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    email: EmailStr # Email верхнего уровня должен быть всегда, мы его сами создаем
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    billing: AddressSchema
    shipping: AddressSchema
    counters: UserCounters
    birth_date: date | None = None
    class Config:
        from_attributes = True

# Схема для данных, которые пользователь может обновить
class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    billing: Optional[AddressSchema] = None
    shipping: Optional[AddressSchema] = None
    birth_date: date | None = None

