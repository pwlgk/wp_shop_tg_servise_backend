# app/services/cms.py
import asyncio
import json
from bs4 import BeautifulSoup
from redis.asyncio import Redis
from typing import List
from html.parser import HTMLParser # <-- Импортируем HTML-парсер
from sqlalchemy.orm import Session
from app.crud import user as crud_user
from app.crud import notification as crud_notification
from app.bot.services import notification as bot_notification_service
from app.clients.woocommerce import wc_client
from app.db.session import SessionLocal
from app.schemas.cms import Banner, Page, StructuredPage, PageBlock, Story # <-- Добавляем Story
import logging

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600

# --- НОВЫЙ ВСПОМОГАТЕЛЬНЫЙ КЛАСС ---
class ImageSrcParser(HTMLParser):
    """Простой парсер для извлечения src первого тега img."""
    def __init__(self):
        super().__init__()
        self.image_url = None

    def handle_starttag(self, tag, attrs):
        # Ищем первый тег 'img' и прекращаем парсинг
        if tag == 'img' and self.image_url is None:
            attr_dict = dict(attrs)
            if 'src' in attr_dict:
                self.image_url = attr_dict['src']

def extract_image_url_from_html(html_content: str) -> str | None:
    """Извлекает URL из атрибута src первого тега img в HTML-строке."""
    if not html_content:
        return None
    parser = ImageSrcParser()
    parser.feed(html_content)
    return parser.image_url
# --- КОНЕЦ НОВОГО КОДА ---


async def get_active_banners(redis: Redis) -> List[Banner]:
    """
    Получает список баннеров с поддержкой изображений и видео.
    Данные (URL) теперь всегда приходят готовыми из WordPress благодаря PHP-хуку.
    """
    cache_key = "cms:banners"
    
    cached_data = await redis.get(cache_key)
    if cached_data:
        return [Banner.model_validate(b) for b in json.loads(cached_data)]
    
    logger.info("Fetching fresh banners from WordPress API.")
    # Запрашиваем до 100 баннеров, сортировка будет в Python
    response = await wc_client.async_client.get("wp/v2/banners", params={"per_page": 100})
    response.raise_for_status()
    banners_data = response.json()
    
    banners = []
    for banner_item in banners_data:
        try:
            acf_fields = banner_item.get("acf", {})
            
            content_type = acf_fields.get("banner_content_type", "image")
            media_url = None
            
            # --- УПРОЩЕННАЯ ЛОГИКА ---
            # Просто берем URL из соответствующего поля ACF
            if content_type == "video":
                media_url = acf_fields.get("banner_video")
            else: # image
                media_url = acf_fields.get("banner_image")
            # -------------------------

            # Добавляем баннер в список, только если URL был найден
            if media_url:
                banners.append(Banner(
                    id=banner_item["id"],
                    title=banner_item.get("title", {}).get("rendered", ""),
                    content_type=content_type,
                    media_url=media_url,
                    link_url=acf_fields.get("banner_link"),
                    sort_order=int(acf_fields.get("sort_order", 999))
                ))
        except Exception as e:
            logger.warning(f"Could not parse banner with ID {banner_item.get('id')}. Skipping. Error: {e}")

    # Сортируем список баннеров на стороне FastAPI перед кешированием
    banners.sort(key=lambda b: b.sort_order)
    
    await redis.set(cache_key, json.dumps([b.model_dump(mode='json') for b in banners]), ex=CACHE_TTL_SECONDS)
    
    logger.info(f"Successfully fetched and cached {len(banners)} banners.")
    return banners


async def get_page_by_slug(redis: Redis, slug: str) -> StructuredPage | None:
    """
    Получает контент страницы по ее ярлыку (slug), парсит HTML
    и возвращает в виде структурированного JSON.
    """
    cache_key = f"cms:page:{slug}"
    
    cached_data = await redis.get(cache_key)
    if cached_data:
        return StructuredPage.model_validate(json.loads(cached_data))

    params = {"slug": slug}
    response = await wc_client.async_client.get("wp/v2/pages", params=params)
    response.raise_for_status()
    pages_data = response.json()

    if not pages_data:
        return None

    page_data = pages_data[0]
    html_content = page_data.get("content", {}).get("rendered", "")
    
    soup = BeautifulSoup(html_content, "lxml")
    
    page_image_url = None
    blocks: List[PageBlock] = []

    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Ищем все нужные нам теги по всему документу, а не только на верхнем уровне.
    # WordPress часто оборачивает контент в div'ы.
    all_tags = soup.find_all(['figure', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'hr'])

    for tag in all_tags:
        # 1. Ищем обложку (первая картинка)
        if tag.name == 'figure' and tag.find('img') and page_image_url is None:
            # Проверяем, что это не картинка внутри какого-то другого блока,
            # а именно картинка-обложка (обычно она идет первой).
            if not tag.find_parent(['p', 'li']): # Если картинка не внутри параграфа или списка
                page_image_url = tag.find('img').get('src')
                continue # Пропускаем, чтобы не дублировать

        # 2. Обрабатываем заголовки
        if tag.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            blocks.append({"type": tag.name, "content": tag.get_text(strip=True)})

        # 3. Обрабатываем параграфы
        elif tag.name == 'p':
            content = ''.join(str(c) for c in tag.contents).strip()
            if content:
                blocks.append({"type": "p", "content": content})
        
        # 4. Обрабатываем списки
        elif tag.name in ['ul', 'ol']:
            # Проверяем, что мы не обработали этот список уже как часть другого блока
            if not tag.find_parent(['ul', 'ol']):
                items = [li.get_text(strip=True) for li in tag.find_all('li') if li.get_text(strip=True)]
                if items:
                    blocks.append({"type": tag.name, "items": items})
        
        # 5. Обрабатываем разделители
        elif tag.name == 'hr':
            blocks.append({"type": "hr"})
    # ----------------------------

    page = StructuredPage(
        id=page_data["id"],
        slug=page_data["slug"],
        title=page_data.get("title", {}).get("rendered", ""),
        image_url=page_image_url,
        blocks=blocks
    )
    
    await redis.set(cache_key, page.model_dump_json(), ex=CACHE_TTL_SECONDS)
    return page

async def process_new_promo(promo_id: int):
    """
    Фоновая задача: получает данные об акции, находит целевых пользователей
    и создает для них уведомления (в Mini App и в боте).
    """
    logger.info(f"Processing new promo with ID: {promo_id}")
    
    with SessionLocal() as db:
        try:
            # 1. Получаем полные данные об акции из WordPress
            response = await wc_client.async_client.get(f"wp/v2/promos/{promo_id}")
            response.raise_for_status()
            promo_data = response.json()

            logger.debug(f"Received promo data from WP for ID {promo_id}:\n{json.dumps(promo_data, indent=2)}")

            # 2. Извлекаем все необходимые данные
            title = promo_data.get("title", {}).get("rendered", "Новая акция!")
            content_html = promo_data.get("content", {}).get("rendered", "")
            acf_fields = promo_data.get("acf", {})
            
            target_level = acf_fields.get("promo_target_level", "all")
            action_url = acf_fields.get("promo_action_url")
            
            # --- Извлекаем URL медиа и очищаем текст ---
            media_url = None
            
            # Приоритет 1: Поле ACF для изображения
            promo_image_url = acf_fields.get("promo_image")
            if promo_image_url and isinstance(promo_image_url, str) and promo_image_url.startswith('http'):
                media_url = promo_image_url
            
            # Приоритет 2: Поле ACF для видео
            if not media_url:
                promo_video_url = acf_fields.get("promo_video")
                if promo_video_url and isinstance(promo_video_url, str) and promo_video_url.startswith('http'):
                    media_url = promo_video_url
            
            # Приоритет 3: Парсинг HTML как fallback
            if not media_url and content_html:
                media_url = extract_image_url_from_html(content_html)
            
            # --- ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ: ПРАВИЛЬНАЯ ОЧИСТКА ТЕКСТА ---
            soup = BeautifulSoup(content_html, "lxml")
            
            # Находим и удаляем ВСЕ блоки с картинками, чтобы они не попали в текст
            for figure in soup.find_all('figure', class_='wp-block-image'):
                figure.decompose()
                
            # Теперь извлекаем текст из "очищенного" HTML
            message_text = soup.get_text(separator='\n', strip=True)
            # --------------------------------------------------------

            # 3. Получаем список пользователей для рассылки
            users = crud_user.get_users(db, skip=0, limit=10000, level=target_level if target_level != "all" else None)

            if not users:
                logger.info(f"No target users found for promo {promo_id}. Target level: {target_level}")
                return

            # 4. Создаем уведомления и отправляем сообщения
            for user in users:
                existing_notification = crud_notification.get_notification_by_type_and_entity(
                    db, user_id=user.id, type="promo", related_entity_id=str(promo_id)
                )
                if existing_notification:
                    logger.info(f"Notification for promo {promo_id} already exists for user {user.id}. Skipping.")
                    continue

                crud_notification.create_notification(
                    db=db, user_id=user.id, type="promo",
                    title=title, message=message_text,
                    related_entity_id=str(promo_id),
                    action_url=action_url,
                    image_url=media_url
                )
                
                await bot_notification_service.send_promo_notification(
                    db=db, user=user, title=title, text=message_text,
                    image_url=media_url, action_url=action_url
                )
                await asyncio.sleep(0.1)
            
            logger.info(f"Promo {promo_id} processed for {len(users)} users.")

        except Exception as e:
            logger.error(f"Failed to process promo {promo_id}", exc_info=True)

async def get_active_stories(redis: Redis) -> List[Story]:
    """
    Получает список активных сторисов с поддержкой изображений и видео.
    """
    cache_key = "cms:stories"
    
    cached_data = await redis.get(cache_key)
    if cached_data:
        return [Story.model_validate(s) for s in json.loads(cached_data)]

    logger.info("Fetching fresh stories from WordPress API.")
    response = await wc_client.async_client.get("wp/v2/stories", params={"per_page": 100})
    response.raise_for_status()
    stories_data = response.json()

    stories = []
    for story_item in stories_data:
        try:
            acf_fields = story_item.get("acf", {})
            content_type = acf_fields.get("story_content_type", "image")
            media_url = None
            
            if content_type == "video":
                media_url = acf_fields.get("story_video")
            else: # image
                media_url = acf_fields.get("story_image")

            if media_url:
                # Описание берем из основного редактора и очищаем от HTML
                description_html = story_item.get("content", {}).get("rendered", "")
                soup = BeautifulSoup(description_html, "lxml")
                description_text = soup.get_text(strip=True)

                stories.append(Story(
                    id=story_item["id"],
                    title=story_item.get("title", {}).get("rendered", ""),
                    description=description_text,
                    content_type=content_type,
                    media_url=media_url,
                    link_url=acf_fields.get("story_link"),
                    sort_order=int(acf_fields.get("sort_order", 999))
                ))
        except Exception as e:
            logger.warning(f"Could not parse story with ID {story_item.get('id')}. Skipping. Error: {e}")

    stories.sort(key=lambda s: s.sort_order)
    
    await redis.set(cache_key, json.dumps([s.model_dump(mode='json') for s in stories]), ex=CACHE_TTL_SECONDS)
    
    return stories