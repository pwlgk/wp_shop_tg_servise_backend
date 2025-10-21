# app/services/channel_publisher.py

import json
import logging
import asyncio
from typing import List
from sqlalchemy.orm import Session
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InlineKeyboardButton

from app.bot.core import bot
from app.core.config import settings
from app.schemas.admin import ChannelPostCreate
from app.models.channel_post import ChannelPost

logger = logging.getLogger(__name__)

async def publish_post_to_channel(
    db: Session, 
    post_data: ChannelPostCreate,
    media_urls: List[str] = None
) -> ChannelPost:
    """
    Формирует и отправляет пост в канал, поддерживая медиагруппы.
    Корректно формирует URL-кнопки для прямого запуска Mini App из канала.
    """
    media_urls = media_urls or []
    
    # 1. Формирование подписи
    full_text = ""
    if post_data.title:
        safe_title = post_data.title.replace('<', '&lt;').replace('>', '&gt;')
        full_text += f"<b>{safe_title}</b>\n\n"
    full_text += post_data.message_text

    caption_text = full_text
    if media_urls and len(caption_text) > 1024:
        caption_text = caption_text[:1020] + "..."
        
    if len(full_text) > 4096:
        full_text = full_text[:4092] + "..."

    # 2. Формирование клавиатуры (ФИНАЛЬНАЯ ВЕРСИЯ V2)
    reply_markup = None
    if post_data.button:
        builder = InlineKeyboardBuilder()
        button = post_data.button
        final_url = button.url
        
        # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ ---
        if button.type == "web_app":
            # Генерируем специальную ссылку для прямого запуска Mini App.
            # Формат: https://t.me/имя_бота?startapp=параметр
            bot_username = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
            
            # В `startapp` не должно быть ведущего слэша.
            # И он должен быть закодирован для URL.
            start_param = final_url.lstrip('/')
            
            final_url = f"https://t.me/{bot_username}?startapp={start_param}"
        
        # Для типа 'external' URL остается без изменений.
        
        # Создаем ОБЫЧНУЮ URL-кнопку. Telegram сам поймет, что делать со ссылкой.
        builder.row(InlineKeyboardButton(text=button.text, url=final_url))
        reply_markup = builder.as_markup()
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        
    sent_messages: List[Message] = []
    error_message: str | None = None
    
    # 3. Отправка сообщения
    try:
        if not media_urls:
            # Сценарий 1: Текстовый пост
            msg = await bot.send_message(
                chat_id=settings.TELEGRAM_CHANNEL_ID,
                text=full_text,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            sent_messages.append(msg)

        elif len(media_urls) == 1:
            # Сценарий 2: Один медиафайл
            media_url = media_urls[0]
            if any(ext in media_url.lower() for ext in ['.mp4', '.mov', '.avi']):
                msg = await bot.send_video(
                    chat_id=settings.TELEGRAM_CHANNEL_ID, video=media_url,
                    caption=caption_text, reply_markup=reply_markup
                )
            else:
                msg = await bot.send_photo(
                    chat_id=settings.TELEGRAM_CHANNEL_ID, photo=media_url,
                    caption=caption_text, reply_markup=reply_markup
                )
            sent_messages.append(msg)
            
        else:
            # Сценарий 3: Медиагруппа
            media_group = []
            for i, url in enumerate(media_urls[:10]):
                current_caption = caption_text if i == 0 else None 
                if any(ext in url.lower() for ext in ['.mp4', '.mov', '.avi']):
                    media_group.append(InputMediaVideo(media=url, caption=current_caption))
                else:
                    media_group.append(InputMediaPhoto(media=url, caption=current_caption))
            
            sent_messages = await bot.send_media_group(
                chat_id=settings.TELEGRAM_CHANNEL_ID,
                media=media_group
            )
            
            if reply_markup:
                button_promo_text = "Подробности по кнопке ниже 👇"
                button_message = await bot.send_message(
                    chat_id=settings.TELEGRAM_CHANNEL_ID,
                    text=button_promo_text,
                    reply_markup=reply_markup
                )
                sent_messages.append(button_message)

    except Exception as e:
        logger.error("Failed to send message to channel.", exc_info=True)
        error_message = str(e)

    # 4. Сохранение результата в БД
    db_post = ChannelPost(
        title=post_data.title,
        message_text=post_data.message_text,
        media_urls_json=json.dumps(media_urls) if media_urls else None,
        button_json=post_data.button.model_dump_json() if post_data.button else None,
        channel_message_ids_json=json.dumps([msg.message_id for msg in sent_messages]) if sent_messages else None,
        status="published" if sent_messages else "failed"
    )
    db.add(db_post)
    db.commit()
    db.refresh(db_post)

    if error_message:
        raise Exception(f"Failed to publish to channel: {error_message}")

    return db_post


async def delete_post_from_channel(db: Session, post_id: int) -> bool:
    """
    Удаляет все сообщения, связанные с постом, из канала и обновляет статус в БД.
    """
    post = db.get(ChannelPost, post_id)
    if not post:
        return False
    
    if post.channel_message_ids_json and post.status == "published":
        try:
            message_ids_to_delete = json.loads(post.channel_message_ids_json)
            
            tasks = [
                bot.delete_message(chat_id=settings.TELEGRAM_CHANNEL_ID, message_id=msg_id)
                for msg_id in message_ids_to_delete
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            logger.info(f"Retracted {len(message_ids_to_delete)} messages for post {post_id}.")
        except Exception as e:
            logger.warning(
                f"Could not delete one or more messages for post {post.id}. "
                f"They might have been deleted manually. Error: {e}"
            )
    
    post.status = "retracted"
    db.commit()
    return True