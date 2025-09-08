# app/bot/services/broadcast.py

import asyncio
from datetime import datetime
from sqlalchemy.orm import Session
from aiogram.exceptions import TelegramForbiddenError

from app.db.session import SessionLocal
from app.models.broadcast import Broadcast
from app.models.user import User
from app.bot.core import bot
# Импортируем сервис уведомлений, чтобы отправить отчет
from app.bot.services import notification as notification_service
import logging

logger = logging.getLogger(__name__)
# Пауза между отправкой сообщений, чтобы не превысить лимиты Telegram
BROADCAST_SLEEP_SECONDS = 0.1 # 10 сообщений в секунду

async def process_broadcast(broadcast_id: int):
    """
    Основная функция, выполняющая рассылку.
    Получает пользователей из БД, отправляет им сообщения (текст или фото)
    и формирует отчет для администратора.
    """
    db: Session = SessionLocal()
    try:
        broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
        if not broadcast or broadcast.status != "pending":
            logger.info(f"Broadcast {broadcast_id} not found or already processed.")
            return

        logger.info(f"Starting broadcast {broadcast_id}...")
        broadcast.status = "processing"
        broadcast.started_at = datetime.utcnow()
        db.commit()

        # 1. Получаем список пользователей для рассылки
        query = db.query(User).filter(User.is_blocked == False)
        if broadcast.target_level and broadcast.target_level != "all":
            query = query.filter(User.level == broadcast.target_level)
        
        users_to_send = query.all()
        
        # 2. Итерируемся и отправляем сообщения
        sent_count = 0
        failed_users = []
        
        for user in users_to_send:
            # Проверяем флаг доступности бота перед отправкой
            if not user.bot_accessible:
                failed_users.append({"user": user, "reason": "Bot marked as inaccessible"})
                continue
                
            success = False
            reason = None
            
            try:
                if broadcast.photo_file_id:
                    # Отправляем фото с подписью
                    await bot.send_photo(
                        chat_id=user.telegram_id,
                        photo=broadcast.photo_file_id,
                        caption=broadcast.message_text
                    )
                else:
                    # Отправляем просто текст
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast.message_text
                    )
                success = True
            except TelegramForbiddenError:
                # Пользователь заблокировал бота
                reason = "User has blocked the bot"
                logger.error(f"User {user.id} has blocked the bot. Updating status.")
                user.bot_accessible = False
                db.add(user)
                # Коммитим изменение статуса немедленно
                db.commit()
            except Exception as e:
                # Любая другая ошибка (например, чат не найден)
                reason = str(e)
                logger.error(f"Failed to send message to user {user.id}: {reason}")

            if success:
                sent_count += 1
            else:
                failed_users.append({"user": user, "reason": reason})
            
            # Делаем паузу
            await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

        # 3. Обновляем статистику и статус рассылки
        broadcast.status = "completed"
        broadcast.sent_count = sent_count
        broadcast.failed_count = len(failed_users)
        broadcast.finished_at = datetime.utcnow()
        db.commit()
        
        logger.info(f"Broadcast {broadcast_id} completed. Sent: {sent_count}, Failed: {len(failed_users)}")

        # 4. Отправляем отчет админу
        await notification_service.send_broadcast_report_to_admin(
            broadcast_id=broadcast.id,
            sent_count=sent_count,
            failed_users_info=failed_users
        )

    except Exception as e:
        logger.error(f"Broadcast {broadcast_id} failed catastrophically: {e}")
        if 'broadcast' in locals() and broadcast:
            broadcast.status = "failed"
            db.commit()
    finally:
        db.close()