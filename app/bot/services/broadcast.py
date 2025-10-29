# app/bot/services/broadcast.py

import asyncio
from datetime import datetime, timezone 
from sqlalchemy.orm import Session
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo

from app.db.session import SessionLocal
from app.models.broadcast import Broadcast, BroadcastRecipient
from app.models.user import User
from app.bot.core import bot
from app.bot.services import notification as notification_service
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
BROADCAST_SLEEP_SECONDS = 0.1 # 10 сообщений в секунду

async def process_broadcast(broadcast_id: int):
    """
    Основная функция, выполняющая рассылку.
    Поддерживает отложенный старт, фото, кнопки и сохраняет message_id.
    """
    db = SessionLocal()
    try:
        broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
        if not broadcast or broadcast.status not in ["pending", "scheduled"]:
            logger.warning(f"Broadcast {broadcast_id} not found or has invalid status '{broadcast.status}'. Aborting.")
            return

        # 1. Проверка отложенного запуска
        # --- ИЗМЕНЕНИЕ 2: Заменяем datetime.utcnow() ---
        if broadcast.scheduled_at and broadcast.scheduled_at > datetime.now(timezone.utc):
            broadcast.status = "scheduled"
            db.commit()
            # --- ИЗМЕНЕНИЕ 3: Заменяем datetime.utcnow() ---
            sleep_duration = (broadcast.scheduled_at - datetime.now(timezone.utc)).total_seconds()
            logger.info(f"Broadcast {broadcast_id} is scheduled for {broadcast.scheduled_at}. Sleeping for {sleep_duration:.2f} seconds.")
            await asyncio.sleep(sleep_duration)
            
            broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
            if not broadcast or broadcast.status != "scheduled":
                logger.warning(f"Broadcast {broadcast_id} was cancelled or modified while scheduled. Aborting.")
                return

        logger.info(f"Starting broadcast {broadcast_id}...")
        broadcast.status = "processing"
        # --- ИЗМЕНЕНИЕ 4: Заменяем datetime.utcnow() ---
        broadcast.started_at = datetime.now(timezone.utc)
        db.commit()

        # 2. Формирование клавиатуры (если нужна)
        reply_markup = None
        if broadcast.button_text and broadcast.button_url:
            builder = InlineKeyboardBuilder()
            
            # --- УЛУЧШЕННАЯ ЛОГИКА ФОРМИРОВАНИЯ URL ---
            final_url = broadcast.button_url
            
            # Если ссылка относительная (начинается с /), достраиваем ее
            if final_url.startswith('/'):
                # Убираем возможный слэш в конце, чтобы избежать двойных //
                base_url = settings.MINI_APP_URL.rstrip('/')
                final_url = f"{base_url}{final_url}"

            # Проверяем, что итоговый URL — это валидный HTTPS URL
            if final_url.startswith("https://"):
                try:
                    builder.button(text=broadcast.button_text, web_app=WebAppInfo(url=final_url))
                    reply_markup = builder.as_markup()
                except Exception as e:
                    # Эта ошибка может возникнуть, если Pydantic в aiogram не сможет
                    # провалидировать URL, хотя это маловероятно после нашей проверки.
                    logger.error(
                        f"Failed to create WebAppInfo for broadcast {broadcast.id} "
                        f"with URL '{final_url}'. Error: {e}"
                    )
            else:
                logger.warning(
                    f"Skipping button for broadcast {broadcast.id} because the final URL "
                    f"'{final_url}' is not a valid HTTPS link."
                )

        # 3. Получаем список пользователей
        query = db.query(User).filter(User.is_blocked == False)
        if broadcast.target_level and broadcast.target_level != "all":
            query = query.filter(User.level == broadcast.target_level)
        users_to_send = query.all()
        
        # 4. Итерация и отправка
        sent_count = 0
        failed_users_info = []
        
        for user in users_to_send:
            if not user.bot_accessible:
                failed_users_info.append({"user": user, "reason": "Bot marked as inaccessible"})
                continue
                
            sent_message = None
            reason = None
            try:
                if broadcast.photo_file_id or broadcast.image_url:
                    photo = broadcast.photo_file_id or broadcast.image_url
                    sent_message = await bot.send_photo(
                        chat_id=user.telegram_id, photo=photo,
                        caption=broadcast.message_text, reply_markup=reply_markup
                    )
                else:
                    sent_message = await bot.send_message(
                        chat_id=user.telegram_id, text=broadcast.message_text,
                        reply_markup=reply_markup, disable_web_page_preview=True
                    )
            except TelegramForbiddenError:
                reason = "User has blocked the bot"
                user.bot_accessible = False
                db.commit()
            except Exception as e:
                reason = str(e)
                logger.error(f"Failed to send message to user {user.id} in broadcast {broadcast.id}: {reason}")

            if sent_message:
                sent_count += 1
                # СОХРАНЯЕМ ДАННЫЕ ДЛЯ ОТЗЫВА
                recipient_record = BroadcastRecipient(
                    broadcast_id=broadcast.id,
                    user_id=user.id,
                    message_id=sent_message.message_id
                )
                db.add(recipient_record)
            else:
                failed_users_info.append({"user": user, "reason": reason})
            
            await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

        # 5. Обновляем статистику
        broadcast.status = "completed"
        broadcast.sent_count = sent_count
        broadcast.failed_count = len(failed_users_info)
        broadcast.finished_at = datetime.utcnow()
        db.commit()
        
        logger.info(f"Broadcast {broadcast_id} completed. Sent: {sent_count}, Failed: {len(failed_users_info)}")
        await notification_service.send_broadcast_report_to_admin(broadcast.id, sent_count, failed_users_info)

    except Exception as e:
        logger.error(f"Broadcast {broadcast_id} failed catastrophically.", exc_info=True)
        if 'broadcast' in locals() and broadcast:
            broadcast.status = "failed"
            db.commit()
    finally:
        db.close()


async def retract_broadcast(broadcast_id: int):
    """
    Фоновая задача для отзыва (удаления) сообщений рассылки.
    """
    logger.info(f"Starting retraction for broadcast {broadcast_id}...")
    db = SessionLocal()
    try:
        recipients = db.query(BroadcastRecipient).filter(BroadcastRecipient.broadcast_id == broadcast_id).all()
        if not recipients:
            logger.warning(f"No recipients found for broadcast {broadcast_id} to retract.")
            return

        deleted_count = 0
        for recipient in recipients:
            try:
                await bot.delete_message(chat_id=recipient.user.telegram_id, message_id=recipient.message_id)
                deleted_count += 1
            except TelegramBadRequest as e:
                # Игнорируем ошибки, если сообщение уже удалено или не найдено
                if "message to delete not found" not in str(e):
                    logger.warning(f"Could not delete message {recipient.message_id} for user {recipient.user_id}: {e}")
            except Exception as e:
                logger.warning(f"Could not delete message {recipient.message_id} for user {recipient.user_id}: {e}")
            
            await asyncio.sleep(BROADCAST_SLEEP_SECONDS)
        
        logger.info(f"Retraction for broadcast {broadcast_id} completed. Deleted {deleted_count} messages.")

    finally:
        db.close()