import logging
from fastapi import HTTPException, status
import httpx
from sqlalchemy.orm import Session
from app.clients.woocommerce import wc_client
from app.models.user import User
from app.schemas.loyalty import LoyaltyProgress, UserDashboard
from app.schemas.user import UserProfile, UserUpdate, UserCounters
from app.crud.cart import get_cart_items, get_favorite_items
from app.services import user_levels as user_levels_service # <-- Импортируем
from app.bot.services import notification as notification_service
from app.services import loyalty as loyalty_service
from datetime import date

logger = logging.getLogger(__name__)

async def get_user_profile(db: Session, current_user: User) -> UserProfile:
    """
    Получает полные данные пользователя, объединяя данные из нашей БД,
    WooCommerce и добавляя счетчики корзины/избранного.
    """
    # 1. Запрашиваем данные из WooCommerce
    wc_user_data_response = await wc_client.get(f"wc/v3/customers/{current_user.wordpress_id}")
    wc_user_json = wc_user_data_response.json()
    
    # 2. Получаем счетчики из нашей БД, используя переданную сессию
    cart_items = get_cart_items(db, user_id=current_user.id)
    favorite_items = get_favorite_items(db, user_id=current_user.id)
    
    counters = UserCounters(
        cart_items_count=len(cart_items),
        favorite_items_count=len(favorite_items)
    )

    # 3. Собираем единый профиль. 
    #    Используем `**wc_user_json` для распаковки данных из WooCommerce.
    #    Затем явно добавляем наши кастомные поля.
    profile_data = {
        **wc_user_json, 
        "telegram_id": current_user.telegram_id,
        "username": current_user.username,
        "counters": counters,
        "birth_date": current_user.birth_date, # <-- Добавляем
        "phone": current_user.phone, # <-- Добавляем


    }
    
    # 4. Валидируем и возвращаем результат
    return UserProfile.model_validate(profile_data)

async def update_user_profile(db: Session, current_user: User, user_update_data: UserUpdate) -> UserProfile:
    """
    Обновляет данные профиля пользователя в WooCommerce и синхронизирует
    ФИО и дату рождения с нашей локальной базой данных.
    """
    # 1. Создаем словарь из данных, которые пришли от фронтенда
    update_data_dict = user_update_data.model_dump(exclude_unset=True)
    
    # Если фронтенд не прислал никаких данных для обновления, выходим
    if not update_data_dict:
        return await get_user_profile(db, current_user)
        
    # 2. Преобразуем объект `date` в строку формата YYYY-MM-DD перед отправкой в JSON
    if 'birth_date' in update_data_dict and update_data_dict['birth_date'] is not None:
        # Проверяем, что это действительно объект date, на всякий случай
        if isinstance(update_data_dict['birth_date'], date):
            update_data_dict['birth_date'] = update_data_dict['birth_date'].isoformat()

    try:
        # 3. Отправляем запрос на обновление в WooCommerce
        # `httpx` сам сериализует наш словарь в JSON
        await wc_client.post(
            f"wc/v3/customers/{current_user.wordpress_id}", 
            json=update_data_dict
        )
        logger.info(f"Successfully updated user {current_user.id} profile in WooCommerce.")

    except httpx.HTTPStatusError as e:
        # 4. Обрабатываем возможные ошибки от WooCommerce
        error_message = "Не удалось обновить профиль в магазине."
        if e.response.status_code == 400:
            try:
                error_details = e.response.json()
                error_message = error_details.get("message", "Переданы неверные данные.")
            except Exception:
                pass
        
        logger.error(f"Failed to update WC user {current_user.wordpress_id}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

    # 5. СИНХРОНИЗИРУЕМ НАШУ ЛОКАЛЬНУЮ БД
    # Здесь мы используем исходный объект `user_update_data`, где `birth_date`
    # все еще является объектом `date`, который SQLAlchemy понимает.
    updated_fields = []
    if user_update_data.first_name is not None:
        current_user.first_name = user_update_data.first_name
        updated_fields.append("first_name")
    if user_update_data.last_name is not None:
        current_user.last_name = user_update_data.last_name
        updated_fields.append("last_name")
    if user_update_data.birth_date is not None:
        current_user.birth_date = user_update_data.birth_date
        updated_fields.append("birth_date")
    
    # Если были изменения, коммитим их в нашу БД
    if updated_fields:
        db.commit()
        logger.info(f"Synced local user {current_user.id} fields: {', '.join(updated_fields)}")
    
    # 6. После успешного обновления, запрашиваем свежие данные, чтобы вернуть их фронтенду
    return await get_user_profile(db, current_user)

async def get_user_dashboard(db: Session, current_user: User) -> UserDashboard:
    """
    Собирает ключевую информацию для "приборной панели" пользователя.
    """
    # --- 1. Получаем базовые данные из WooCommerce (только ФИО) ---
    #is_bot_accessible = await notification_service.ping_user(db, current_user)

    customer_data_response = await wc_client.get(f"wc/v3/customers/{current_user.wordpress_id}")
    customer_data = customer_data_response.json()
    first_name = customer_data.get("first_name")
    last_name = customer_data.get("last_name")

    # --- 2. Получаем данные из нашей БД ---
    # Счетчики
    cart_items_count = len(get_cart_items(db, user_id=current_user.id))
    favorite_items_count = len(get_favorite_items(db, user_id=current_user.id))
    counters = UserCounters(cart_items_count=cart_items_count, favorite_items_count=favorite_items_count)
    
    # Баланс и уровень
    balance = loyalty_service.get_user_balance(db, current_user)
    level = current_user.level

    # --- 3. Проверяем наличие активных заказов ---
    # Запрашиваем только 1 заказ со статусами, которые считаются "активными"
    active_orders_response = await wc_client.get(
        "wc/v3/orders",
        params={"customer": current_user.wordpress_id, "status": "processing,on-hold", "per_page": 1}
    )
    has_active_orders = len(active_orders_response.json()) > 0

    # --- 4. Рассчитываем прогресс до следующего уровня ---
    current_spending = await user_levels_service.get_total_spending_for_user(current_user.wordpress_id)
    
    # Определяем следующий уровень и сколько до него осталось
    next_level = None
    spending_to_next_level = None
    
    levels_order = ["bronze", "silver", "gold"] # Порядок уровней от низшего к высшему
    current_level_index = levels_order.index(level) if level in levels_order else -1
    
    if 0 <= current_level_index < len(levels_order) - 1:
        next_level_name = levels_order[current_level_index + 1]
        next_level_threshold = user_levels_service.LEVEL_THRESHOLDS[next_level_name]
        
        if current_spending < next_level_threshold:
            next_level = next_level_name
            spending_to_next_level = round(next_level_threshold - current_spending, 2)

    loyalty_progress = LoyaltyProgress(
        current_spending=round(current_spending, 2),
        next_level=next_level,
        spending_to_next_level=spending_to_next_level
    )
    
    # --- 5. Собираем финальный объект ---
    return UserDashboard(
        first_name=first_name,
        last_name=last_name,
        balance=balance,
        level=level,
        has_active_orders=has_active_orders,
        loyalty_progress=loyalty_progress,
        counters=counters,
        phone=current_user.phone, # <-- Добавляем

        is_blocked=current_user.is_blocked,
        is_bot_accessible=current_user.bot_accessible        
    )