# app/core/logging_config.py
import logging
from logging.config import dictConfig

# Настройки для логирования.
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": "app.log",  # Имя файла лога
            "maxBytes": 10485760,  # 10 MB
            "backupCount": 5,      # Хранить 5 старых файлов
            "encoding": "utf8",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["console", "file"], "level": "INFO"},
        "fastapi": {"handlers": ["console", "file"], "level": "INFO"},
        "app": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        # Логгер для SQLAlchemy, чтобы видеть SQL-запросы (для отладки)
        # "sqlalchemy.engine": {"handlers": ["console"], "level": "INFO"},
    },
    "root": {
        "level": "INFO",
        "handlers": ["console", "file"],
    },
}

def setup_logging():
    """Применяет конфигурацию логирования."""
    dictConfig(LOGGING_CONFIG)