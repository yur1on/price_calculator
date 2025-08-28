"""Application configuration for the repairs app.

This config class registers the app with Django's
application registry. When the app is ready it can
perform additional startup logic if required.
"""
from django.apps import AppConfig


class RepairsConfig(AppConfig):
    """Configuration class for the repairs app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "repairs"
    verbose_name = "Phone Repair Booking"

