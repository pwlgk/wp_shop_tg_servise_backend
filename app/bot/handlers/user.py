# app/bot/handlers/user.py

from typing import Optional
from aiogram import F, Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, WebAppInfo, ContentType
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from fastapi import UploadFile
from sqlalchemy.orm import Session
from app.clients.woocommerce import wc_client
from app.core.config import settings
from app.dependencies import get_db, get_db_context
from app.crud import user as crud_user
from app.models.user import User
from app.services import auth as auth_service
from app.bot.core import bot
from app.bot.filters.admin import IsAdminFilter
import logging
from app.services import auth as auth_service, user as user_service, storage as storage_service
from app.crud import dialogue as crud_dialogue
from app.bot.utils.user_display import get_display_name
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
                db=db, # <-- Передаем db
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
    
    # --- НАЧАЛО ИЗМЕНЕНИЙ ---
    
    # 1. Получаем username бота из настроек, убирая символ @, если он есть
    bot_username = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
    
    # 2. Формируем ту самую deep link ссылку. 
    # Так как это главная кнопка, параметр startapp можно оставить пустым или использовать
    # нейтральное значение вроде "home", чтобы фронтенд мог его обработать.
    startapp_url = f"https://t.me/{bot_username}?startapp=home"
    
    # 3. Создаем ОБЫЧНУЮ URL-кнопку с нашей ссылкой
    builder.button(
        text="🛍️ Открыть магазин",
        url=startapp_url
    )
    
    # --- КОНЕЦ ИЗМЕНЕНИЙ ---
    
    await message.answer(
        f"👋 Привет, {message.from_user.full_name}!\n\nДобро пожаловать в наш магазин. Нажмите кнопку ниже, чтобы начать покупки.",
        reply_markup=builder.as_markup()
    )


@user_router.message(F.content_type == ContentType.CONTACT, ~IsAdminFilter())
async def handle_contact(message: Message):
    """
    Ловит контакт, которым поделился пользователь, сохраняет его,
    и отправляет в чат поддержки нативный контакт и уведомление.
    """
    contact = message.contact
    
    # Проверяем, что пользователь делится своим собственным контактом
    if message.from_user.id != contact.user_id:
        await message.answer("Пожалуйста, поделитесь вашим собственным контактом.")
        return

    with get_db_context() as db:
        user = crud_user.get_user_by_telegram_id(db, message.from_user.id)
        if not user:
            logger.warning(f"Received contact from an unknown user with TG ID {message.from_user.id}. Ignoring.")
            return

        # 1. Сохраняем/обновляем номер в нашей БД
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

        # 3. Создаем системное сообщение в диалоге
        display_name = await get_display_name_from_user(user)
        info_text = f"[Системное сообщение] Пользователь {display_name} поделился своим номером телефона: {contact.phone_number}"
        
        dialogue = crud_dialogue.get_open_dialogue_by_user(db, user_id=user.id)
        if dialogue:
            crud_dialogue.add_message_to_dialogue(db, dialogue=dialogue, sender=user, text=info_text, sender_type="user")
        else:
            dialogue = crud_dialogue.create_dialogue(db, user=user, first_message_text=info_text)
            
        # --- ОБНОВЛЕННАЯ ЛОГИКА ОТПРАВКИ УВЕДОМЛЕНИЯ В ЧАТ ПОДДЕРЖКИ ---
        
        # 4. Формируем текстовую часть уведомления
        notification_text = (
            f"📞 **Пользователь поделился контактом в диалоге #{dialogue.id}**\n\n"
            f"<b>От:</b> {display_name} (TG ID: <code>{user.telegram_id}</code>)"
        )
        
        # Формируем кнопку для перехода в профиль
        builder = InlineKeyboardBuilder()
        bot_username = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
        admin_app_name = getattr(settings, "TELEGRAM_ADMIN_APP_NAME", settings.TELEGRAM_MINI_APP_ADMIN_NAME)
        profile_param = f"users-{user.id}"
        profile_url = f"https://t.me/{bot_username}/{admin_app_name}?startapp={profile_param}"
        builder.button(text="👤 Открыть профиль клиента", url=profile_url)
        
        try:
            # 5. СНАЧАЛА отправляем нативный контакт
            await bot.send_contact(
                chat_id=settings.SUPPORT_CHAT_ID,
                phone_number=contact.phone_number,
                first_name=contact.first_name or message.from_user.first_name,
                last_name=contact.last_name or message.from_user.last_name
            )
            
            # 6. ВТОРЫМ сообщением отправляем контекст и кнопку
            await bot.send_message(
                chat_id=settings.SUPPORT_CHAT_ID,
                text=notification_text,
                reply_markup=builder.as_markup()
            )
        except Exception as e:
            logger.error(f"Failed to send contact notification to support chat.", exc_info=True)
            # Если не удалось отправить в чат, это не должно ломать флоу для юзера
        
        # ------------------------------------------------------------------

    # 7. Отправляем пользователю подтверждение
    await message.answer("✅ Спасибо, ваш номер телефона сохранен!")

async def get_display_name_from_user(user: User) -> str:
    """Вспомогательная функция для получения имени пользователя."""
    try:
        wc_user_data = (await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")).json()
        return get_display_name(wc_user_data, user)
    except Exception:
        return user.username or f"ID {user.telegram_id}"

# --- ПОЛНАЯ ВЕРСИЯ ХЕНДЛЕРА ---
@user_router.message(
    ~IsAdminFilter(), # Фильтр на НЕ-админа
    F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT})
)
async def handle_any_user_message(message: Message):
    """
    Ловит текст, фото, видео и документы от пользователя, сохраняет в диалог
    и отправляет уведомление в чат поддержки.
    """
    user_tg_id = message.from_user.id
    
    # 1. Готовим данные для сохранения в БД
    text_content = message.text or message.caption or ""
    media_type: Optional[str] = None
    media_url: Optional[str] = None
    file_name: Optional[str] = None
    
    # Если есть медиа, обрабатываем его
    if message.content_type != ContentType.TEXT:
        media_type = message.content_type.lower()
        file_id_to_download = None
        
        if message.photo:
            file_id_to_download = message.photo[-1].file_id # Берем фото наибольшего разрешения
            file_name = f"photo_{file_id_to_download}.jpg"
        elif message.video:
            file_id_to_download = message.video.file_id
            file_name = message.video.file_name or f"video_{file_id_to_download}.mp4"
        elif message.document:
            file_id_to_download = message.document.file_id
            file_name = message.document.file_name or f"document_{file_id_to_download}"
            
        if file_id_to_download:
            try:
                # 1. Скачиваем файл из Telegram в виде байтового потока в памяти
                file_info = await bot.get_file(file_id_to_download)
                file_stream = await bot.download_file(file_info.file_path) # Это объект io.BytesIO
                
                # 2. Определяем ContentType (MIME-тип)
                content_type = "application/octet-stream" # Значение по умолчанию
                if message.photo:
                    content_type = "image/jpeg"
                elif message.video and message.video.mime_type:
                    content_type = message.video.mime_type
                elif message.document and message.document.mime_type:
                    content_type = message.document.mime_type

                # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Прямая передача в сервис ---
                # Нам не нужно создавать объект UploadFile.
                # Мы передаем сам файловый поток и его метаданные.
                media_url = await storage_service.upload_file_object_to_s3(
                    file_obj=file_stream,
                    file_name=file_name,
                    content_type=content_type,
                    bucket_name=settings.S3_BUCKET_NAME
                )

            except Exception as e:
                logger.error(f"Failed to process media from user {user_tg_id}", exc_info=True)
                await message.answer("❌ Не удалось обработать ваш файл. Попробуйте еще раз.")
                return

    # Если нет ни текста, ни успешно загруженного медиа, игнорируем сообщение
    if not text_content and not media_url:
        logger.warning(f"Ignoring message from {user_tg_id} because it has no text and media processing failed.")
        return

    # 2. Работа с базой данных
    with get_db_context() as db:
        user = crud_user.get_user_by_telegram_id(db, user_tg_id)
        if not user:
            logger.warning(f"Received a message from an unknown user with TG ID {user_tg_id}. Ignoring.")
            return

        # Ищем открытый диалог или создаем новый
        dialogue = crud_dialogue.get_open_dialogue_by_user(db, user_id=user.id)
        
        if dialogue:
            crud_dialogue.add_message_to_dialogue(
                db, dialogue=dialogue, sender=user, text=text_content, 
                sender_type="user", media_type=media_type, media_url=media_url, file_name=file_name
            )
        else:
            dialogue = crud_dialogue.create_dialogue(
                db, user=user, first_message_text=text_content, 
                media_type=media_type, media_url=media_url, file_name=file_name
            )
    
        # 3. Формирование и отправка уведомления в чат поддержки
        display_name = await get_display_name_from_user(user)
        
        # Формируем текстовую часть уведомления
        message_part = f"«<i>{text_content}</i>»" if text_content else "[Медиафайл без подписи]"
        if media_url:
            message_part += f"\n📎 <a href='{media_url}'>Просмотреть вложение ({file_name or media_type})</a>"

        notification_text = (
            f"💬 <b>Новое сообщение в диалоге #{dialogue.id}</b>\n\n"
            f"<b>От:</b> {display_name} (TG ID: <code>{user.telegram_id}</code>)\n"
            f"<b>Сообщение:</b> {message_part}"
        )
        
        # Формирование кнопок
        builder = InlineKeyboardBuilder()
        bot_username = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
        admin_app_name = getattr(settings, "TELEGRAM_ADMIN_APP_NAME", settings.TELEGRAM_MINI_APP_ADMIN_NAME)
        
        reply_param = f"support-dialogues-{dialogue.id}"
        profile_param = f"users-{user.id}"
        
        reply_url = f"https://t.me/{bot_username}/{admin_app_name}?startapp={reply_param}"
        profile_url = f"https://t.me/{bot_username}/{admin_app_name}?startapp={profile_param}"
        
        builder.button(text="✍️ Ответить в Mini App", url=reply_url)
        builder.button(text="👤 Профиль клиента", url=profile_url)
        builder.adjust(1)
        
        try:
            await bot.send_message(
                chat_id=settings.SUPPORT_CHAT_ID,
                text=notification_text,
                reply_markup=builder.as_markup()
            )
            # Отвечаем пользователю, что его сообщение получено
            await message.answer("✅ Ваше сообщение получено. Менеджер скоро ответит вам.")
        except Exception as e:
            logger.error(f"Failed to send support notification to chat {settings.SUPPORT_CHAT_ID}", exc_info=True)
            await message.answer("Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже.")