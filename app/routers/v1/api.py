# app/routers/v1/api.py

from fastapi import APIRouter

from app.routers.v1.endpoints import auth, user, catalog, cart, order, settings, coupon, notification, cms
from app.routers.v1.endpoints import admin as admin_v1_router

# Создаем главный роутер для API версии v1
# Все пути, подключенные к нему, будут иметь префикс /api/v1
api_router = APIRouter(prefix="/v1")

# Пользовательские и публичные эндпоинты
api_router.include_router(auth.router, tags=["Authentication"])
api_router.include_router(user.router, tags=["Users"])
api_router.include_router(catalog.router, tags=["Catalog"])
api_router.include_router(cart.router, tags=["Cart & Favorites"])
api_router.include_router(order.router, tags=["Orders"])
api_router.include_router(settings.router, tags=["Settings"])
api_router.include_router(coupon.router, tags=["Coupons"])
api_router.include_router(notification.router, tags=["Notifications"])
api_router.include_router(cms.router, tags=["CMS"])

# Админские эндпоинты
api_router.include_router(admin_v1_router.router, prefix="/admin", tags=["Admin"])