# app/bot/handlers/user.py

from aiogram import F, Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, WebAppInfo, ContentType
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies import get_db
from app.crud import user as crud_user
from app.services import auth as auth_service
from app.bot.core import bot
from app.bot.filters.admin import IsAdminFilter
import logging

logger = logging.getLogger(__name__)
# Создаем роутер для этого модуля.
user_router = Router()


async def forward_to_admin(message: Message):
    """
    Общая логика для пересылки любого сообщения в админ-чат.
    """
    user_info = f"<b>Сообщение от клиента:</b> {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    user_info += f"\n(ID: <code>{message.from_user.id}</code>)"

    builder = InlineKeyboardBuilder()
    builder.button(text="🤖 Ответить", callback_data=f"reply_to:{message.from_user.id}")

    try:
        # Пересылаем оригинальное сообщение
        await message.forward(chat_id=settings.ADMIN_CHAT_ID)
        # И следом отправляем инфо с кнопкой
        await bot.send_message(
            chat_id=settings.ADMIN_CHAT_ID,
            text=user_info,
            reply_markup=builder.as_markup()
        )
        await message.answer("✅ Ваше сообщение передано менеджеру. Мы скоро ответим!")
    except Exception as e:
        logger.error(f"Failed to forward user message: {e}")
        await message.answer("Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже.")


@user_router.message(CommandStart(), ~IsAdminFilter())
async def command_start_handler(message: Message, command: CommandObject) -> None:
    """
    Обрабатывает команду /start от НЕ-админов,
    регистрирует пользователя и ловит реферальный код.
    """
    referral_code = None
    if command.args and command.args.startswith("ref_"):
        referral_code = command.args.split("ref_")[1]
    
    db: Session = next(get_db())
    try:
        db_user = await auth_service.register_or_get_user(
            db=db,
            user_info=message.from_user.model_dump(),
            referral_code=referral_code
        )
        
        if not db_user.bot_accessible:
            logger.info(f"User {db_user.id} re-activated the bot. Setting bot_accessible to True.")
            db_user.bot_accessible = True
            db.commit()
    finally:
        db.close()
    
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🛍️ Открыть магазин",
        web_app=WebAppInfo(url=settings.MINI_APP_URL) 
    )
    
    await message.answer(
        f"👋 Привет, {message.from_user.full_name}!\n\nДобро пожаловать в наш магазин. Нажмите кнопку ниже, чтобы начать покупки.",
        reply_markup=builder.as_markup()
    )


@user_router.message(F.content_type == ContentType.CONTACT, ~IsAdminFilter())
async def handle_contact(message: Message):
    """
    Ловит контакт, которым поделился пользователь (НЕ-админ), и пересылает в админ-чат.
    """
    contact = message.contact
    user_info = f"<b>От:</b> {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    
    await bot.send_contact(
        chat_id=settings.ADMIN_CHAT_ID,
        phone_number=contact.phone_number,
        first_name=contact.first_name,
        last_name=contact.last_name
    )
    await bot.send_message(settings.ADMIN_CHAT_ID, user_info)
    await message.answer("Спасибо, мы получили ваш номер телефона!")


@user_router.message(~IsAdminFilter())
async def handle_any_user_message(message: Message):
    """
    Ловит ЛЮБОЕ другое сообщение от пользователя (НЕ-админа), которое не подошло
    под более специфичные хендлеры (/start, контакт), и пересылает его админу.
    """
    await forward_to_admin(message)