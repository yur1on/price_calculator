# notify_tg/models.py
from django.db import models
from repairs.models import ReferralPartner

class PartnerTelegram(models.Model):
    partner = models.OneToOneField(
        ReferralPartner,
        on_delete=models.CASCADE,
        related_name="telegram",
        verbose_name="Партнёр",
    )
    chat_id = models.BigIntegerField("Telegram chat_id", unique=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "TG-привязка партнёра"
        verbose_name_plural = "TG-привязки партнёров"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} ↔ {self.chat_id}"
