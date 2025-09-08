# app/utils/telegram.py
import hmac
import hashlib
from urllib.parse import unquote, parse_qsl
from app.core.config import settings

def validate_init_data(init_data: str) -> tuple[bool, dict]:
    """
    Валидирует initData от Telegram Mini App.
    Возвращает кортеж: (валидность, данные пользователя).
    """
    try:
        parsed_data = dict(parse_qsl(unquote(init_data)))
    except ValueError:
        return False, {}

    if "hash" not in parsed_data:
        return False, {}

    hash_str = parsed_data.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    
    secret_key = hmac.new("WebAppData".encode(), settings.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash == hash_str:
        return True, parsed_data
    
    return False, {}