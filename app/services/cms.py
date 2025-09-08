# app/services/cms.py
import json
from bs4 import BeautifulSoup
from redis.asyncio import Redis
from typing import List
from html.parser import HTMLParser # <-- Импортируем HTML-парсер

from app.clients.woocommerce import wc_client
from app.schemas.cms import Banner, Page, PageBlock, StructuredPage

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
    Получает список баннеров. Сортировка будет производиться на фронтенде.
    """
    cache_key = "cms:banners"
    
    cached_data = await redis.get(cache_key)
    if cached_data:
        # Pydantic v2 может требовать явного указания типа для десериализации дженериков
        # но в данном случае List[Banner] должен работать.
        # Если возникнет ошибка, можно использовать `parse_raw_as(List[Banner], cached_data)`
        return [Banner.model_validate(b) for b in json.loads(cached_data)]
    
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Убираем параметры сортировки ---
    # Мы больше не передаем _embed, orderby, meta_key, order
    # Просто запрашиваем все опубликованные баннеры (до 100 штук по умолчанию)
    params = {"per_page": 100} # Получаем до 100 баннеров, этого должно хватить
    response = await wc_client.async_client.get("wp/v2/banners", params=params)
    response.raise_for_status()
    banners_data = response.json()
    
    banners = []
    for banner_item in banners_data:
        content_html = banner_item.get("content", {}).get("rendered", "")
        image_url = extract_image_url_from_html(content_html)
        
        if image_url:
            acf_fields = banner_item.get("acf", {})
            banners.append(Banner(
                id=banner_item["id"],
                title=banner_item.get("title", {}).get("rendered", ""),
                image_url=image_url,
                link_url=acf_fields.get("banner_link"),
                # --- ДОБАВЛЯЕМ ПОЛЕ ДЛЯ СОРТИРОВКИ ---
                sort_order=int(acf_fields.get("sort_order", 999)) # 999 - чтобы баннеры без номера были в конце
            ))

    await redis.set(cache_key, json.dumps([b.model_dump(mode='json') for b in banners]), ex=CACHE_TTL_SECONDS)
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