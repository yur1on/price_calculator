from django.conf import settings
from django.db import models


class AccountProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Администратор"
        MASTER = "master", "Мастер"
        CLIENT = "client", "Клиент"

    class Approval(models.TextChoices):
        ACTIVE = "active", "Активен"
        PENDING = "pending", "Ожидает подтверждения"
        APPROVED = "approved", "Одобрен"
        REJECTED = "rejected", "Отклонён"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_profile")
    role = models.CharField("Роль", max_length=16, choices=Role.choices, default=Role.CLIENT, db_index=True)
    approval_status = models.CharField("Статус", max_length=16, choices=Approval.choices, default=Approval.ACTIVE, db_index=True)
    phone = models.CharField("Телефон", max_length=40, blank=True)
    phone_verified_at = models.DateTimeField("Телефон подтверждён", null=True, blank=True)
    referral_partner = models.OneToOneField(
        "repairs.ReferralPartner", on_delete=models.SET_NULL, related_name="account_profile",
        null=True, blank=True, verbose_name="Участник реферальной программы",
    )
    crm_client = models.OneToOneField(
        "crm.CRMClient", verbose_name="Подтверждённый клиент CRM", on_delete=models.SET_NULL,
        related_name="account_profile", null=True, blank=True,
        help_text="Назначается администратором после проверки владения. Автоматически по телефону не связывается.",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Профиль аккаунта"
        verbose_name_plural = "Профили аккаунтов"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} · {self.get_role_display()}"


class PhoneVerification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_verifications")
    phone = models.CharField("Телефон", max_length=20, db_index=True)
    code_hash = models.CharField("Хеш кода", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("Истекает", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    @property
    def is_active(self):
        from django.utils import timezone
        return not self.verified_at and self.attempts < 5 and self.expires_at > timezone.now()
