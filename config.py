import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_POSTGRES_USER = os.environ.get("POSTGRES_USER") or "watermelon"
DEFAULT_POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD") or "change-me"
DEFAULT_POSTGRES_DB = os.environ.get("POSTGRES_DB") or "watermelon_db"
DEFAULT_POSTGRES_HOST = os.environ.get("POSTGRES_HOST") or "db"
DEFAULT_POSTGRES_PORT = os.environ.get("POSTGRES_PORT") or "5432"
DEFAULT_DATABASE_URL = (
    f"postgresql://{DEFAULT_POSTGRES_USER}:{DEFAULT_POSTGRES_PASSWORD}"
    f"@{DEFAULT_POSTGRES_HOST}:{DEFAULT_POSTGRES_PORT}/{DEFAULT_POSTGRES_DB}"
)


def _to_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes", "y"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SITE_TITLE = os.environ.get("SITE_TITLE") or "MyFlow"
    OWNER_NAME = os.environ.get("OWNER_NAME") or "재훈"
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or ""

    UPLOAD_FOLDER = (
        Path(os.environ["UPLOAD_FOLDER"]).expanduser()
        if os.environ.get("UPLOAD_FOLDER")
        else BASE_DIR / "app" / "static" / "uploads"
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class DevelopmentConfig(Config):
    DEBUG = True
    FLASK_ENV = "development"
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-local-only-change-me"


class ProductionConfig(Config):
    DEBUG = False
    FLASK_ENV = "production"
    SESSION_COOKIE_SECURE = _to_bool_env(os.environ.get("SESSION_COOKIE_SECURE"), default=True)


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
