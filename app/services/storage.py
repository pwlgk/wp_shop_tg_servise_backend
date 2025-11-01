# app/services/storage.py

import logging
import uuid
import os
from pathlib import Path

from fastapi import UploadFile, HTTPException, status
import aiofiles

logger = logging.getLogger(__name__)

# Определяем путь к временной директории внутри контейнера/проекта
TEMP_MEDIA_DIR = Path("/app/temp_media")

# Создаем директорию при старте, если ее нет
TEMP_MEDIA_DIR.mkdir(exist_ok=True)


async def save_temp_file(file: UploadFile) -> str:
    """
    Сохраняет загруженный файл во временную локальную директорию
    и возвращает полный путь к нему.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл для загрузки не предоставлен.")

    try:
        # Генерируем безопасное, уникальное имя файла
        file_extension = Path(file.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = TEMP_MEDIA_DIR / unique_filename

        # Асинхронно записываем файл на диск
        async with aiofiles.open(file_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024):  # Читаем по 1MB
                await out_file.write(content)
        
        logger.info(f"Temporarily saved file '{file.filename}' to '{file_path}'.")
        return str(file_path)

    except Exception as e:
        logger.error(f"Failed to save temporary file '{file.filename}'.", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка при сохранении временного файла.")


async def cleanup_temp_file(file_path: str):
    """
    Безопасно удаляет временный файл.
    """
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temporary file: '{file_path}'.")
    except Exception as e:
        logger.error(f"Failed to clean up temporary file '{file_path}'.", exc_info=True)
        # Не выбрасываем исключение, так как это фоновая задача очистки