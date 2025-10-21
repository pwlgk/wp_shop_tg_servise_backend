# app/routers/v1/media.py (или v2/endpoints/media.py)

import httpx
import logging # <-- Добавляем импорт logging
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from typing import List
from pydantic import BaseModel, HttpUrl

from app.dependencies import get_current_user
from app.clients.woocommerce import wc_client
from app.models.user import User

router = APIRouter(prefix="/media", tags=["Media"])

class MediaUploadResponse(BaseModel):
    id: int
    source_url: HttpUrl

@router.post("/upload", response_model=List[MediaUploadResponse], status_code=status.HTTP_201_CREATED)
async def upload_media_files(
    files: List[UploadFile] = File(..., description="Один или несколько файлов для загрузки"),
    current_user: User = Depends(get_current_user)
):
    """
    Загружает одно или несколько изображений в медиатеку WordPress.
    Возвращает список с ID и URL каждого загруженного файла.
    """
    uploaded_files_info = []
    
    # --- НАЧАЛО ИСПРАВЛЕНИЯ: УВЕЛИЧИВАЕМ ТАЙМАУТ ---
    # Создаем кастомный, более долгий таймаут специально для этой операции.
    # 60 секунд должно быть достаточно для загрузки и обработки большинства изображений.
    upload_timeout = httpx.Timeout(60.0)
    # -----------------------------------------------
    
    for file in files:
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Файл '{file.filename}' не является изображением.")
        
        try:
            contents = await file.read()
            
            headers = {
                'Content-Disposition': f'attachment; filename="{file.filename}"',
                'Content-Type': file.content_type
            }
            
            # --- ИСПОЛЬЗУЕМ КАСТОМНЫЙ ТАЙМАУТ ---
            # Передаем наш новый таймаут в метод post
            response = await wc_client.async_client.post(
                "wp/v2/media",
                content=contents,
                headers=headers,
                timeout=upload_timeout # <--- Вот оно!
            )
            response.raise_for_status()
            
            response_data = response.json()
            uploaded_files_info.append({
                "id": response_data["id"],
                "source_url": response_data["source_url"]
            })

        except httpx.ReadTimeout:
            # --- ЯВНАЯ ОБРАБОТКА ТАЙМАУТА ---
            error_msg = f"Сервер не успел обработать файл '{file.filename}' за {upload_timeout.read} секунд. Попробуйте загрузить файл меньшего размера."
            logging.warning(error_msg)
            raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail=error_msg)
            
        except httpx.HTTPStatusError as e:
            error_detail = f"Ошибка загрузки файла '{file.filename}' в WordPress. Ответ сервера: {e.response.text}"
            logging.error(error_detail)
            raise HTTPException(status_code=e.response.status_code, detail=error_detail)
        except Exception as e:
            # Используем имя логгера для правильной записи
            logging.getLogger(__name__).error(f"Внутренняя ошибка при обработке файла '{file.filename}'.", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Внутренняя ошибка при обработке файла '{file.filename}'.")
            
    return uploaded_files_info