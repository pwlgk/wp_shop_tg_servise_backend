import json
from typing import Any, Dict, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Настройки базы данных
    DATABASE_USER: str
    DATABASE_PASSWORD: str
    DATABASE_HOST: str
    DATABASE_PORT: int
    DATABASE_NAME: str

    # Настройки WordPress
    WP_URL: str
    # WP_CONSUMER_KEY: str
    # WP_CONSUMER_SECRET: str
    WP_APP_USER: str
    WP_APP_PASSWORD: str
    TELEGRAM_BOT_TOKEN: str
    WP_WEBHOOK_SECRET: str
    TELEGRAM_BOT_USERNAME: str
    # Настройки JWT токенов
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7 дней
    REDIS_HOST: str
    REDIS_PORT: int
    LOYALTY_SETTINGS_JSON: str = Field(
        default='{"bronze": {"cashback_percent": 3}, "silver": {"cashback_percent": 5}, "gold": {"cashback_percent": 7}}'
    )
    POINTS_LIFETIME_DAYS: int = 90
    ADMIN_TELEGRAM_IDS_STR: str = Field(alias="ADMIN_TELEGRAM_IDS")
    ADMIN_CHAT_ID: int
    
    @property
    def ADMIN_TELEGRAM_IDS(self) -> List[int]:
        return [int(admin_id.strip()) for admin_id in self.ADMIN_TELEGRAM_IDS_STR.split(',')]

    # Это свойство будет автоматически парсить JSON в словарь
    LOYALTY_SETTINGS: Dict[str, Any] = {}

    TELEGRAM_BOT_TOKEN: str
    BASE_WEBHOOK_URL: str
    TELEGRAM_WEBHOOK_SECRET: str
    MINI_APP_URL: str
    
    @property
    def TELEGRAM_WEBHOOK_PATH(self) -> str:
        # Путь, который мы будем слушать. /bot/ префикс для безопасности
        return f"/bot/{self.TELEGRAM_BOT_TOKEN}"

    @field_validator("LOYALTY_SETTINGS", mode="before")
    def parse_loyalty_settings(cls, v, values):
        # v - всегда пустой словарь из-за инициализации
        # values.data - содержит все поля, включая LOYALTY_SETTINGS_JSON
        json_str = values.data.get("LOYALTY_SETTINGS_JSON")
        if json_str:
            return json.loads(json_str)
        return v
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+psycopg2://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()