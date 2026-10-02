import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.storage import private_media_storage


def normalize_phone(value):
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("80"):
        digits = "375" + digits[2:]
    elif len(digits) == 9:
        digits = "375" + digits
    return f"+{digits}" if digits else ""


class CRMClient(models.Model):
    name = models.CharField("Имя", max_length=160)
    phone = models.CharField("Телефон", max_length=40)
    normalized_phone = models.CharField("Нормализованный телефон", max_length=20, db_index=True)
    additional_phone = models.CharField("Дополнительный телефон", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    source = models.CharField("Источник", max_length=120, blank=True)
    city = models.CharField("Город", max_length=120, blank=True)
    note = models.TextField("Заметка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Клиент CRM"
        verbose_name_plural = "Клиенты CRM"
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["name"])]

    def save(self, *args, **kwargs):
        self.normalized_phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} · {self.phone}"


class CRMDevice(models.Model):
    client = models.ForeignKey(CRMClient, verbose_name="Клиент", on_delete=models.PROTECT, related_name="devices")
    device_type = models.CharField("Тип устройства", max_length=80)
    brand = models.CharField("Бренд", max_length=100, blank=True)
    model = models.CharField("Модель", max_length=140, blank=True)
    imei = models.CharField("IMEI", max_length=32, blank=True, db_index=True)
    serial_number = models.CharField("Серийный номер", max_length=120, blank=True, db_index=True)
    color = models.CharField("Цвет", max_length=80, blank=True)
    condition = models.TextField("Состояние", blank=True)
    included_items = models.TextField("Комплектность", blank=True)
    unlock_code = models.CharField("Код устройства", max_length=255, blank=True)
    note = models.TextField("Заметка", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Устройство клиента"
        verbose_name_plural = "Устройства клиентов"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["client", "serial_number"])]

    @property
    def display_name(self):
        return " ".join(filter(None, [self.brand, self.model])) or self.device_type

    def __str__(self):
        return self.display_name


class CRMOrder(models.Model):
    class Type(models.TextChoices):
        PAID = "paid", "Платный"
        WARRANTY = "warranty", "Гарантийный"

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Принято"
        DIAGNOSTIC = "diagnostic", "Диагностика"
        APPROVAL = "approval", "Ожидает согласования"
        WAITING_PART = "waiting_part", "Ожидает деталь"
        REPAIR = "repair", "В ремонте"
        READY = "ready", "Готово к выдаче"
        ISSUED = "issued", "Выдано"
        CANCELED = "canceled", "Отказ / отмена"

    TRANSITIONS = {
        Status.ACCEPTED: {Status.DIAGNOSTIC, Status.CANCELED},
        Status.DIAGNOSTIC: {Status.APPROVAL, Status.WAITING_PART, Status.REPAIR, Status.CANCELED},
        Status.APPROVAL: {Status.WAITING_PART, Status.REPAIR, Status.CANCELED},
        Status.WAITING_PART: {Status.REPAIR, Status.CANCELED},
        Status.REPAIR: {Status.WAITING_PART, Status.READY, Status.CANCELED},
        Status.READY: {Status.ISSUED},
        Status.ISSUED: set(),
        Status.CANCELED: set(),
    }

    number = models.CharField("Номер заказа", max_length=9, unique=True, editable=False)
    client = models.ForeignKey(CRMClient, verbose_name="Клиент", on_delete=models.PROTECT, related_name="orders")
    device = models.ForeignKey(CRMDevice, verbose_name="Устройство", on_delete=models.PROTECT, related_name="orders")
    employee = models.ForeignKey("finance.Employee", verbose_name="Мастер", on_delete=models.PROTECT, related_name="crm_orders", null=True, blank=True)
    source_appointment = models.OneToOneField("repairs.Appointment", verbose_name="Онлайн-запись", on_delete=models.SET_NULL, related_name="crm_order", null=True, blank=True)
    order_type = models.CharField("Тип заказа", max_length=20, choices=Type.choices, default=Type.PAID)
    warranty_source_order = models.ForeignKey("self", verbose_name="Исходный гарантийный ремонт", on_delete=models.PROTECT, related_name="warranty_orders", null=True, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.ACCEPTED, db_index=True)
    accepted_at = models.DateTimeField("Принят", default=timezone.now)
    ready_at = models.DateTimeField("Готов", null=True, blank=True)
    issued_at = models.DateTimeField("Выдан", null=True, blank=True)
    issue_description = models.TextField("Причина обращения")
    diagnostic_result = models.TextField("Результат диагностики", blank=True)
    internal_note = models.TextField("Внутренняя заметка", blank=True)
    condition_on_intake = models.TextField("Состояние при приёме", blank=True)
    accessories_on_intake = models.TextField("Комплектность при приёме", blank=True)
    agreed_price = models.DecimalField("Согласованная стоимость", max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    warranty_days = models.PositiveIntegerField("Гарантия, дней", default=0)
    warranty_started_at = models.DateTimeField("Начало гарантии", null=True, blank=True)
    final_price = models.DecimalField(
        "Итоговая сумма", max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    paid_amount = models.DecimalField(
        "Оплачено клиентом", max_digits=12, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(0)],
    )
    finance_repair = models.OneToOneField(
        "finance.RepairFinance", verbose_name="Финансовый ремонт", on_delete=models.PROTECT,
        related_name="crm_order", null=True, blank=True,
    )
    estimated_work_minutes_snapshot = models.PositiveIntegerField(
        "Плановое время работы, минут", null=True, blank=True, default=60,
        help_text="Внутренняя оценка трудоёмкости ремонта.",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Заказ CRM"
        verbose_name_plural = "Заказы CRM"
        ordering = ["-accepted_at", "-id"]
        permissions = [
            ("access_crm", "Может входить в CRM"),
            ("view_all_orders", "Может видеть все заказы CRM"),
            ("change_order_status", "Может менять статусы CRM"),
            ("manage_documents", "Может открывать документы CRM"),
        ]
        indexes = [
            models.Index(fields=["status", "accepted_at"]),
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["order_type", "accepted_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(agreed_price__gte=0), name="crm_order_price_nonnegative"),
        ]

    @classmethod
    def generate_number(cls):
        alphabet = string.ascii_uppercase + string.digits
        return "R" + "".join(secrets.choice(alphabet) for _ in range(8))

    def save(self, *args, **kwargs):
        if self.number:
            return super().save(*args, **kwargs)
        for _ in range(12):
            number = self.generate_number()
            if not type(self).objects.filter(number=number).exists():
                self.number = number
                return super().save(*args, **kwargs)
        raise RuntimeError("Не удалось сформировать уникальный номер заказа.")

    @property
    def allowed_next_statuses(self):
        return self.TRANSITIONS.get(self.status, set())

    @property
    def works_total(self):
        return sum((item.total for item in self.work_items.all()), Decimal("0.00"))

    @property
    def warranty_until(self):
        if not self.warranty_started_at or not self.warranty_days:
            return None
        return self.warranty_started_at + timezone.timedelta(days=self.warranty_days)

    @property
    def amount_due(self):
        return self.final_price if self.final_price is not None else self.agreed_price

    @property
    def payment_remaining(self):
        return max(Decimal("0.00"), self.amount_due - self.paid_amount)

    @property
    def is_active(self):
        return self.status not in {self.Status.ISSUED, self.Status.CANCELED}

    @property
    def client_status_label(self):
        from .client_status import CLIENT_STATUS
        return CLIENT_STATUS[self.status][0]

    @property
    def client_status_description(self):
        from .client_status import CLIENT_STATUS
        return CLIENT_STATUS[self.status][1]

    def __str__(self):
        return f"{self.number} · {self.device}"


class CRMWorkTemplate(models.Model):
    name = models.CharField("Название", max_length=180, unique=True)
    default_price = models.DecimalField("Цена по умолчанию", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    description = models.TextField("Описание", blank=True)
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class CRMIssueTemplate(models.Model):
    name = models.CharField("Название", max_length=180, unique=True)
    text = models.TextField("Текст")
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class CRMDeviceType(models.Model):
    name = models.CharField("Название", max_length=100, unique=True)
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Тип устройства CRM"
        verbose_name_plural = "Типы устройств CRM"
        ordering = ["sort_order", "name"]

    def __str__(self): return self.name


class CRMBrand(models.Model):
    name = models.CharField("Название", max_length=100, unique=True)
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Бренд CRM"
        verbose_name_plural = "Бренды CRM"
        ordering = ["sort_order", "name"]

    def __str__(self): return self.name


class CRMDeviceModel(models.Model):
    brand = models.ForeignKey(CRMBrand, on_delete=models.CASCADE, related_name="device_models", verbose_name="Бренд")
    name = models.CharField("Название", max_length=140)
    is_active = models.BooleanField("Активна", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Модель устройства CRM"
        verbose_name_plural = "Модели устройств CRM"
        ordering = ["brand__sort_order", "brand__name", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["brand", "name"], name="uniq_crm_brand_model")]

    def __str__(self): return f"{self.brand} · {self.name}"


class CRMWarrantyTerm(models.Model):
    days = models.PositiveIntegerField("Дней", unique=True)
    label = models.CharField("Название", max_length=100, blank=True)
    is_active = models.BooleanField("Активен", default=True)
    is_default = models.BooleanField("По умолчанию", default=False)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Срок гарантии CRM"
        verbose_name_plural = "Сроки гарантии CRM"
        ordering = ["sort_order", "days"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            type(self).objects.exclude(pk=self.pk).update(is_default=False)

    def __str__(self): return self.label or f"{self.days} дней"


class CRMWorkItem(models.Model):
    order = models.ForeignKey(CRMOrder, verbose_name="Заказ", on_delete=models.CASCADE, related_name="work_items")
    name = models.CharField("Работа", max_length=180)
    quantity = models.DecimalField("Количество", max_digits=8, decimal_places=2, default=1, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField("Цена", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    comment = models.CharField("Комментарий", max_length=255, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return self.name


class CRMComment(models.Model):
    order = models.ForeignKey(CRMOrder, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="crm_comments")
    text = models.TextField("Комментарий")
    created_at = models.DateTimeField("Добавлен", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class CRMEvent(models.Model):
    class Type(models.TextChoices):
        CREATED = "created", "Заказ создан"
        STATUS = "status", "Статус изменён"
        EMPLOYEE = "employee", "Мастер изменён"
        DIAGNOSTIC = "diagnostic", "Диагностика изменена"
        PRICE = "price", "Цена изменена"
        WORK = "work", "Работа изменена"
        COMMENT = "comment", "Комментарий добавлен"
        ATTACHMENT = "attachment", "Файл добавлен"
        WARRANTY = "warranty", "Гарантия изменена"
        DOCUMENT = "document", "Документ открыт"
        PART = "part", "Операция с деталью"

    order = models.ForeignKey(CRMOrder, on_delete=models.CASCADE, related_name="events")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="crm_events", null=True, blank=True)
    event_type = models.CharField("Тип", max_length=24, choices=Type.choices)
    description = models.CharField("Описание", max_length=500)
    old_value = models.CharField("Было", max_length=255, blank=True)
    new_value = models.CharField("Стало", max_length=255, blank=True)
    created_at = models.DateTimeField("Время", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class CRMOrderPartUsage(models.Model):
    class Status(models.TextChoices):
        INSTALLED = "installed", "Установлена"
        REMOVED = "removed", "Возвращена на склад"

    order = models.ForeignKey(CRMOrder, verbose_name="Ремонт CRM", on_delete=models.PROTECT, related_name="part_usages")
    part_item = models.ForeignKey("finance.PartItem", verbose_name="Экземпляр детали", on_delete=models.PROTECT, related_name="crm_usages")
    finance_repair = models.ForeignKey("finance.RepairFinance", verbose_name="Финансовый ремонт", on_delete=models.PROTECT, related_name="crm_part_usages")
    unit_cost_snapshot = models.DecimalField("Закупочная стоимость", max_digits=12, decimal_places=2)
    supplier_name_snapshot = models.CharField("Поставщик", max_length=160)
    warranty_days_snapshot = models.PositiveIntegerField("Гарантия поставщика, дней", default=0)
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.INSTALLED, db_index=True)
    installed_at = models.DateTimeField("Установлена", auto_now_add=True)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Установил", on_delete=models.PROTECT, related_name="installed_crm_parts")
    removed_at = models.DateTimeField("Возвращена", null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Вернул", on_delete=models.PROTECT, related_name="removed_crm_parts", null=True, blank=True)

    class Meta:
        verbose_name = "Деталь в CRM-ремонте"
        verbose_name_plural = "Детали в CRM-ремонтах"
        ordering = ["installed_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["part_item"], condition=models.Q(status="installed"), name="uniq_active_crm_part_usage"),
            models.CheckConstraint(condition=models.Q(unit_cost_snapshot__gte=0), name="crm_part_usage_cost_nonnegative"),
        ]
        indexes = [models.Index(fields=["order", "status"])]

    @property
    def warranty_until(self):
        if not self.warranty_days_snapshot:
            return None
        return self.part_item.receipt.date + timezone.timedelta(days=self.warranty_days_snapshot)

    @property
    def warranty_active(self):
        return bool(self.warranty_until and self.warranty_until >= timezone.localdate())

    def __str__(self):
        return f"{self.order.number} · {self.part_item}"


def crm_attachment_path(instance, filename):
    safe = filename.rsplit("/", 1)[-1]
    return f"crm/orders/{instance.order_id}/{secrets.token_hex(8)}-{safe}"


class CRMAttachment(models.Model):
    order = models.ForeignKey(CRMOrder, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField("Файл", upload_to=crm_attachment_path, storage=private_media_storage)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="crm_attachments")
    original_name = models.CharField("Имя файла", max_length=255)
    description = models.CharField("Описание", max_length=255, blank=True)
    created_at = models.DateTimeField("Загружен", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_image(self):
        return self.original_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


class CRMDocumentSettings(models.Model):
    workshop_name = models.CharField("Название", max_length=160, default="Техсфера")
    phone = models.CharField("Телефон", max_length=80, blank=True)
    email = models.EmailField("Email", blank=True)
    website = models.CharField("Сайт", max_length=255, blank=True, default="")
    address = models.CharField("Адрес", max_length=255, blank=True)
    receipt_terms = models.TextField("Условия приёма", blank=True)
    act_terms = models.TextField("Текст акта", blank=True)
    warranty_terms = models.TextField("Условия гарантии", blank=True)
    diagnostic_warning_hours = models.PositiveSmallIntegerField(
        "Диагностика без изменений, часов", default=24,
        help_text="Внутренний индикатор внимания, не SLA для клиента.",
    )
    approval_warning_hours = models.PositiveSmallIntegerField(
        "Согласование без изменений, часов", default=24,
        help_text="Внутренний индикатор внимания, не SLA для клиента.",
    )
    waiting_part_warning_days = models.PositiveSmallIntegerField(
        "Ожидание детали без изменений, дней", default=3,
        help_text="Внутренний индикатор внимания, не SLA для клиента.",
    )
    ready_warning_days = models.PositiveSmallIntegerField(
        "Готов и не забран, дней", default=3,
        help_text="Внутренний индикатор внимания, не SLA для клиента.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки документов CRM"
        verbose_name_plural = "Настройки документов CRM"
        permissions = [("manage_crm_settings", "Может управлять настройками CRM")]

    def save(self, *args, **kwargs):
        if not self.pk and CRMDocumentSettings.objects.exists():
            raise ValidationError("Настройки документов уже созданы.")
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
