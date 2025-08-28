# notify_tg/admin.py
from django.contrib import admin
from .models import PartnerTelegram

@admin.register(PartnerTelegram)
class PartnerTelegramAdmin(admin.ModelAdmin):
    list_display = ("partner", "chat_id", "is_active", "created_at")
    search_fields = ("partner__name", "partner__code", "chat_id")
    list_filter = ("is_active",)
