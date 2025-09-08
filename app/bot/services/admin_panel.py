# app/bot/services/admin_panel.py
from typing import Optional
from app.models.user import User
from app.clients.woocommerce import wc_client
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.bot.utils.user_display import get_display_name
from app.crud import user as crud_user
from app.bot.callbacks.admin import UserListCallback
import math
from sqlalchemy.orm import Session
from aiogram.types import InlineKeyboardButton # <-- Добавляем импорт
from app.crud import user as crud_user
from app.clients.woocommerce import wc_client

async def format_user_card(user: User) -> tuple[str, InlineKeyboardBuilder]:
    """Формирует текст и кнопки для карточки пользователя."""
    # Получаем доп. инфо из WC
    wc_user_data = (await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")).json()
    display_name = get_display_name(wc_user_data, user)

    # full_name = f"{wc_user_data.get('first_name', '')} {wc_user_data.get('last_name', '')}".strip()
    
    # Получаем последние 3 заказа
    orders_data = (await wc_client.get(f"wc/v3/orders", params={"customer": user.wordpress_id, "per_page": 3})).json()
    orders_lines = []
    if orders_data:
        for order in orders_data:
            orders_lines.append(f"  • Заказ #{order['number']} ({order['status']}) - {order['total']} руб.")
    else:
        orders_lines.append("<i>Заказов пока нет.</i>")
    
    # Формируем текст
    card_text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"<b>ID:</b> <code>{user.id}</code> (TG ID: <code>{user.telegram_id}</code>)\n"
        # f"<b>Имя:</b> {full_name or '<i>не указано</i>'}\n"
        f"<b>Имя:</b> {display_name}\n"

        f"<b>Username:</b> @{user.username or '<i>не указан</i>'}\n"
        f"<b>Уровень:</b> {user.level.capitalize()}\n"
        f"<b>Статус:</b> {'✅ Активен' if not user.is_blocked else '🚫 Заблокирован'}\n\n"
        f"<b>Последние заказы:</b>\n" + "\n".join(orders_lines)
    )
    
    # Формируем кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Написать", url=f"tg://user?id={user.telegram_id}")
    builder.button(text="🤖 Ответить", callback_data=f"reply_to:{user.telegram_id}")
    
    if user.is_blocked:
        builder.button(text="✅ Разблокировать", callback_data=f"user_unblock_confirm:{user.id}")
    else:
        builder.button(text="🚫 Заблокировать", callback_data=f"user_block_confirm:{user.id}")
        
    builder.adjust(2)
    return card_text, builder


USERS_PER_PAGE = 5

async def generate_user_list_message(
    db: Session,
    page: int = 1,
    level: str | None = None,
    bot_blocked: bool | None = None
):
    """Генерирует текст и клавиатуру для пагинированного списка пользователей."""
    
    skip = (page - 1) * USERS_PER_PAGE
    users = crud_user.get_users(db, skip, USERS_PER_PAGE, level, bot_blocked)
    total_users = crud_user.count_users_with_filters(db, level, bot_blocked)
    total_pages = math.ceil(total_users / USERS_PER_PAGE) if total_users > 0 else 1

    message_lines = [f"👥 <b>Список пользователей</b> (Стр. {page}/{total_pages})\n"]
    if users:
        for user in users:
            try:
                wc_user_data = (await wc_client.get(f"wc/v3/customers/{user.wordpress_id}")).json()
                display_name = f"{wc_user_data.get('first_name', '')} {wc_user_data.get('last_name', '')}".strip() or user.username or f"ID {user.telegram_id}"
            except Exception:
                display_name = user.username or f"ID {user.telegram_id}"
            
            status_icon = "✅" if user.bot_accessible else "🤖"
            block_icon = "🚫" if user.is_blocked else ""
            message_lines.append(f"• /find_user <code>{user.telegram_id}</code> - {display_name} {status_icon}{block_icon}")
    else:
        message_lines.append("<i>Пользователи не найдены.</i>")

    # --- ИСПРАВЛЕННАЯ ЛОГИКА СОЗДАНИЯ КЛАВИАТУРЫ ---
    builder = InlineKeyboardBuilder()
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                # --- ИЗМЕНЕНИЕ: передаем фильтры в явном виде ---
                callback_data=UserListCallback(action="nav", page=page-1, level=level, bot_blocked=bot_blocked).pack()
            )
        )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                # --- ИЗМЕНЕНИЕ ---
                callback_data=UserListCallback(action="nav", page=page+1, level=level, bot_blocked=bot_blocked).pack()
            )
        )
    
    # Кнопки фильтров по уровню
    filter_buttons_row1 = []
    levels = ["all", "bronze", "silver", "gold"]
    for lvl in levels:
        text = f"🏅 {lvl.capitalize()}" if lvl != level else f"✅ {lvl.capitalize()}"
        filter_buttons_row1.append(
            InlineKeyboardButton(text=text, callback_data=UserListCallback(
                action="f_level", 
                page=1,
                # Передаем новое значение для level
                level=lvl, 
                # СОХРАНЯЕМ текущее значение для bot_blocked
                bot_blocked=bot_blocked 
            ).pack())
        )

    # Кнопки фильтров по статусу бота
    filter_buttons_row2 = []
    blocked_text = "🤖 Забл."
    if bot_blocked is True: blocked_text = "✅ Забл."
    if bot_blocked is False: blocked_text = "☑️ Не забл." # Пользователи, которые НЕ заблокировали
    
    # Определяем, каким будет СЛЕДУЮЩЕЕ состояние фильтра
    next_bot_blocked_state: Optional[bool] = None
    if bot_blocked is None: next_bot_blocked_state = True
    elif bot_blocked is True: next_bot_blocked_state = False
    elif bot_blocked is False: next_bot_blocked_state = None
        
    # filter_buttons_row2.append(
    #     InlineKeyboardButton(
    #         text=blocked_text,
    #         callback_data=UserListCallback(
    #             action="f_block",
    #             page=1,
    #             # СОХРАНЯЕМ текущее значение для level
    #             level=level,
    #             # ПЕРЕДАЕМ новое значение для bot_blocked
    #             bot_blocked=next_bot_blocked_state
    #         ).pack()
    #     )
    # )

    # Добавляем кнопки в билдер
    if filter_buttons_row1: builder.row(*filter_buttons_row1)
    if nav_buttons or filter_buttons_row2: builder.row(*nav_buttons, *filter_buttons_row2)

    return "\n".join(message_lines), builder.as_markup()