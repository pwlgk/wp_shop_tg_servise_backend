# app/routers/v1/admin/__init__.py

from fastapi import APIRouter
from app.dependencies import get_admin_user
from fastapi import Depends

# 1. Импортируем все наши модули с роутерами из текущего пакета
from . import (
    general,
    users,
    orders,
    communications,
    settings,
    tasks,
    support,
    reports,
    coupons,
)

# 2. Создаем главный роутер для всего админского раздела.
#    - `tags=["Admin"]`: все эндпоинты будут сгруппированы под этим тегом в документации Swagger.
#    - `dependencies=[Depends(get_admin_user)]`: ВАЖНЫЙ ШАГ! Эта зависимость будет автоматически
#      применена ко ВСЕМ эндпоинтам, подключенным к этому роутеру. Это гарантирует, что
#      доступ ко всему API админки будет только у авторизованных администраторов.
router = APIRouter(
    tags=["Admin V_2"],
    dependencies=[Depends(get_admin_user)]
)

# 3. Подключаем роутеры из каждого модуля, добавляя им специфичные префиксы.
#    Порядок подключения не имеет значения.

# Общие эндпоинты (без дополнительного префикса)
# /admin/dashboard, /admin/cache/clear
router.include_router(general.router)

# Эндпоинты для управления пользователями
# /admin/users, /admin/users/{id}, и т.д.
router.include_router(users.router, prefix="/users")

# Эндпоинты для управления заказами
# /admin/orders, /admin/orders/{id}, и т.д.
router.include_router(orders.router, prefix="/orders")

# Эндпоинты для коммуникаций (рассылки и посты в канале)
# /admin/broadcasts, /admin/channel/posts
router.include_router(communications.router)

# Эндпоинты для управления настройками магазина
# /admin/settings
router.include_router(settings.router, prefix="/settings")

# Эндпоинты для управления фоновыми задачами
# /admin/tasks, /admin/tasks/run, и т.д.
router.include_router(tasks.router, prefix="/tasks")

# Эндпоинты для системы поддержки
router.include_router(support.router, prefix="/dialogues")

# Эндпоинты для отчетов
router.include_router(reports.router, prefix="/reports")

# Эндпоинты для управления промокодами
router.include_router(coupons.router, prefix="/coupons")