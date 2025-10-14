# app/bot/handlers/user.py

from aiogram import F, Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, WebAppInfo, ContentType
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy.orm import Session
from app.clients.woocommerce import wc_client
from app.core.config import settings
from app.dependencies import get_db, get_db_context
from app.crud import user as crud_user
from app.services import auth as auth_service
from app.bot.core import bot
from app.bot.filters.admin import IsAdminFilter
import logging
from app.services import user as user_service

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
    if command.args and command.args.startswith("request_contact_"):
        # TODO: Можно добавить проверку токена из command.args, если нужна доп. безопасность
        
        builder = ReplyKeyboardBuilder()
        builder.button(text="📞 Поделиться номером", request_contact=True)
        
        await message.answer(
            "Пожалуйста, нажмите кнопку ниже, чтобы поделиться вашим номером телефона.",
            reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
        )
        # Важно! Завершаем выполнение, чтобы не отправлять основное приветствие
        return
    
    referral_code = None
    if command.args and command.args.startswith("ref_"):
        referral_code = command.args.split("ref_")[1]
    
    db: Session = next(get_db())
    try:
        db_user = await auth_service.register_or_get_user(
            user_info=message.from_user.model_dump(),
            referral_code=referral_code
        )
        user_service.update_user_profile_from_telegram(db, db_user, message.from_user.model_dump())
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
    Ловит контакт, которым поделился пользователь, сохраняет его
    и пересылает в админский чат.
    """
    contact = message.contact
    
    # Проверяем, что пользователь делится своим собственным контактом
    if message.from_user.id != contact.user_id:
        await message.answer("Пожалуйста, поделитесь вашим собственным контактом.")
        return

    # --- НОВАЯ ЛОГИКА СОХРАНЕНИЯ ---
    with get_db_context() as db:
        user = crud_user.get_user_by_telegram_id(db, message.from_user.id)
        if user:
            # 1. Сохраняем в нашу БД
            crud_user.update_user_phone(db, user, contact.phone_number)
            
            # 2. Асинхронно синхронизируем с WooCommerce
            try:
                await wc_client.post(
                    f"wc/v3/customers/{user.wordpress_id}",
                    json={"billing": {"phone": contact.phone_number}}
                )
                logger.info(f"Successfully synced phone for user {user.id} to WooCommerce.")
            except Exception as e:
                logger.error(f"Failed to sync phone for user {user.id} to WooCommerce.", exc_info=True)
        else:
            logger.warning(f"Received contact from user {message.from_user.id}, but user not found in DB.")

    # 3. Отправляем пользователю подтверждение и кнопку для возврата
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться в магазин", web_app=WebAppInfo(url=settings.MINI_APP_URL))
    
    await message.answer(
        "✅ Спасибо, ваш номер телефона сохранен! Можете вернуться к покупкам.",
        reply_markup=builder.as_markup()
    )
    # ---------------------------

    # Пересылаем контакт админу (старая логика)
    user_info = f"<b>От:</b> {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    
    await bot.send_contact(
        chat_id=settings.ADMIN_CHAT_ID,
        phone_number=contact.phone_number,
        first_name=contact.first_name,
        last_name=contact.last_name,
        user_id=contact.user_id
    )
    await bot.send_message(settings.ADMIN_CHAT_ID, user_info)

@user_router.message(~IsAdminFilter())
async def handle_any_user_message(message: Message):
    """
    Ловит ЛЮБОЕ другое сообщение от пользователя (НЕ-админа), которое не подошло
    под более специфичные хендлеры (/start, контакт), и пересылает его админу.
    """
    await forward_to_admin(message)