# app/routers/v1/api.py

from fastapi import APIRouter

from app.routers.v1.endpoints import auth, user, catalog, cart, order, settings, coupon, notification, cms
from app.routers.v1.endpoints import admin as admin_v1_router

# Создаем главный роутер для API версии v1
# Все пути, подключенные к нему, будут иметь префикс /api/v1
api_router = APIRouter(prefix="/v1")

# Пользовательские и публичные эндпоинты
api_router.include_router(auth.router, tags=["Authentication V_1"])
api_router.include_router(user.router, tags=["Users V_1"])
api_router.include_router(catalog.router, tags=["Catalog V_1"])
api_router.include_router(cart.router, tags=["Cart & Favorites V_1"])
api_router.include_router(order.router, tags=["Orders V_1"])
api_router.include_router(settings.router, tags=["Settings V_1"])
api_router.include_router(coupon.router, tags=["Coupons V_1"])
api_router.include_router(notification.router, tags=["Notifications V_1"])
api_router.include_router(cms.router, tags=["CMS V_1"])

# Админские эндпоинты
api_router.include_router(admin_v1_router.router, prefix="/admin", tags=["Admin V_1"])