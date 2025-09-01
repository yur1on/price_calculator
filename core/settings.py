"""
Настройки Django для проекта core.

Сгенерировано командой 'django-admin startproject' (Django 5.2.5).
Документация: https://docs.djangoproject.com/en/5.2/
"""

from pathlib import Path

# Базовая директория проекта
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ⚠️ В продакшене обязательно вынести в переменные окружения!
SECRET_KEY = "django-insecure-naw19pjep&r2ge^oc+hnp%tcqenkqv&x140^dc+&2k!la1m6^-"

# Режим разработки
DEBUG = True

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]



# Приложения
INSTALLED_APPS = [
    "unfold",  # обязательно до django.contrib.admin
    "unfold.contrib.filters",  # опционально (диапазоны дат/чисел и т.п.)
    "unfold.contrib.forms",  # опционально

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "repairs.apps.RepairsConfig",
    "notify_tg",
]


import os
# 🔑 Токен Telegram-бота (Только для локальной разработки!)
TELEGRAM_BOT_TOKEN = "8221103907:AAHD4RoedYbC2qfrWtN_5SzvxL4vIPbQnZY"
TELEGRAM_ADMIN_CHAT_IDS = "486747175"

# SITE_URL="http://127.0.0.1:8000"

# Промежуточные слои (middleware)
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Важно: LocaleMiddleware сразу после SessionMiddleware
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

# Шаблоны
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # При желании можно хранить общие шаблоны в BASE_DIR / 'templates'
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

# База данных (SQLite по умолчанию)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Валидаторы паролей
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Интернационализация и локализация
LANGUAGE_CODE = "ru"  # русский интерфейс в админке и шаблонах
LANGUAGES = [
    ("ru", "Русский"),
]
USE_I18N = True

# Таймзона проекта (можно поменять при необходимости)
TIME_ZONE = "Europe/Bratislava"
USE_TZ = True

# Статические файлы
STATIC_URL = "static/"
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]  # если кладёте статические в /static рядом с manage.py
# Для продакшена:
# STATIC_ROOT = BASE_DIR / "staticfiles"


# Тип автоинкрементного ключа по умолчанию
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Сколько заявок можно вести параллельно (1 = запрещать любые пересечения)
REPAIRS_MAX_PARALLEL_APPOINTMENTS = 2
