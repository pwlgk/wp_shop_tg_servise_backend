# app/core/logging_config.py

import logging
from logging.config import dictConfig

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

    },
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": "INFO"},
        "fastapi": {"handlers": ["console"], "level": "INFO"},
        "app": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"], 
    },
}

def setup_logging():
    """Применяет конфигурацию логирования."""
    dictConfig(LOGGING_CONFIG)