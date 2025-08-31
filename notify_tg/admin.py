# notify_tg/admin.py
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import PartnerTelegram


@admin.register(PartnerTelegram)
class PartnerTelegramAdmin(ModelAdmin):
    list_display = ("partner", "chat_id", "is_active", "created_at")
    search_fields = ("partner__name", "partner__code", "chat_id")
    list_filter = ("is_active",)
    list_select_related = ("partner",)
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")
