from sqlalchemy.orm import Session
from app.clients.woocommerce import wc_client
from app.models.user import User
from app.schemas.loyalty import LoyaltyProgress, UserDashboard
from app.schemas.user import UserProfile, UserUpdate, UserCounters
from app.crud.cart import get_cart_items, get_favorite_items
from app.services import user_levels as user_levels_service # <-- Импортируем
from app.bot.services import notification as notification_service
from app.services import loyalty as loyalty_service
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
        "counters": counters
    }
    
    # 4. Валидируем и возвращаем результат
    return UserProfile.model_validate(profile_data)
async def update_user_profile(current_user: User, user_update_data: UserUpdate) -> UserProfile:
    """
    Обновляет данные профиля пользователя в WooCommerce.
    """
    # Преобразуем Pydantic-схему в словарь, исключая неустановленные значения
    update_data_dict = user_update_data.model_dump(exclude_unset=True)
    
    if not update_data_dict:
        # Если нечего обновлять, просто возвращаем текущий профиль
        return await get_user_profile(current_user)

    # Отправляем запрос на обновление в WooCommerce
    updated_wc_user_data = await wc_client.post(f"wc/v3/customers/{current_user.wordpress_id}", json=update_data_dict)
    
    # Собираем и возвращаем обновленный профиль
    profile_data = {
        "telegram_id": current_user.telegram_id,
        "username": current_user.username,
        **updated_wc_user_data
    }
    
    return UserProfile.model_validate(profile_data)


async def get_user_dashboard(db: Session, current_user: User) -> UserDashboard:
    """
    Собирает ключевую информацию для "приборной панели" пользователя.
    """
    # --- 1. Получаем базовые данные из WooCommerce (только ФИО) ---
    is_bot_accessible = await notification_service.ping_user(db, current_user)

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
        is_blocked=current_user.is_blocked,
        is_bot_accessible=is_bot_accessible
        
    )