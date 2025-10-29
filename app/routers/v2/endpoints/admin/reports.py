# app/routers/v1/admin/reports.py

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.admin import PaginatedAdminProducts # <- Эту схему мы сейчас создадим
from app.services import reports as reports_service # <- Этот сервис мы сейчас создадим

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/low-stock", response_model=PaginatedAdminProducts)
async def get_low_stock_report(
    threshold: int = Query(5, ge=1, description="Порог остатка, ниже которого товар попадает в отчет")
):
    """
    [АДМИН] Получает список товаров, остаток которых на складе ниже заданного порога.
    """
    return await reports_service.get_low_stock_products(threshold=threshold)