# app/routers/v1/api.py

from fastapi import APIRouter
from app.routers.v2.endpoints import cart, review, media, auth, user, catalog, order, settings, coupon, notification, cms
from app.routers.v2.endpoints import admin as admin_router_package # Импортируем весь пакет 'admin'

api_router = APIRouter(prefix="/v2")

api_router.include_router(cart.router, tags=["Cart & Favorites V_2"])
api_router.include_router(review.router)
api_router.include_router(media.router)
api_router.include_router(auth.router, tags=["Authentication V_2"])
api_router.include_router(user.router, tags=["Users V_2"])
api_router.include_router(catalog.router, tags=["Catalog V_2"])
api_router.include_router(order.router, tags=["Orders V_2"])
api_router.include_router(settings.router, tags=["Settings V_2"])
# api_router.include_router(coupon.router, tags=["Coupons V_2"])
api_router.include_router(notification.router, tags=["Notifications V_2"])
api_router.include_router(cms.router, tags=["CMS V_2"])



# Подключаем весь админский раздел с префиксом /admin
api_router.include_router(admin_router_package.router, prefix="/admin", tags=["Admin V_2"])