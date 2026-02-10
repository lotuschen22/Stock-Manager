import os

from dotenv import load_dotenv

load_dotenv()


def get_google_api_key() -> str:
    return os.getenv("GOOGLE_API_KEY", "").strip()


def get_primary_model() -> str:
    return os.getenv("GOOGLE_PRIMARY_MODEL", "gemini-2.5-pro").strip()


def get_fallback_model() -> str:
    return os.getenv("GOOGLE_FALLBACK_MODEL", "gemini-2.5-flash").strip()


def get_model_timeout_seconds() -> int:
    raw = os.getenv("GOOGLE_MODEL_TIMEOUT_SECONDS", "30").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 30
    return max(5, value)
