# app/services/storage.py

import logging
import asyncio
import io
from datetime import datetime, timedelta, timezone

import aioboto3
from botocore.exceptions import ClientError
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.broadcast import Broadcast
from app.models.dialogue import DialogueMessage
import uuid

logger = logging.getLogger(__name__)

# Мы больше не создаем глобальный синхронный клиент.
# Вместо этого мы будем создавать асинхронные клиенты по мере необходимости.

async def upload_file_to_s3(file: UploadFile, object_name_prefix: str) -> str:
    """
    Асинхронно загружает файл в S3-совместимое хранилище и возвращает публичный URL.
    
    Args:
        file: Объект UploadFile от FastAPI.
        object_name_prefix: Префикс (папка) для файла в бакете, например, "broadcasts".
    
    Returns:
        Публичный URL загруженного файла.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided for upload.")

    try:
        file_extension = file.filename.split('.')[-1]
    except IndexError:
        file_extension = "jpg"
        
    unique_filename = f"{object_name_prefix}/{uuid.uuid4()}.{file_extension}"

    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION
    ) as s3_client:
        logger.info(f"Starting upload of '{file.filename}' to S3 as '{unique_filename}'.")
        
        try:
            # Перематываем файл в начало, чтобы избежать ошибок SHA256Mismatch
            await file.seek(0)
            
            # Асинхронно загружаем файл
            await s3_client.upload_fileobj(
                file.file,
                settings.S3_BUCKET_NAME,
                unique_filename,
                ExtraArgs={'ContentType': file.content_type, 'ACL': 'public-read'}
            )

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            logger.error(f"An S3 client error occurred during upload: {error_code}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ошибка при загрузке файла в хранилище.")
        except Exception:
            logger.error(f"An unexpected error occurred during S3 upload", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка сервера при загрузке файла.")

    public_url = f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET_NAME}/{unique_filename}"
    logger.info(f"File '{file.filename}' uploaded successfully. URL: {public_url}")
    return public_url


async def upload_file_object_to_s3(
    file_obj: io.BytesIO, 
    file_name: str,
    content_type: str,
    object_name_prefix: str
) -> str:
    """
    Асинхронно загружает файлоподобный объект (bytes) в S3 и возвращает URL.
    """
    file_obj.seek(0)

    try:
        file_extension = file_name.split('.')[-1]
    except IndexError:
        file_extension = "bin"
        
    unique_filename = f"{object_name_prefix}/{uuid.uuid4()}.{file_extension}"

    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION
    ) as s3_client:
        logger.info(f"Starting upload of file object '{file_name}' to S3 as '{unique_filename}'.")
        
        try:
            await s3_client.upload_fileobj(
                file_obj,
                settings.S3_BUCKET_NAME,
                unique_filename,
                ExtraArgs={'ContentType': content_type, 'ACL': 'public-read'}
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            logger.error(f"An S3 client error occurred during file object upload: {error_code}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ошибка при загрузке объекта в хранилище.")
        except Exception:
            logger.error(f"An unexpected error occurred during S3 file object upload", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка сервера при загрузке объекта.")

    public_url = f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET_NAME}/{unique_filename}"
    logger.info(f"File object '{file_name}' uploaded successfully. URL: {public_url}")
    return public_url


async def cleanup_s3_storage_task(db: Session, older_than_days: int, admin_user_id: int):
    """
    Асинхронная фоновая задача для удаления старых медиафайлов из S3.
    """
    logger.info(f"Starting S3 cleanup task for files older than {older_than_days} days, initiated by admin {admin_user_id}.")
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    
    # Собираем URL'ы (синхронная операция с БД)
    old_broadcasts_media = db.query(Broadcast.image_url).filter(
        Broadcast.image_url.isnot(None), Broadcast.created_at < cutoff_date
    ).all()
    old_dialogue_media = db.query(DialogueMessage.media_url).filter(
        DialogueMessage.media_url.isnot(None), DialogueMessage.created_at < cutoff_date
    ).all()

    all_urls = [url for (url,) in old_broadcasts_media] + [url for (url,) in old_dialogue_media]
    
    if not all_urls:
        logger.info("No old media files found in the database to delete.")
        from app.bot.core import bot
        await bot.send_message(admin_user_id, f"✅ Задача очистки хранилища завершена.\n\nФайлы старше {older_than_days} дней не найдены.")
        return

    keys_to_delete = []
    base_path = f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET_NAME}/"
    for url in all_urls:
        if url.startswith(base_path):
            key = url.replace(base_path, "")
            keys_to_delete.append({"Key": key})

    if not keys_to_delete:
        logger.info("Found old media URLs, but could not parse any valid keys for deletion.")
        return

    deleted_count = 0
    failed_count = 0
    
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION
    ) as s3_client:
        try:
            # Разбиваем на порции по 1000 и удаляем асинхронно
            for i in range(0, len(keys_to_delete), 1000):
                chunk = keys_to_delete[i:i + 1000]
                response = await s3_client.delete_objects(
                    Bucket=settings.S3_BUCKET_NAME,
                    Delete={'Objects': chunk}
                )
                
                if 'Deleted' in response:
                    deleted_count += len(response['Deleted'])
                if 'Errors' in response:
                    failed_count += len(response['Errors'])
                    for error in response['Errors']:
                        logger.error(f"S3 Deletion Error: Code={error['Code']}, Key={error['Key']}, Message={error['Message']}")
            
            logger.info(f"S3 cleanup finished. Successfully deleted: {deleted_count}, Failed: {failed_count}.")

        except Exception as e:
            logger.error("A critical error occurred during S3 delete_objects call.", exc_info=True)
            from app.bot.core import bot
            await bot.send_message(admin_user_id, f"❌ Ошибка во время очистки хранилища: {e}")
            return

    report_text = (
        f"✅ Задача очистки хранилища завершена.\n\n"
        f"Удалено файлов (старше {older_than_days} дней): {deleted_count}\n"
        f"Ошибок при удалении: {failed_count}"
    )
    from app.bot.core import bot
    await bot.send_message(admin_user_id, report_text)