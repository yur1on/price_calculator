"""Application configuration for the repairs app."""
from django.apps import AppConfig


class RepairsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "repairs"
    verbose_name = "Мастерская"

    def ready(self):
        # Импортируем сигналы при старте приложения
        from . import signals  # noqa: F401
