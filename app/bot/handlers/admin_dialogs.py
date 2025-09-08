# app/bot/handlers/admin_dialogs.py
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from app.bot.filters.admin import IsAdminFilter
from app.bot.core import bot
from app.bot.services import notification as notification_service # Для безопасной отправки
from app.dependencies import get_db
from app.crud import user as crud_user
from app.clients.woocommerce import wc_client
from app.dependencies import get_db_context # <-- Новый импорт!
import logging

logger = logging.getLogger(__name__)

admin_dialog_router = Router()
# Применяем фильтр ко всем хендлерам в этом роутере
admin_dialog_router.message.filter(IsAdminFilter())
admin_dialog_router.callback_query.filter(IsAdminFilter())

# Создаем состояния для конечного автомата (FSM)
class ReplyState(StatesGroup):
    waiting_for_reply = State()

@admin_dialog_router.callback_query(F.data.startswith("reply_to:"))
async def start_reply_handler(callback: CallbackQuery, state: FSMContext):
    """Ловит нажатие на кнопку 'Ответить от бота'."""
    customer_id_str = callback.data.split(":")[1]
    with get_db_context() as db:
        try:
            customer_id = int(customer_id_str)
            # Находим пользователя в нашей БД, чтобы получить его wordpress_id
            customer_local = crud_user.get_user_by_telegram_id(db, customer_id)
            
            customer_name = f"ID {customer_id}" # Значение по умолчанию
            
            if customer_local:
                try:
                    # --- НОВАЯ ЛОГИКА: Запрашиваем данные из WooCommerce ---
                    wc_customer_response = await wc_client.get(f"wc/v3/customers/{customer_local.wordpress_id}")
                    wc_customer_data = wc_customer_response.json()
                    
                    # Собираем имя
                    first_name = wc_customer_data.get("first_name", "")
                    last_name = wc_customer_data.get("last_name", "")
                    full_name = f"{first_name} {last_name}".strip()
                    
                    # Используем полное имя, если оно есть, иначе username, иначе ID
                    customer_name = full_name or customer_local.username or f"ID {customer_id}"
                    
                except Exception as e:
                    logger.error(f"Could not fetch customer name from WooCommerce: {e}")
                    # Если не удалось получить данные из WP, используем то, что есть у нас
                    customer_name = customer_local.username or f"ID {customer_id}"

            await state.update_data(customer_id=customer_id, customer_name=customer_name)
            await state.set_state(ReplyState.waiting_for_reply)
            
            await callback.message.reply(
                f"📝 Введите сообщение для <b>{customer_name}</b> (<code>{customer_id}</code>).\nДля отмены введите /cancel",
                parse_mode="HTML"
            )
        except (ValueError, IndexError):
            await callback.message.reply("Ошибка: неверный ID пользователя.")
        finally:
            db.close()
    
    await callback.answer()

@admin_dialog_router.message(ReplyState.waiting_for_reply, Command("cancel"))
async def cancel_reply_handler(message: Message, state: FSMContext):
    """Обрабатывает отмену диалога."""
    await state.clear()
    await message.answer("Действие отменено.")

@admin_dialog_router.message(ReplyState.waiting_for_reply, F.text)
async def send_text_reply_handler(message: Message, state: FSMContext):
    """Получает ТЕКСТОВЫЙ ответ от админа и отправляет его клиенту."""
    data = await state.get_data()
    customer_id = data.get("customer_id")
    customer_name = data.get("customer_name")
    
    if not customer_id:
        await message.answer("❌ Произошла ошибка: не найден ID клиента. Попробуйте снова.")
        await state.clear()
        return

    with get_db_context() as db:
        try:
            user_to_reply = crud_user.get_user_by_telegram_id(db, customer_id)
            if not user_to_reply:
                await message.answer(f"❌ Пользователь с ID {customer_id} не найден в нашей базе.")
            else:
                # Используем нашу безопасную функцию отправки
                success = await notification_service._send_message(db, user_to_reply, f"💬 <b>Сообщение от менеджера:</b>\n\n{message.html_text}")
                if success:
                    await message.answer(f"✅ Сообщение успешно отправлено для <b>{customer_name}</b>.", parse_mode="HTML")
                else:
                    await message.answer(f"❌ Не удалось отправить сообщение для {customer_name}. Возможно, пользователь заблокировал бота.")
        finally:
            db.close()
    
    await state.clear()


# --- НОВЫЙ ХЕНДЛЕР ДЛЯ ФОТО ---
@admin_dialog_router.message(ReplyState.waiting_for_reply, F.photo)
async def send_photo_reply_handler(message: Message, state: FSMContext):
    """Получает ФОТО от админа и отправляет его клиенту."""
    data = await state.get_data()
    customer_id = data.get("customer_id")
    customer_name = data.get("customer_name")

    if not customer_id:
        # ... (обработка ошибки)
        return

    with get_db_context() as db:
        try:
            user_to_reply = crud_user.get_user_by_telegram_id(db, customer_id)
            if not user_to_reply:
                pass
            else:
                photo_id = message.photo[-1].file_id
                caption = f"🖼️ <b>Изображение от менеджера:</b>\n\n{message.caption or ''}"
                
                # Нужен кастомный метод отправки, так как _send_message не умеет слать фото
                # Давайте создадим его в notification_service
                success = await notification_service.send_photo_to_user(db, user_to_reply, photo_id, caption)

                if success:
                    await message.answer(f"✅ Фото успешно отправлено для <b>{customer_name}</b>.", parse_mode="HTML")
                else:
                    await message.answer(f"❌ Не удалось отправить фото для {customer_name}.")
        finally:
            db.close()

    await state.clear()


@admin_dialog_router.callback_query(F.data.startswith("request_contact:"))
async def request_contact_handler(callback: CallbackQuery):
    """Ловит нажатие админом кнопки 'Запросить контакт'."""
    customer_id_str = callback.data.split(":")[1]
    with get_db_context() as db:
        try:
            customer = crud_user.get_user_by_telegram_id(db, int(customer_id_str))
            if customer:
                admin_name = callback.from_user.first_name
                await notification_service.request_contact_from_user(db, customer, admin_name)
                await callback.answer("✅ Запрос на контакт отправлен пользователю.", show_alert=True)
            else:
                await callback.answer("❌ Пользователь не найден.", show_alert=True)
        finally:
            db.close()