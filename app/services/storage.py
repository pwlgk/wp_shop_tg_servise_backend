# app/services/storage.py
from datetime import datetime, timedelta, timezone
import io
import boto3
from botocore.client import Config
from fastapi import UploadFile, HTTPException # Добавляем HTTPException
from app.core.config import settings
import uuid
import logging # Добавляем logging
from sqlalchemy.orm import Session
from app.models.broadcast import Broadcast
from app.models.dialogue import DialogueMessage

logger = logging.getLogger(__name__)

# Инициализация S3 клиента (остается без изменений)
s3_client = boto3.client(
    's3',
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY,
    aws_secret_access_key=settings.S3_SECRET_KEY,
    config=Config(signature_version='s3')
)

async def upload_file_to_s3(file: UploadFile, bucket_name: str) -> str:
    """
    Загружает файл в S3-совместимое хранилище и возвращает публичный URL.
    """
    # Проверка, что файл вообще был передан
    if not file:
        raise HTTPException(status_code=400, detail="No file provided for upload.")

    # Генерируем уникальное имя файла, чтобы избежать коллизий
    try:
        file_extension = file.filename.split('.')[-1]
    except IndexError:
        file_extension = "jpg" # Запасной вариант
        
    unique_filename = f"broadcasts/{uuid.uuid4()}.{file_extension}"

    try:
        # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ ---
        # Перемещаем курсор в начало файла перед чтением.
        # Это необходимо, так как FastAPI мог уже прочитать файл.
        await file.seek(0)
        # -----------------------------

        s3_client.upload_fileobj(
            file.file,
            bucket_name,
            unique_filename,
            ExtraArgs={
                'ContentType': file.content_type,
                'ACL': 'public-read' # Делаем файл публично доступным
            }
        )
    except Exception as e:
        logger.error(f"Failed to upload file to S3 bucket {bucket_name}", exc_info=True)
        # Перебрасываем ошибку наверх, чтобы API вернул корректный 500 статус
        raise e

    # Формируем публичный URL
    public_url = f"{settings.S3_ENDPOINT_URL}/{bucket_name}/{unique_filename}"
    logger.info(f"File '{file.filename}' successfully uploaded to {public_url}")
    return public_url



async def upload_file_object_to_s3(
    file_obj: io.BytesIO, 
    file_name: str,
    content_type: str,
    bucket_name: str
) -> str:
    """
    Загружает файлоподобный объект (file-like object) в S3 и возвращает URL.
    """
    # 1. "Перематываем" файловый объект в начало, на всякий случай
    file_obj.seek(0)

    # 2. Генерируем уникальное имя
    try:
        file_extension = file_name.split('.')[-1]
    except IndexError:
        file_extension = "bin" # "bin" для бинарных данных без расширения
        
    unique_filename = f"dialogues/{uuid.uuid4()}.{file_extension}"

    # 3. Загружаем объект в S3
    try:
        s3_client.upload_fileobj(
            file_obj, # <--- Передаем сам объект
            bucket_name,
            unique_filename,
            ExtraArgs={
                'ContentType': content_type,
                'ACL': 'public-read'
            }
        )
    except Exception as e:
        logger.error(f"Failed to upload file object to S3 bucket {bucket_name}", exc_info=True)
        raise e

    # 4. Формируем и возвращаем URL
    public_url = f"{settings.S3_ENDPOINT_URL}/{bucket_name}/{unique_filename}"
    logger.info(f"File object '{file_name}' successfully uploaded to {public_url}")
    return public_url



async def cleanup_s3_storage_task(db: Session, older_than_days: int, admin_user_id: int):
    """
    Фоновая задача для удаления старых медиафайлов из S3, на которые есть
    ссылки в базе данных.
    """
    logger.info(f"Starting S3 cleanup task for files older than {older_than_days} days, initiated by admin {admin_user_id}.")
    
    # 1. Определяем пороговую дату
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    
    # 2. Собираем URL'ы из рассылок (broadcasts)
    old_broadcasts_media = db.query(Broadcast.image_url).filter(
        Broadcast.image_url.isnot(None),
        Broadcast.created_at < cutoff_date
    ).all()
    
    # 3. Собираем URL'ы из сообщений диалогов
    old_dialogue_media = db.query(DialogueMessage.media_url).filter(
        DialogueMessage.media_url.isnot(None),
        DialogueMessage.created_at < cutoff_date
    ).all()

    all_urls = [url for (url,) in old_broadcasts_media] + [url for (url,) in old_dialogue_media]
    
    if not all_urls:
        logger.info("No old media files found in the database to delete.")
        # Отправляем отчет админу
        from app.bot.core import bot
        await bot.send_message(admin_user_id, f"✅ Задача очистки хранилища завершена.\n\nФайлы старше {older_than_days} дней не найдены.")
        return

    # 4. Извлекаем ключи объектов из URL'ов
    keys_to_delete = []
    base_path = f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET_NAME}/"
    for url in all_urls:
        if url.startswith(base_path):
            key = url.replace(base_path, "")
            keys_to_delete.append({"Key": key})

    if not keys_to_delete:
        logger.info("Found old media URLs, but could not parse any valid keys for deletion.")
        return

    # 5. Массовое удаление объектов из S3 (порциями по 1000)
    deleted_count = 0
    failed_count = 0
    
    try:
        # boto3 позволяет удалять до 1000 ключей за один запрос
        for i in range(0, len(keys_to_delete), 1000):
            chunk = keys_to_delete[i:i + 1000]
            response = s3_client.delete_objects(
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

    # 6. Отправка отчета администратору
    report_text = (
        f"✅ Задача очистки хранилища завершена.\n\n"
        f"Удалено файлов (старше {older_than_days} дней): {deleted_count}\n"
        f"Ошибок при удалении: {failed_count}"
    )
    from app.bot.core import bot
    await bot.send_message(admin_user_id, report_text)