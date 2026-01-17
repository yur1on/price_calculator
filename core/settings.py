"""
Настройки Django для проекта core.
Документация: https://docs.djangoproject.com/en/5.2/
"""

from pathlib import Path
import os
import mimetypes

mimetypes.init()
mimetypes.add_type("image/webp", ".webp", strict=True)
mimetypes.add_type("image/avif", ".avif", strict=True)
mimetypes.add_type("image/svg+xml", ".svg", strict=True)

# ──────────────────────────────────────────────────────────────
# БАЗА
# ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# Опционально подхватываем .env локально (в Docker переменные уже передаются из env_file)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

# Секретный ключ
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-dev-key-change-me")
BOOKING_TIME_STEP_MIN = 60
BOOKING_PREP_BUFFER_MIN = 0     # при желании можно менять
BOOKING_CLEANUP_BUFFER_MIN = 0  # при желании можно менять

# Режим отладки
# DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
# Режим отладки продакшен
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"

# Разрешённые хосты

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

# Базовый URL сайта (для ссылок в уведомлениях)
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")

# CSRF trusted origins (должны быть полными origin с протоколом)
_csrf_from_env = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if _csrf_from_env:
    CSRF_TRUSTED_ORIGINS = _csrf_from_env
elif SITE_URL.startswith(("http://", "https://")):
    CSRF_TRUSTED_ORIGINS = [SITE_URL]
else:
    CSRF_TRUSTED_ORIGINS = []

# ──────────────────────────────────────────────────────────────
# ПРИЛОЖЕНИЯ
# ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "unfold",                 # до django.contrib.admin
    "unfold.contrib.filters",
    "unfold.contrib.forms",

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "repairs.apps.RepairsConfig",
    "notify_tg",
    "news.apps.NewsConfig",

]

# ──────────────────────────────────────────────────────────────
# MIDDLEWARE / ШАБЛОНЫ / WSGI
# ──────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # ← сразу после SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "repairs.middleware.AnalyticsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

# ──────────────────────────────────────────────────────────────
# БАЗА ДАННЫХ
# ──────────────────────────────────────────────────────────────
# По умолчанию используем PostgreSQL; хост по умолчанию — "db" (как в docker-compose).
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "db")  # локально можно поставить 127.0.0.1
DB_PORT = os.getenv("DB_PORT", "5432")

if DB_NAME and DB_USER and DB_PASSWORD:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DB_NAME,
            "USER": DB_USER,
            "PASSWORD": DB_PASSWORD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
        }
    }
else:
    # Фолбэк на SQLite для быстрой локальной проверки без Postgres
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ──────────────────────────────────────────────────────────────
# ПАРОЛИ / I18N / TZ
# ──────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
LANGUAGES = [("ru", "Русский")]
USE_I18N = True

TIME_ZONE = os.getenv("TIME_ZONE", "Europe/Minsk")

USE_TZ = True

# ──────────────────────────────────────────────────────────────
# STATIC & MEDIA
# ──────────────────────────────────────────────────────────────

STATIC_URL = "/static/"
# В проде обычно собираем статику сюда (например, в Docker том):
STATIC_ROOT = os.getenv("STATIC_ROOT", str(BASE_DIR / "staticfiles"))

# В режиме разработки удобно иметь /static с исходниками
_static_dir = BASE_DIR / "static"

STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
if not DEBUG:
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"



MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ──────────────────────────────────────────────────────────────
# ПРОЧЕЕ
# ──────────────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Кол-во параллельных заявок (ёмкость)
REPAIRS_MAX_PARALLEL_APPOINTMENTS = int(os.getenv("REPAIRS_MAX_PARALLEL_APPOINTMENTS", "3"))
# Сколько дней вперёд можно записываться
REPAIRS_MAX_BOOK_AHEAD_DAYS = int(os.getenv("REPAIRS_MAX_BOOK_AHEAD_DAYS", "30"))

# Параметры Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_IDS = os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "")

# Флаг автозасева демо-данных (используется командами/скриптами при старте)
SEED_DATA = os.getenv("SEED_DATA", "0") in ("1", "true", "True")

# ──────────────────────────────────────────────────────────────
# SECURITY для продакшена (включаются когда DEBUG=False)
# ──────────────────────────────────────────────────────────────
if not DEBUG:
    # Если стоим за прокси/Ingress и нужен корректный scheme
    if os.getenv("ENABLE_SECURE_PROXY_SSL_HEADER", "1") in ("1", "true", "True"):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        USE_X_FORWARDED_HOST = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# ──────────────────────────────────────────────────────────────
# ЛОГИРОВАНИЕ (минимально удобное для dev/prod)
# ──────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {name}: {message}", "style": "{"},
        "verbose": {"format": "{asctime} [{levelname}] {name}: {message}", "style": "{", "datefmt": "%Y-%m-%d %H:%M:%S"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if not DEBUG else "simple",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
