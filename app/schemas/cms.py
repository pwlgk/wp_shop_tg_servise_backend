from pydantic import BaseModel, HttpUrl
from typing import List, Literal, Union
class Banner(BaseModel):
    id: int
    title: str
    image_url: HttpUrl # Оставляем HttpUrl, так как от WP всегда приходит полный URL
    
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Меняем HttpUrl на простую строку str
    link_url: str | None = None
    # -----------------------
    
    sort_order: int

class Page(BaseModel):
    id: int
    slug: str
    title: str
    content: str # Будет содержать HTML-контент


# --- НОВЫЕ СХЕМЫ ДЛЯ СТРУКТУРИРОВАННЫХ СТРАНИЦ ---

class HeadingBlock(BaseModel):
    type: Literal["h1", "h2", "h3", "h4", "h5", "h6"]
    content: str

class ParagraphBlock(BaseModel):
    type: Literal["p"]
    content: str # Будет содержать HTML-разметку внутри параграфа (<strong>, <a> и т.д.)

class ListBlock(BaseModel):
    type: Literal["ul", "ol"]
    items: List[str] # Список текстовых элементов

class SeparatorBlock(BaseModel):
    type: Literal["hr"]

# "Дженерик" тип блока, который может быть одним из вышеперечисленных
PageBlock = Union[HeadingBlock, ParagraphBlock, ListBlock, SeparatorBlock]

class StructuredPage(BaseModel):
    id: int
    slug: str
    title: str
    image_url: HttpUrl | None = None # Изображение-обложка страницы
    blocks: List[PageBlock]