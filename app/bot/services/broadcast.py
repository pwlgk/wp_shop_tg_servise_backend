# app/bot/services/broadcast.py

import asyncio
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, Message, FSInputFile

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
    Поддерживает отложенный старт, локальные фото, кнопки и сохраняет message_id.
    """
    db = SessionLocal()
    try:
        broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
        if not broadcast or broadcast.status not in ["pending", "scheduled"]:
            logger.warning(f"Broadcast {broadcast_id} not found or has invalid status '{broadcast.status}'. Aborting.")
            return

        # 1. Проверка отложенного запуска
        if broadcast.scheduled_at and broadcast.scheduled_at > datetime.now(timezone.utc):
            broadcast.status = "scheduled"
            db.commit()
            sleep_duration = (broadcast.scheduled_at - datetime.now(timezone.utc)).total_seconds()
            logger.info(f"Broadcast {broadcast_id} is scheduled for {broadcast.scheduled_at}. Sleeping for {sleep_duration:.2f} seconds.")
            await asyncio.sleep(sleep_duration)
            
            # Перечитываем объект из БД, чтобы проверить, не был ли он отменен за время сна
            broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
            if not broadcast or broadcast.status != "scheduled":
                logger.warning(f"Broadcast {broadcast_id} was cancelled or modified while scheduled. Aborting.")
                return

        logger.info(f"Starting broadcast {broadcast_id}...")
        broadcast.status = "processing"
        broadcast.started_at = datetime.now(timezone.utc)
        db.commit()

        # 2. Формирование клавиатуры (если нужна)
        reply_markup = None
        if broadcast.button_text and broadcast.button_url:
            builder = InlineKeyboardBuilder()
            final_url = broadcast.button_url
            
            if final_url.startswith('/'):
                base_url = settings.MINI_APP_URL.rstrip('/')
                final_url = f"{base_url}{final_url}"

            if final_url.startswith("https://"):
                try:
                    builder.button(text=broadcast.button_text, web_app=WebAppInfo(url=final_url))
                    reply_markup = builder.as_markup()
                except Exception as e:
                    logger.error(f"Failed to create WebAppInfo for broadcast {broadcast.id} with URL '{final_url}'. Error: {e}")
            else:
                logger.warning(f"Skipping button for broadcast {broadcast.id} because URL '{final_url}' is not a valid HTTPS link.")

        # 3. Получаем список пользователей для рассылки
        query = db.query(User).filter(User.is_blocked == False)
        if broadcast.target_level and broadcast.target_level != "all":
            query = query.filter(User.level == broadcast.target_level)
        users_to_send = query.all()
        
        # 4. Итерация и отправка сообщений
        sent_count = 0
        failed_users_info = []
        
        for user in users_to_send:
            if not user.bot_accessible:
                failed_users_info.append({"user": user, "reason": "Bot marked as inaccessible"})
                continue
                
            sent_message = None
            reason = None
            try:
                # Проверяем, есть ли локальный путь к изображению
                if broadcast.image_url:
                    photo_input = FSInputFile(broadcast.image_url)
                    sent_message = await bot.send_photo(
                        chat_id=user.telegram_id, 
                        photo=photo_input,
                        caption=broadcast.message_text, 
                        reply_markup=reply_markup
                    )
                else:
                    sent_message = await bot.send_message(
                        chat_id=user.telegram_id, 
                        text=broadcast.message_text,
                        reply_markup=reply_markup, 
                        disable_web_page_preview=True
                    )
            except TelegramForbiddenError:
                reason = "User has blocked the bot"
                user.bot_accessible = False
                db.commit() # Немедленно сохраняем изменение статуса
            except Exception as e:
                reason = str(e)
                logger.error(f"Failed to send message to user {user.id} in broadcast {broadcast.id}: {reason}")

            if sent_message:
                sent_count += 1
                # Сохраняем ID сообщения для возможности отзыва
                recipient_record = BroadcastRecipient(
                    broadcast_id=broadcast.id,
                    user_id=user.id,
                    message_id=sent_message.message_id
                )
                db.add(recipient_record)
            else:
                failed_users_info.append({"user": user, "reason": reason})
            
            await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

        # 5. Обновляем статистику и статус рассылки
        broadcast.status = "completed"
        broadcast.sent_count = sent_count
        broadcast.failed_count = len(failed_users_info)
        broadcast.finished_at = datetime.now(timezone.utc)
        db.commit()
        
        logger.info(f"Broadcast {broadcast_id} completed. Sent: {sent_count}, Failed: {len(failed_users_info)}")
        
        # 6. Отправляем отчет администратору
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
        # Получаем все записи о получателях для данной рассылки
        recipients = db.query(BroadcastRecipient).filter(BroadcastRecipient.broadcast_id == broadcast_id).all()
        if not recipients:
            logger.warning(f"No recipients found for broadcast {broadcast_id} to retract.")
            return

        deleted_count = 0
        tasks = []
        for recipient in recipients:
            # Создаем асинхронные задачи на удаление
            tasks.append(
                bot.delete_message(chat_id=recipient.user.telegram_id, message_id=recipient.message_id)
            )

        # Выполняем все задачи на удаление конкурентно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if not isinstance(result, Exception):
                deleted_count += 1
            elif isinstance(result, TelegramBadRequest) and "message to delete not found" in str(result):
                # Это не ошибка, просто сообщение уже было удалено
                deleted_count += 1
            else:
                logger.warning(f"Could not delete a broadcast message: {result}")
        
        logger.info(f"Retraction for broadcast {broadcast_id} completed. Deleted/processed {deleted_count} messages.")

    finally:
        db.close()