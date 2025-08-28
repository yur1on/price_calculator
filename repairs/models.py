"""База данных приложения repairs.

Сущности: бренды и модели телефонов, типы ремонта и цены,
мастера, рабочие часы и записи на ремонт.

Реферальная система: модели ``ReferralPartner`` и ``ReferralRedemption``.
Партнёры (продавцы) выдают клиентам коды на скидку; клиент получает
скидку, продавец — комиссию после завершения ремонта.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import timedelta

from django.db import models
from django.utils import timezone


class PhoneBrand(models.Model):
    name = models.CharField("Название бренда", max_length=50, unique=True)
    slug = models.SlugField("Слаг", unique=True)

    class Meta:
        verbose_name = "Бренд"
        verbose_name_plural = "Бренды"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PhoneModel(models.Model):
    CATEGORY_CHOICES = [
        ("phone", "Телефон"),
        ("tablet", "Планшет"),
        ("watch", "Смарт-часы"),
    ]

    brand = models.ForeignKey(
        PhoneBrand,
        verbose_name="Бренд",
        on_delete=models.CASCADE,
        related_name="models",
    )
    name = models.CharField("Модель", max_length=80)
    slug = models.SlugField("Слаг", unique=True)
    category = models.CharField(
        "Категория",
        max_length=10,
        choices=CATEGORY_CHOICES,
        default="phone",
        db_index=True,
    )

    class Meta:
        verbose_name = "Модель устройства"
        verbose_name_plural = "Модели устройств"
        ordering = ["brand__name", "category", "name"]
        unique_together = ["brand", "name"]

    def __str__(self) -> str:
        return f"{self.brand.name} {self.name}"


class RepairType(models.Model):
    name = models.CharField("Тип ремонта", max_length=80)
    slug = models.SlugField("Слаг", unique=True)
    default_duration_min = models.PositiveIntegerField("Длительность по умолчанию (мин)", default=60)

    class Meta:
        verbose_name = "Тип ремонта"
        verbose_name_plural = "Типы ремонта"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ModelRepairPrice(models.Model):
    phone_model = models.ForeignKey(
        PhoneModel,
        verbose_name="Модель",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    repair_type = models.ForeignKey(
        RepairType,
        verbose_name="Тип ремонта",
        on_delete=models.CASCADE,
        related_name="model_prices",
    )
    price = models.DecimalField("Цена (BYN)", max_digits=10, decimal_places=2)

    duration_min = models.PositiveIntegerField("Длительность (мин)")
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Цена ремонта по модели"
        verbose_name_plural = "Цены ремонтов по моделям"
        ordering = ["phone_model__brand__name", "phone_model__name", "repair_type__name"]
        unique_together = ["phone_model", "repair_type"]

    def __str__(self) -> str:
        return f"{self.phone_model} — {self.repair_type} ({self.price} BYN)"


class ReferralPartner(models.Model):
    name = models.CharField("Партнёр (продавец)", max_length=120)
    contact = models.CharField("Контакты", max_length=120, blank=True)
    code = models.CharField("Код", max_length=16, unique=True)
    client_discount_pct = models.DecimalField("Скидка клиенту (%)", max_digits=4, decimal_places=2, default=Decimal("5.00"))
    partner_commission_pct = models.DecimalField("Комиссия партнёру (%)", max_digits=4, decimal_places=2, default=Decimal("5.00"))
    expires_at = models.DateTimeField("Действует до", null=True, blank=True)
    max_uses = models.PositiveIntegerField("Макс. использований", null=True, blank=True)

    class Meta:
        verbose_name = "Партнёр (продавец)"
        verbose_name_plural = "Партнёры (продавцы)"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def is_active(self) -> bool:
        if self.expires_at and self.expires_at < timezone.now():
            return False
        if self.max_uses is not None and self.redemptions.count() >= self.max_uses:
            return False
        return True


class ReferralRedemption(models.Model):
    STATUS_CHOICES = [
        ("pending", "Ожидает выполнения"),
        ("accrued", "Начислено (запись выполнена)"),
        ("paid", "Выплачено партнёру"),
    ]

    partner = models.ForeignKey(
        ReferralPartner,
        verbose_name="Партнёр",
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    phone = models.CharField("Телефон клиента", max_length=20)
    appointment = models.ForeignKey(
        "Appointment",
        verbose_name="Запись",
        on_delete=models.CASCADE,
        related_name="referrals",
    )
    discount_amount = models.DecimalField("Сумма скидки", max_digits=10, decimal_places=2)
    commission_amount = models.DecimalField("Комиссия партнёра", max_digits=10, decimal_places=2)
    status = models.CharField("Статус выплаты", max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True)
    paid_at = models.DateTimeField("Дата выплаты", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Использование реф-кода"
        verbose_name_plural = "Использования реф-кодов"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["partner", "appointment"], name="uniq_partner_appointment_redemption"),
        ]

    def __str__(self) -> str:
        return f"{self.partner.code} → #{self.appointment_id} [{self.get_status_display()}]"


class Technician(models.Model):
    name = models.CharField("Имя мастера", max_length=80)
    skills = models.ManyToManyField(RepairType, verbose_name="Навыки", related_name="technicians")

    class Meta:
        verbose_name = "Мастер"
        verbose_name_plural = "Мастера"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WorkingHour(models.Model):
    WEEKDAYS = [
        (0, "Понедельник"),
        (1, "Вторник"),
        (2, "Среда"),
        (3, "Четверг"),
        (4, "Пятница"),
        (5, "Суббота"),
        (6, "Воскресенье"),
    ]
    weekday = models.IntegerField("День недели", choices=WEEKDAYS)
    start = models.TimeField("Начало")
    end = models.TimeField("Конец")

    class Meta:
        verbose_name = "Часы работы"
        verbose_name_plural = "Часы работы"
        ordering = ["weekday", "start"]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()}: {self.start}–{self.end}"


class TimeOff(models.Model):
    technician = models.ForeignKey(
        Technician,
        verbose_name="Мастер",
        on_delete=models.CASCADE,
        related_name="time_off",
    )
    start = models.DateTimeField("С")
    end = models.DateTimeField("По")
    reason = models.CharField("Причина", max_length=120, blank=True)

    class Meta:
        verbose_name = "Отсутствие мастера"
        verbose_name_plural = "Отсутствия мастеров"
        ordering = ["start"]

    def __str__(self) -> str:
        return f"{self.technician} вне графика {self.start}–{self.end} ({self.reason})"


class Appointment(models.Model):
    STATUS_CHOICES = [
        ("new", "Новая"),
        ("confirmed", "Подтверждена"),
        ("done", "Завершена"),
        ("cancelled", "Отмена"),
    ]

    phone_model = models.ForeignKey(
        PhoneModel,
        verbose_name="Модель",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    repair_type = models.ForeignKey(
        RepairType,
        verbose_name="Тип ремонта",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    technician = models.ForeignKey(
        Technician,
        verbose_name="Мастер",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="appointments",
    )
    start = models.DateTimeField("Начало")
    end = models.DateTimeField("Окончание")
    customer_name = models.CharField("Имя клиента", max_length=120)
    customer_phone = models.CharField("Телефон клиента", max_length=20)
    referral_code = models.CharField("Код продавца", max_length=16, blank=True)
    price_original = models.DecimalField("Цена до скидки", max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField("Скидка", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    price_final = models.DecimalField("Итоговая цена", max_digits=10, decimal_places=2)
    status = models.CharField("Статус", max_length=12, choices=STATUS_CHOICES, default="new")
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Запись"
        verbose_name_plural = "Записи"
        ordering = ["-start"]

    def __str__(self) -> str:
        return f"{self.customer_name} • {self.phone_model} • {self.repair_type} • {self.start:%d.%m.%Y %H:%M}"

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def apply_referral(self) -> None:
        if not self.referral_code:
            self.discount_amount = Decimal("0")
            self.price_final = self.price_original
            return
        try:
            partner = ReferralPartner.objects.get(code__iexact=self.referral_code)
        except ReferralPartner.DoesNotExist:
            self.discount_amount = Decimal("0")
            self.price_final = self.price_original
            return

        if not partner.is_active():
            self.discount_amount = Decimal("0")
            self.price_final = self.price_original
            return

        discount = (self.price_original * partner.client_discount_pct / Decimal("100")).quantize(Decimal("0.01"))
        self.discount_amount = discount
        self.price_final = self.price_original - discount

    def save(self, *args, **kwargs) -> None:
        if not self.price_final:
            self.price_final = self.price_original - self.discount_amount
        super().save(*args, **kwargs)
