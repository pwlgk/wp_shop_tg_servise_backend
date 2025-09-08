# app/bot/handlers/admin_actions.py
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import BackgroundTasks
from sqlalchemy import Column, String
from app.bot.filters.admin import IsAdminFilter
from app.clients.woocommerce import wc_client
from app.core.redis import redis_client
from app.db.session import SessionLocal
from app.models.broadcast import Broadcast
from app.bot.services.broadcast import process_broadcast
import asyncio
from app.dependencies import get_db_context # <-- Новый импорт!
from aiogram.exceptions import TelegramBadRequest
from app.bot.callbacks.admin import UserListCallback
from app.bot.services.admin_panel import generate_user_list_message
from aiogram.fsm.context import FSMContext
from app.crud import user as crud_user
from app.bot.services import admin_panel as admin_panel_service
from aiogram.types import CallbackQuery
from aiogram import F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.dependencies import get_db
from app.models.user import User

import logging

logger = logging.getLogger(__name__)

admin_actions_router = Router()
admin_actions_router.message.filter(IsAdminFilter()) # Защищаем все команды в этом файле

@admin_actions_router.message(Command("promo_welcome"))
async def set_welcome_bonus(message: Message):
    """
    Управляет акцией 'Приветственный бонус'.
    Примеры: /promo_welcome 300
             /promo_welcome on
             /promo_welcome off
    """
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Пожалуйста, укажите значение. Например: `/promo_welcome 300` или `/promo_welcome on`")
        return

    value = args[1].lower()
    payload = {}

    if value.isdigit():
        payload['welcome_bonus_amount'] = int(value)
    elif value in ['on', 'true', '1']:
        payload['is_welcome_bonus_active'] = True
    elif value in ['off', 'false', '0']:
        payload['is_welcome_bonus_active'] = False
    else:
        await message.answer("Неверное значение. Укажите число, 'on' или 'off'.")
        return

    try:
        # Отправляем запрос на наш новый эндпоинт в WP
        await wc_client.async_client.post("headless-api/v1/settings", json=payload)
        # Принудительно сбрасываем кеш настроек в Redis
        await redis_client.delete("shop_settings")
        await message.answer(f"✅ Настройки акции обновлены: `{list(payload.keys())[0]}` = `{list(payload.values())[0]}`")
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении настроек: {e}")


@admin_actions_router.message(Command("send_broadcast"))
async def create_broadcast_from_reply(message: Message):
    """
    Запускает рассылку.
    Нужно использовать как ответ (reply) на сообщение, которое будет рассылаться.
    """
    if not message.reply_to_message:
        await message.answer("❗️Пожалуйста, используйте эту команду как ответ на сообщение, которое вы хотите разослать.")
        return
        
    args = message.text.split()
    target_level = "all"
    if len(args) > 1 and args[1].lower() in ["bronze", "silver", "gold", "all"]:
        target_level = args[1].lower()

    # --- ИЗМЕНЕНИЕ ЛОГИКИ: СОЗДАЕМ ЗАДАЧУ В БД В ОТДЕЛЬНОЙ ФУНКЦИИ ---
    
    original_msg = message.reply_to_message
    broadcast_text = ""
    photo_file_id = None
    
    if original_msg.text:
        broadcast_text = original_msg.html_text
    elif original_msg.photo:
        photo_file_id = original_msg.photo[-1].file_id
        broadcast_text = original_msg.caption or ""
    else:
        await message.answer("❗️Для рассылки поддерживается только текст или фото с подписью.")
        return

    # Вызываем синхронную функцию для работы с БД
    broadcast_id = create_broadcast_in_db(
        message_text=broadcast_text,
        target_level=target_level,
        photo_file_id=photo_file_id
    )
    
    if broadcast_id:
        # Запускаем фоновую задачу
        asyncio.create_task(process_broadcast(broadcast_id=broadcast_id))
        await message.answer(f"✅ Рассылка запущена для группы '{target_level}'.\nID задачи: {broadcast_id}")
    else:
        await message.answer("❌ Не удалось создать задачу на рассылку.")

# --- НОВАЯ СИНХРОННАЯ ФУНКЦИЯ ДЛЯ РАБОТЫ С БД ---
def create_broadcast_in_db(message_text: str, target_level: str, photo_file_id: str | None) -> int | None:
    """Создает запись о рассылке в БД в изолированной сессии."""
    db = SessionLocal()
    try:
        # Вам нужно добавить поле photo_file_id в модель Broadcast и сделать миграцию
        new_broadcast = Broadcast(
            message_text=message_text,
            target_level=target_level,
            photo_file_id=photo_file_id
        )
        db.add(new_broadcast)
        db.commit()
        db.refresh(new_broadcast)
        return new_broadcast.id
    except Exception as e:
        logger.error(f"Error creating broadcast in DB: {e}")
        db.rollback()
        return None
    finally:
        db.close()


@admin_actions_router.message(Command("stats"))
async def get_stats_handler(message: Message):
    """Выводит общую статистику по пользователям."""
    with get_db_context() as db:
        try:
            total_users = crud_user.count_all_users(db)
            bronze_count = crud_user.count_users_by_level(db, "bronze")
            silver_count = crud_user.count_users_by_level(db, "silver")
            gold_count = crud_user.count_users_by_level(db, "gold")
            blocked_bot_count = crud_user.count_users_with_bot_blocked(db)
            
            stats_text = (
                f"📊 <b>Статистика по пользователям:</b>\n\n"
                f"👥 <b>Всего пользователей:</b> {total_users}\n\n"
                f"<b>По уровням:</b>\n"
                f"🥉 Бронза: {bronze_count}\n"
                f"🥈 Серебро: {silver_count}\n"
                f"🥇 Золото: {gold_count}\n\n"
                f"🤖 <b>Заблокировали бота:</b> {blocked_bot_count}"
            )
            await message.answer(stats_text)
        finally:
            db.close()

@admin_actions_router.message(Command("find_user"))
async def find_user_handler(message: Message):
    query = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not query:
        await message.answer("Пожалуйста, укажите ID, Telegram ID или username для поиска. Например: `/find_user 12345678`")
        return
        
    with get_db_context() as db:
        try:
            # 1. Сначала ищем в нашей быстрой БД по ID и username
            users = crud_user.find_users(db, query)
            
            # 2. Если ничего не нашли, ищем в "медленном" WooCommerce по ФИО
            if not users:
                logger.info(f"No users found in local DB for '{query}'. Searching in WooCommerce...")
                try:
                    # API WC ищет по частичному совпадению в имени, фамилии, email
                    wc_users_response = await wc_client.get("wc/v3/customers", params={"search": query})
                    wc_users_data = wc_users_response.json()
                    
                    if wc_users_data:
                        # Получаем telegram_id из email'ов (наш хак)
                        telegram_ids = [int(u['email'].split('@')[0]) for u in wc_users_data if '@telegram.user' in u['email']]
                        if telegram_ids:
                            # Находим этих пользователей в нашей БД
                            users = db.query(User).filter(User.telegram_id.in_(telegram_ids)).all()
                except Exception as e:
                    logger.error(f"Error searching users in WooCommerce: {e}")

            if not users:
                await message.answer("❌ Пользователи не найдены.")
            else:
                await message.answer(f"✅ Найдено пользователей: {len(users)}")
                for user in users:
                    card_text, builder = await admin_panel_service.format_user_card(user)
                    await message.answer(card_text, reply_markup=builder.as_markup())
        finally:
            db.close()

# --- Хендлеры для блокировки с подтверждением ---

@admin_actions_router.callback_query(F.data.startswith("user_block_confirm:"))
async def confirm_block_user(callback: CallbackQuery):
    """Шаг 1: Запрашивает подтверждение на блокировку."""
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный ID пользователя.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🔥 Да, заблокировать", callback_data=f"user_block_execute:{user_id}")
    builder.button(text="Отмена", callback_data=f"user_action_cancel:{user_id}")
    
    await callback.message.edit_text(
        f"Вы уверены, что хотите <b>заблокировать</b> пользователя с ID <code>{user_id}</code>?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@admin_actions_router.callback_query(F.data.startswith("user_block_execute:"))
async def execute_block_user(callback: CallbackQuery):
    """Шаг 2: Выполняет блокировку."""
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный ID пользователя.", show_alert=True)
        return

    with get_db_context() as db:
        try:
            user = crud_user.get_user_by_id(db, user_id)
            if user:
                user.is_blocked = True
                db.commit()
                db.refresh(user)
                # Обновляем карточку пользователя, чтобы показать новый статус
                card_text, builder = await admin_panel_service.format_user_card(user)
                try:
                    await callback.message.edit_text(card_text, reply_markup=builder.as_markup())
                except TelegramBadRequest as e:
                    # Игнорируем ошибку "message is not modified", но логируем остальные
                    if "message is not modified" not in str(e):
                        logger.error(f"Error editing message: {e}")
                # -------------------------
                
                await callback.answer("✅ Пользователь заблокирован.", show_alert=True)
            else:
                await callback.message.edit_text("Пользователь не найден.")
                await callback.answer("❌ Пользователь не найден.", show_alert=True)
        finally:
            db.close()

# --- ХЕНДЛЕРЫ ДЛЯ ПРОЦЕССА РАЗБЛОКИРОВКИ ---

@admin_actions_router.callback_query(F.data.startswith("user_unblock_confirm:"))
async def confirm_unblock_user(callback: CallbackQuery):
    """Шаг 1: Запрашивает подтверждение на разблокировку (аналогично)."""
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный ID пользователя.", show_alert=True)
        return
        
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, разблокировать", callback_data=f"user_unblock_execute:{user_id}")
    builder.button(text="Отмена", callback_data=f"user_action_cancel:{user_id}")
    await callback.message.edit_text(
        f"Вы уверены, что хотите <b>разблокировать</b> пользователя с ID <code>{user_id}</code>?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@admin_actions_router.callback_query(F.data.startswith("user_unblock_execute:"))
async def execute_unblock_user(callback: CallbackQuery):
    """Шаг 2: Выполняет разблокировку."""
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный ID пользователя.", show_alert=True)
        return

    with get_db_context() as db:
        try:
            user = crud_user.get_user_by_id(db, user_id)
            if user:
                user.is_blocked = False
                db.commit()
                db.refresh(user)
                card_text, builder = await admin_panel_service.format_user_card(user)
                await callback.message.edit_text(card_text, reply_markup=builder.as_markup())
                await callback.answer("✅ Пользователь разблокирован.", show_alert=True)
            else:
                await callback.message.edit_text("Пользователь не найден.")
                await callback.answer("❌ Пользователь не найден.", show_alert=True)
        finally:
            db.close()

# --- ХЕНДЛЕР ДЛЯ ОТМЕНЫ ДЕЙСТВИЯ ---

@admin_actions_router.callback_query(F.data.startswith("user_action_cancel:"))
async def cancel_user_action(callback: CallbackQuery):
    """Отменяет действие и возвращает исходную карточку пользователя."""
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный ID пользователя.", show_alert=True)
        return

    with get_db_context() as db:
        try:
            user = crud_user.get_user_by_id(db, user_id)
            if user:
                card_text, builder = await admin_panel_service.format_user_card(user)
                await callback.message.edit_text(card_text, reply_markup=builder.as_markup())
                await callback.answer("Действие отменено.")
            else:
                await callback.message.edit_text("Пользователь не найден.")
        finally:
            db.close()


@admin_actions_router.message(Command("users"))
async def list_users_handler(message: Message):
    """Показывает первую страницу списка пользователей."""
    with get_db_context() as db:
        try:
            text, markup = await generate_user_list_message(db)
            await message.answer(text, reply_markup=markup)
        finally:
            db.close()

@admin_actions_router.callback_query(UserListCallback.filter(F.action == "nav"))
async def navigate_user_list_handler(callback: CallbackQuery, callback_data: UserListCallback):
    """Обрабатывает навигацию по страницам."""
    with get_db_context() as db:
        try:
            text, markup = await generate_user_list_message(
                db, 
                page=callback_data.page, 
                level=callback_data.level, 
                bot_blocked=callback_data.bot_blocked
            )
            
            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            try:
                await callback.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    logger.error(f"Error editing user list message: {e}")
            # -------------------------
            
        except Exception as e:
            logger.error(f"Error in user list navigation: {e}")
            await callback.answer("Произошла ошибка.", show_alert=True)
            
    await callback.answer() # Ответ на колбэк, чтобы "часики" исчезли

@admin_actions_router.callback_query(UserListCallback.filter(F.action.in_(["f_level", "f_block"])))
async def filter_user_list_handler(callback: CallbackQuery, callback_data: UserListCallback):
    """Обрабатывает применение фильтров."""
    with get_db_context() as db:
        try:
            # Просто берем все данные из callback_data
            text, markup = await generate_user_list_message(
                db,
                page=1, # При смене фильтра всегда сбрасываем на 1-ю страницу
                level=callback_data.level,
                bot_blocked=callback_data.bot_blocked
            )

            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            try:
                await callback.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    logger.error(f"Error editing user list message: {e}")
            # -------------------------

        except Exception as e:
            logger.error(f"Error in user list filtering: {e}")
            await callback.answer("Произошла ошибка.", show_alert=True)
            
    await callback.answer()












@admin_actions_router.message(F.text, ~Command(commands=["start", "cancel", "help"]))
async def admin_fallback_handler(message: Message, state: FSMContext):
    """
    Ловит любые текстовые сообщения от админа, которые не подошли
    под другие админские хендлеры и если админ НЕ в состоянии FSM.
    """
    current_state = await state.get_state()
    if current_state is not None:
        return

    if message.chat.type == "private":
        # --- ИСПРАВЛЕННАЯ РАЗМЕТКА ---
        help_text = (
            "🤖 <b>Панель администратора</b>\n\n"
            "<b>Статистика:</b>\n"
            "<code>/stats</code> - общая статистика по пользователям\n\n"
            "<b>Поиск пользователя:</b>\n"
            "<code>/find_user &lt;ID/TG_ID/username&gt;</code>\n\n"
            "<b>Управление акциями:</b>\n"
            "<code>/promo_welcome &lt;сумма&gt;</code>\n"
            "<code>/promo_welcome on|off</code>\n\n"
            "<b>Рассылки (в админ-группе):</b>\n"
            "Ответьте на сообщение командой:\n"
            "<code>/send_broadcast &lt;all|bronze|silver|gold&gt;</code>"
        )
        # ----------------------------
        await message.answer(help_text, parse_mode="HTML")


