# app/bot/services/notification.py
import asyncio
from sqlalchemy.orm import Session
from aiogram.exceptions import TelegramForbiddenError
from app.schemas.order import Order
from app.bot.core import bot
from app.models.user import User
from app.crud import user as crud_user # Нам понадобится CRUD для обновления статуса бота
from app.core.config import settings
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.models.user import User
from aiogram.utils.keyboard import ReplyKeyboardBuilder # <-- Меняем импорт
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
import logging

logger = logging.getLogger(__name__)

async def _send_message(db: Session, user: User, text: str) -> tuple[bool, str | None]:
    """
    Приватная функция-обертка для безопасной отправки сообщений.
    Обновляет статус 'bot_accessible' в случае блокировки.
    Возвращает кортеж (успех: bool, причина_неудачи: str | None).
    """
    if not user.bot_accessible:
        reason = "Bot is marked as inaccessible"
        logger.info(f"Skipping notification for user {user.id}: {reason}.")
        return False, reason
            
    try:
        await bot.send_message(chat_id=user.telegram_id, text=text)
        return True, None # Успех, причины нет
    except TelegramForbiddenError:
        reason = "User has blocked the bot"
        logger.error(f"User {user.id} has blocked the bot. Updating status.")
        user.bot_accessible = False
        db.add(user)
        db.commit()
        return False, reason
    except Exception as e:
        reason = str(e) # Любая другая ошибка
        logger.error(f"Failed to send message to user {user.id}: {reason}")
        return False, reason
    

async def ping_user(db: Session, user: User) -> bool:
    """
    Проверяет доступность пользователя, отправляя и сразу удаляя "тихое" сообщение.
    Возвращает актуальный статус доступности (True/False).
    """
    # Мы не можем полагаться на user.bot_accessible, так как пользователь мог разблокировать бота.
    # Поэтому мы всегда пытаемся отправить пинг.
    try:
        # Отправляем "тихое" сообщение без уведомления на стороне клиента
        sent_message = await bot.send_message(
            chat_id=user.telegram_id, 
            text=".", # Просто точка или любой другой минимальный символ
            disable_notification=True
        )
        # Если отправка удалась, сразу удаляем это сообщение
        await bot.delete_message(
            chat_id=user.telegram_id, 
            message_id=sent_message.message_id
        )
        
        # Если мы дошли до сюда, значит, бот доступен. Обновим статус в БД.
        if not user.bot_accessible:
            user.bot_accessible = True
            db.add(user)
            db.commit()
        return True

    except TelegramForbiddenError:
        # Пользователь заблокировал бота
        if user.bot_accessible:
            user.bot_accessible = False
            db.add(user)
            db.commit()
        return False
    except Exception as e:
        # Другая ошибка (например, чат не найден)
        logger.error(f"Ping failed for user {user.id}: {e}")
        if user.bot_accessible:
            user.bot_accessible = False
            db.add(user)
            db.commit()
        return False

def _format_order_details(order: Order) -> str:
    """Вспомогательная функция для форматирования деталей заказа в текст."""
    
    # --- Получатель и контакты ---
    billing = order.billing
    recipient_name = f"{billing.first_name or ''} {billing.last_name or ''}".strip()
    recipient_lines = []
    if recipient_name:
        recipient_lines.append(f"<b>Получатель:</b> {recipient_name}")
    if billing.phone:
        recipient_lines.append(f"<b>Номер телефона:</b> {billing.phone}")
    if billing.email:
        recipient_lines.append(f"<b>Email:</b> {billing.email}")
    
    # --- Состав заказа ---
    items_lines = ["<b>Состав заказа:</b>"]
    for item in order.line_items:
        items_lines.append(f"• {item.name} ({item.quantity} шт.) - {item.total} руб.")
        
    # --- Собираем все вместе ---
    message_parts = [
        f"✅ Заказ №<b>{order.number}</b> успешно оформлен!\n",
        f"<b>Способ оплаты:</b> {order.payment_method_title}",
    ]
    message_parts.extend(recipient_lines)
    
    # Добавляем пустую строку для разделения, если есть данные получателя
    if recipient_lines:
        message_parts.append("")
        
    message_parts.extend(items_lines)
    message_parts.append(f"\n<b>Итоговая сумма: {order.total} руб.</b>")
    
    # Добавляем финальное сообщение в зависимости от способа оплаты
    if order.status == 'on-hold': # Это наш "Согласование с менеджером"
        message_parts.append("\nСпасибо за ваш заказ! В ближайшее время с вами свяжется менеджер для подтверждения.")
    else:
        message_parts.append("\nСпасибо за ваш заказ!")
        
    return "\n".join(message_parts)


async def send_new_order_confirmation(db: Session, user: User, order: Order):
    """
    Уведомление о создании нового заказа (теперь принимает весь объект заказа).
    """
    message = _format_order_details(order)
    await _send_message(db, user, message)
async def send_order_cancellation_confirmation(db: Session, user: User, order_id: int):
    """Уведомление об отмене заказа."""
    message = f"✅ Заказ №<b>{order_id}</b> был успешно отменен."
    await _send_message(db, user, message)

async def send_order_status_update(db: Session, user: User, order_id: int, new_status_title: str):
    """Уведомление об изменении статуса заказа."""
    message = f"🔔 Статус вашего заказа №<b>{order_id}</b> изменен на: <b>{new_status_title}</b>"
    await _send_message(db, user, message)

async def send_points_earned(db: Session, user: User, points_added: int, order_id: int):
    """Уведомление о начислении бонусных баллов."""
    message = (
        f"💰 Вам начислено <b>{points_added} бонусных баллов</b> за заказ №<b>{order_id}</b>!\n\n"
        f"Используйте их для оплаты следующих покупок."
    )
    await _send_message(db, user, message)

async def send_referral_bonus(db: Session, user: User, referred_user_name: str, points_added: int):
    """Уведомление о начислении бонуса за покупку приглашенного пользователя."""
    message = (
        f"🎉 Поздравляем! Ваш друг <b>{referred_user_name}</b> совершил первую покупку!\n\n"
        f"Вам начислено <b>{points_added} бонусных баллов</b> в качестве вознаграждения. Спасибо, что вы с нами!"
    )
    await _send_message(db, user, message)

async def request_contact_from_user(db: Session, user: User, admin_name: str):
    """
    Отправляет пользователю сообщение с кнопкой запроса контакта,
    используя правильный тип клавиатуры (ReplyKeyboardMarkup).
    """
    message_text = (
        f"👋 Здравствуйте! Менеджер <b>{admin_name}</b> хотел бы связаться с вами для уточнения деталей заказа. "
        f"Пожалуйста, нажмите на кнопку ниже, чтобы поделиться вашим номером телефона."
    )
    
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Используем ReplyKeyboardBuilder ---
    builder = ReplyKeyboardBuilder()
    # Создаем кнопку с параметром request_contact
    builder.add(KeyboardButton(text="📞 Поделиться контактом", request_contact=True))
    
    # Конфигурируем клавиатуру, чтобы она исчезла после одного нажатия
    keyboard = builder.as_markup(
        resize_keyboard=True, 
        one_time_keyboard=True
    )
    # --------------------------------------------------------

    if not user.bot_accessible:
        logger.info(f"Skipping contact request for user {user.id}: bot is marked as inaccessible.")
        return

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message_text,
            reply_markup=keyboard # <-- Передаем новую клавиатуру
        )
    except TelegramForbiddenError:
        logger.warning(f"User {user.id} has blocked the bot. Updating status.")
        user.bot_accessible = False
        db.add(user)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to send contact request to user {user.id}: {e}")

async def send_new_order_to_admin(order: Order, customer: User):
    """Отправляет детали нового заказа в админский чат."""
    message_text = _format_order_details(order)
    
    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # Берем ФИО из адреса (`billing`) самого заказа, а не из объекта User
    billing_info = order.billing
    customer_name = f"{billing_info.first_name or ''} {billing_info.last_name or ''}".strip()
    
    # Используем username из нашего объекта User как дополнительную информацию
    customer_info = f"👤 <b>Клиент:</b> {customer_name}"
    if customer.username:
        customer_info += f" (@{customer.username})"
    
    admin_message = f"<b>🔥 Новый заказ!</b>\n{customer_info}\n\n{message_text}"
    # --------------------------------

    builder = InlineKeyboardBuilder()
    
    customer_telegram_id = order.customer_telegram_id
    if customer_telegram_id:
        builder.button(text="👤 Написать клиенту", url=f"tg://user?id={customer_telegram_id}")
        builder.button(text="🤖 Ответить от бота", callback_data=f"reply_to:{customer_telegram_id}")
        # --- НОВАЯ КНОПКА ЗАПРОСА КОНТАКТА ---
        builder.button(text="📞 Запросить контакт", callback_data=f"request_contact:{customer_telegram_id}")
    
    # --- НОВАЯ DEEP LINK ССЫЛКА ---
    # Deep link для открытия заказа в мобильном приложении WooCommerce
    builder.button(text="🔗 Заказ в WP", url=f"{settings.WP_URL}/wp-admin/post.php?post={order.id}&action=edit")
    builder.adjust(2)

    await bot.send_message(
        chat_id=settings.ADMIN_CHAT_ID,
        text=admin_message,
        reply_markup=builder.as_markup()
    )

async def send_broadcast_report_to_admin(broadcast_id: int, sent_count: int, failed_users_info: list):
    """Отправляет итоговый отчет о рассылке в админский чат."""
    
    failed_count = len(failed_users_info)
    total_processed = sent_count + failed_count

    report_lines = [
        f"<b>📊 Отчет по рассылке #{broadcast_id}</b>\n",
        f"✅ <b>Успешно отправлено:</b> {sent_count}",
        f"❌ <b>Не удалось отправить:</b> {failed_count}",
        f"👥 <b>Всего обработано:</b> {total_processed}\n"
    ]

    if failed_count > 0:
        report_lines.append("<b>Список неудачных отправок:</b>")
        
        # Ограничим вывод, чтобы не спамить в чат, если ошибок много
        for i, failure in enumerate(failed_users_info[:10]):
            user = failure['user']
            reason = failure['reason']
            user_info = f"<a href='tg://user?id={user.telegram_id}'>{user.username or user.telegram_id}</a>"
            report_lines.append(f"{i+1}. {user_info} - <i>Причина: {reason}</i>")
            
        if failed_count > 10:
            report_lines.append(f"\n<i>... и еще {failed_count - 10} пользователей.</i>")

    report_text = "\n".join(report_lines)

    # Отправляем отчет в админский чат
    await bot.send_message(
        chat_id=settings.ADMIN_CHAT_ID,
        text=report_text,
        parse_mode="HTML"
    )

async def send_photo_to_user(db: Session, user: User, photo_id: str, caption: str) -> bool:
    """Безопасно отправляет фото пользователю."""
    if not user.bot_accessible:
        return False
        
    try:
        await bot.send_photo(
            chat_id=user.telegram_id, 
            photo=photo_id, 
            caption=caption,
            parse_mode="HTML"
        )
        return True
    except TelegramForbiddenError:
        user.bot_accessible = False
        db.add(user)
        db.commit()
        return False
    except Exception as e:
        logger.error(f"Failed to send photo to user {user.id}: {e}")
        return False
    
async def send_points_expired_notification(db: Session, user: User, points_expired: int):
    """Уведомление о сгорании бонусных баллов."""
    message = (
        f"🔥 К сожалению, срок действия ваших бонусных баллов истек.\n\n"
        f"Списано: <b>{points_expired} баллов</b>.\n\n"
        f"Совершайте покупки, чтобы накопить новые!"
    )
    await _send_message(db, user, message)

async def send_points_expiring_soon_notification(db: Session, user: User, points_expiring: int, days_left: int):
    """Уведомление о скором сгорании баллов."""
    # Выбираем правильное склонение для слова "день"
    day_word = "дней"
    if days_left == 1:
        day_word = "день"
    elif 1 < days_left < 5:
        day_word = "дня"

    message = (
        f"⏳ <b>Напоминание!</b>\n\n"
        f"Через <b>{days_left} {day_word}</b> с вашего бонусного счета сгорит <b>{points_expiring} баллов</b>.\n\n"
        f"Успейте потратить их на приятные покупки! 🎁"
    )
    # Можно добавить кнопку, ведущую в магазин
    # builder = InlineKeyboardBuilder()
    # builder.button(text="🛍️ Потратить баллы", web_app=...)
    
    await _send_message(db, user, message)


async def send_promo_notification(
    db: Session,
    user: User,
    title: str,
    text: str,
    image_url: str | None,
    action_url: str | None
):
    """
    Отправляет пользователю промо-уведомление (акцию).
    Поддерживает отправку с картинкой и кнопкой-ссылкой в Mini App.
    """
    if not user.bot_accessible:
        print(f"Skipping promo for user {user.id}: bot is marked as inaccessible.")
        return

    # --- 1. Формируем текст сообщения ---
    # Объединяем заголовок и основной текст
    full_text = f"<b>{title}</b>\n\n{text}"
    
    # Обрезаем текст, если он слишком длинный для подписи к фото (лимит Telegram 1024 символа)
    if image_url and len(full_text) > 1024:
        full_text = full_text[:1020] + "..."

    # --- 2. Формируем инлайн-кнопку, если есть URL ---
    reply_markup = None
    if action_url:
        builder = InlineKeyboardBuilder()
        # Собираем полный URL для Mini App
        full_action_url = f"{settings.MINI_APP_URL}{action_url}" if action_url.startswith('/') else action_url
        
        builder.button(
            text="✨ Участвовать!",
            web_app=WebAppInfo(url=full_action_url)
        )
        reply_markup = builder.as_markup()

    # --- 3. Отправляем сообщение ---
    try:
        if image_url:
            # Если есть картинка, отправляем как фото с подписью
            await bot.send_photo(
                chat_id=user.telegram_id,
                photo=image_url,
                caption=full_text,
                reply_markup=reply_markup
            )
        else:
            # Если картинки нет, отправляем как простое сообщение
            await bot.send_message(
                chat_id=user.telegram_id,
                text=full_text,
                reply_markup=reply_markup,
                disable_web_page_preview=True # Отключаем превью ссылок в тексте
            )
    except TelegramForbiddenError:
        print(f"User {user.id} has blocked the bot while sending promo. Updating status.")
        user.bot_accessible = False
        db.add(user)
        db.commit()
    except Exception as e:
        print(f"Failed to send promo notification to user {user.id}: {e}")


async def send_error_to_super_admins(error_message: str):
    """
    Отправляет сообщение о критической ошибке всем супер-админам в личные сообщения.
    """
    if not settings.SUPER_ADMIN_IDS:
        logger.warning("SUPER_ADMIN_IDS is not set. Critical error cannot be sent.")
        return

    # Используем asyncio.gather для параллельной отправки всем суперадминам
    tasks = []
    for admin_id in settings.SUPER_ADMIN_IDS:
        try:
            # Обрезаем сообщение, если оно слишком длинное (лимит Telegram 4096 символов)
            if len(error_message) > 4096:
                error_message = error_message[:4090] + "\n[...]"
            
            task = bot.send_message(
                chat_id=admin_id,
                text=error_message,
                parse_mode="HTML"
            )
            tasks.append(task)
        except Exception as e:
            logger.error(f"Failed to create send_message task for super admin {admin_id}: {e}")

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True) # return_exceptions=True, чтобы не упасть, если один из админов заблокировал бота

async def send_birthday_greeting(db: Session, user: User, points_added: int):
    """Поздравляет пользователя с Днем Рождения."""
    message = (
        f"🎉 <b>С Днем Рождения, {user.first_name or 'дорогой друг'}!</b>\n\n"
        f"Поздравляем вас с праздником! В этот особенный день мы хотим сделать вам подарок и начисляем "
        f"<b>{points_added} бонусных баллов</b> на ваш счет.\n\n"
        f"Желаем вам всего наилучшего и ждем в нашем магазине! 🥳"
    )
    await _send_message(db, user, message)

async def send_manual_points_update(db: Session, user: User, points_adjusted: int, comment: str):
    """Уведомление о ручном изменении баланса администратором."""
    if points_adjusted > 0:
        action_text = f"✅ Вам начислено <b>{points_adjusted} бонусных баллов</b>."
    else:
        # Убираем минус для красивого отображения
        action_text = f"❌ С вашего счета списано <b>{-points_adjusted} бонусных баллов</b>."
    
    comment_text = f"<i>Комментарий администратора: {comment}</i>" if comment else ""
    
    message = f"{action_text}\n{comment_text}".strip()
    await _send_message(db, user, message)