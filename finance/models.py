from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.conf import settings


MONEY_PLACES = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, verbose_name="Аккаунт пользователя",
        on_delete=models.SET_NULL, related_name="employee_profile", null=True, blank=True,
    )
    name = models.CharField("Имя", max_length=120)
    default_percent = models.DecimalField(
        "Процент по умолчанию",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_owner = models.BooleanField("Владелец", default=False)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Исполнитель"
        verbose_name_plural = "Исполнители"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (владелец)" if self.is_owner else self.name


class FinanceDashboard(Employee):
    class Meta:
        proxy = True
        verbose_name = "Финансовый dashboard"
        verbose_name_plural = "Финансовый dashboard"


class RepairFinance(models.Model):
    date = models.DateField("Дата")
    description = models.CharField("Описание ремонта", max_length=255)
    revenue = models.DecimalField(
        "Получено от клиента", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    part_cost = models.DecimalField(
        "Стоимость запчасти", max_digits=12, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(0)],
    )
    manual_part_cost = models.DecimalField(
        "Дополнительная стоимость запчастей вручную", max_digits=12, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(0)],
        help_text="Используйте для деталей, которых нет на складе, или деталей клиента.",
    )
    employee = models.ForeignKey(
        Employee, verbose_name="Исполнитель", on_delete=models.PROTECT,
        related_name="repairs",
    )
    master_percent = models.DecimalField(
        "Процент исполнителя", max_digits=5, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Сохраняется в ремонте и не меняется при изменении профиля исполнителя.",
    )
    repair_margin = models.DecimalField("Маржа до зарплаты", max_digits=12, decimal_places=2, editable=False)
    master_salary = models.DecimalField("Зарплата мастера", max_digits=12, decimal_places=2, editable=False)
    workshop_profit = models.DecimalField("Прибыль мастерской", max_digits=12, decimal_places=2, editable=False)
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Ремонт (финансы)"
        verbose_name_plural = "Ремонты (финансы)"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["employee", "date"])]

    def __str__(self):
        return f"{self.date:%d.%m.%Y} — {self.description}"

    def calculate(self):
        revenue = money(self.revenue)
        part_cost = money(self.part_cost)
        margin = money(revenue - part_cost)
        percent = Decimal("0.00") if self.employee.is_owner else Decimal(self.master_percent or 0)
        salary_base = max(margin, Decimal("0.00"))
        salary = money(salary_base * percent / Decimal("100"))
        self.repair_margin = margin
        self.master_salary = salary
        self.workshop_profit = money(margin - salary)

    def clean(self):
        super().clean()
        if self.employee_id and self.employee.is_owner:
            self.master_percent = Decimal("0.00")

    def save(self, *args, **kwargs):
        if self._state.adding and not self.manual_part_cost and self.part_cost:
            self.manual_part_cost = self.part_cost
        self.full_clean()
        self.calculate()
        super().save(*args, **kwargs)

    def recalculate_part_cost(self):
        linked_cost = self.repair_parts.aggregate(total=Sum("cost_used"))["total"] or Decimal("0.00")
        self.part_cost = money(Decimal(self.manual_part_cost or 0) + linked_cost)
        self.calculate()
        RepairFinance.objects.filter(pk=self.pk).update(
            part_cost=self.part_cost, repair_margin=self.repair_margin,
            master_salary=self.master_salary, workshop_profit=self.workshop_profit,
        )


class ExpenseCategory(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    is_active = models.BooleanField("Активна", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Категория расходов"
        verbose_name_plural = "Категории расходов"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Expense(models.Model):
    date = models.DateField("Дата")
    amount = models.DecimalField(
        "Сумма", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    category = models.ForeignKey(
        ExpenseCategory, verbose_name="Категория", on_delete=models.PROTECT,
        related_name="expenses",
    )
    description = models.CharField("Описание", max_length=255)
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Расход"
        verbose_name_plural = "Расходы"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["category", "date"])]

    def __str__(self):
        return f"{self.date:%d.%m.%Y} — {self.description}"


class OtherIncome(models.Model):
    date = models.DateField("Дата")
    amount = models.DecimalField(
        "Сумма", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    description = models.CharField("Категория / описание", max_length=255)
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Дополнительный доход"
        verbose_name_plural = "Дополнительные доходы"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.date:%d.%m.%Y} — {self.description}"


class SalaryPayment(models.Model):
    employee = models.ForeignKey(
        Employee, verbose_name="Мастер", on_delete=models.PROTECT,
        related_name="salary_payments",
        limit_choices_to={"is_owner": False},
    )
    date = models.DateField("Дата")
    amount = models.DecimalField(
        "Сумма", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Выплата зарплаты"
        verbose_name_plural = "Выплаты зарплаты"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["employee", "date"])]

    def clean(self):
        super().clean()
        if self.employee_id and self.employee.is_owner:
            raise ValidationError({"employee": "Владельцу зарплата не начисляется."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.amount} BYN"


class Supplier(models.Model):
    name = models.CharField("Название", max_length=160, unique=True)
    contact_person = models.CharField("Контактное лицо", max_length=120, blank=True)
    phone = models.CharField("Телефон", max_length=40, blank=True)
    messenger = models.CharField("Telegram / другой контакт", max_length=120, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Поставщик"
        verbose_name_plural = "Поставщики"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PartCatalog(models.Model):
    brand = models.CharField("Бренд", max_length=80, blank=True)
    device_model = models.CharField("Модель устройства", max_length=120, blank=True)
    name = models.CharField("Название запчасти", max_length=160)
    comment = models.TextField("Комментарий", blank=True)
    is_active = models.BooleanField("Активна", default=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Наименование запчасти"
        verbose_name_plural = "Каталог запчастей"
        ordering = ["brand", "device_model", "name"]
        constraints = [models.UniqueConstraint(fields=["brand", "device_model", "name"], name="uniq_finance_part_catalog")]
        indexes = [models.Index(fields=["brand", "device_model"])]

    def __str__(self):
        return " — ".join(filter(None, [self.brand, self.device_model, self.name]))


class StockReceipt(models.Model):
    date = models.DateField("Дата поступления")
    supplier = models.ForeignKey(Supplier, verbose_name="Поставщик", on_delete=models.PROTECT, related_name="receipts")
    part = models.ForeignKey(PartCatalog, verbose_name="Запчасть", on_delete=models.PROTECT, related_name="receipts")
    quantity = models.PositiveIntegerField("Количество", validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField("Цена за единицу", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    order_number = models.CharField("Номер заказа / накладной", max_length=120, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Поступление запчастей"
        verbose_name_plural = "Поступления запчастей"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date", "supplier"])]

    @property
    def total_cost(self):
        return money(self.quantity * self.unit_cost)

    def clean(self):
        super().clean()
        if self.pk and self.quantity < self.items.count():
            raise ValidationError({"quantity": "Нельзя уменьшить количество: экземпляры уже созданы."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            missing = self.quantity - self.items.count()
            if missing > 0:
                PartItem.objects.bulk_create([PartItem(receipt=self, status=PartItem.Status.IN_STOCK) for _ in range(missing)])

    def __str__(self):
        return f"{self.date:%d.%m.%Y} — {self.part} × {self.quantity}"


class PartItem(models.Model):
    class Status(models.TextChoices):
        IN_STOCK = "in_stock", "На складе"
        INSTALLED = "installed", "Установлена"
        RETURNED = "returned", "Возврат поставщику"
        WARRANTY = "warranty", "Гарантийный случай"
        WRITTEN_OFF = "written_off", "Списана"

    receipt = models.ForeignKey(StockReceipt, verbose_name="Поступление", on_delete=models.PROTECT, related_name="items")
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.IN_STOCK, db_index=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Экземпляр запчасти"
        verbose_name_plural = "Экземпляры запчастей"
        ordering = ["-id"]
        indexes = [models.Index(fields=["status", "receipt"])]

    @property
    def inventory_code(self):
        return f"P{self.pk:06d}" if self.pk else "P------"

    @property
    def unit_cost(self):
        return self.receipt.unit_cost

    def __str__(self):
        return f"#{self.inventory_code} · {self.receipt.part}"


class RepairPart(models.Model):
    repair = models.ForeignKey(RepairFinance, verbose_name="Ремонт", on_delete=models.PROTECT, related_name="repair_parts")
    part_item = models.OneToOneField(PartItem, verbose_name="Экземпляр запчасти", on_delete=models.PROTECT, related_name="repair_usage")
    cost_used = models.DecimalField("Себестоимость в ремонте", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    installed_at = models.DateField("Дата установки")
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Запчасть в ремонте"
        verbose_name_plural = "Запчасти в ремонтах"
        ordering = ["installed_at", "id"]

    def clean(self):
        super().clean()
        if self.part_item_id:
            existing = RepairPart.objects.filter(part_item_id=self.part_item_id).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError({"part_item": "Эта запчасть уже используется в другом ремонте."})
            if not self.pk and self.part_item.status != PartItem.Status.IN_STOCK:
                raise ValidationError({"part_item": "Можно установить только запчасть со статусом «На складе»."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        PartItem.objects.filter(pk=self.part_item_id).update(status=PartItem.Status.INSTALLED)
        self.repair.recalculate_part_cost()

    def delete(self, *args, **kwargs):
        repair, item_id = self.repair, self.part_item_id
        result = super().delete(*args, **kwargs)
        PartItem.objects.filter(pk=item_id).update(status=PartItem.Status.IN_STOCK)
        repair.recalculate_part_cost()
        return result

    def __str__(self):
        return f"{self.repair} · {self.part_item}"


class WarrantyClaim(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        INSPECTION = "inspection", "На проверке"
        SENT = "sent", "Отправлен поставщику"
        APPROVED = "approved", "Поставщик подтвердил гарантию"
        REJECTED = "rejected", "Поставщик отказал"
        REPLACED = "replaced", "Заменена"
        REFUNDED = "refunded", "Возврат денег"
        CLOSED = "closed", "Закрыт"

    part_item = models.ForeignKey(PartItem, verbose_name="Запчасть", on_delete=models.PROTECT, related_name="warranty_claims")
    opened_at = models.DateField("Дата обращения")
    reason = models.CharField("Причина", max_length=200)
    defect_description = models.TextField("Описание дефекта")
    inspection_result = models.TextField("Результат проверки", blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    comment = models.TextField("Комментарий", blank=True)
    sent_to_supplier_at = models.DateField("Дата отправки поставщику", null=True, blank=True)
    supplier_decision = models.TextField("Решение поставщика", blank=True)
    closed_at = models.DateField("Дата закрытия", null=True, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Гарантийный случай"
        verbose_name_plural = "Гарантийные случаи"
        ordering = ["-opened_at", "-created_at"]

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        PartItem.objects.filter(pk=self.part_item_id).update(status=PartItem.Status.WARRANTY)

    def __str__(self):
        return f"#{self.part_item.inventory_code} — {self.reason}"


class SupplierPayment(models.Model):
    supplier = models.ForeignKey(Supplier, verbose_name="Поставщик", on_delete=models.PROTECT, related_name="payments")
    receipt = models.ForeignKey(StockReceipt, verbose_name="Поступление", on_delete=models.PROTECT, related_name="payments", null=True, blank=True)
    date = models.DateField("Дата оплаты")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Оплата поставщику"
        verbose_name_plural = "Оплаты поставщикам"
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["supplier", "date"])]

    def clean(self):
        super().clean()
        if self.receipt_id and self.receipt.supplier_id != self.supplier_id:
            raise ValidationError({"receipt": "Поступление относится к другому поставщику."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.supplier} — {self.amount} BYN"


class DistributedExpense(models.Model):
    name = models.CharField("Название", max_length=200)
    category = models.ForeignKey(ExpenseCategory, verbose_name="Категория", on_delete=models.PROTECT, related_name="distributed_expenses")
    source_expense = models.OneToOneField(Expense, verbose_name="Связанный реальный расход", on_delete=models.PROTECT, related_name="distribution", null=True, blank=True)
    employees = models.ManyToManyField(Employee, verbose_name="Мастера", related_name="distributed_expenses", blank=True, limit_choices_to={"is_owner": False})
    start_date = models.DateField("Дата начала распределения")
    total_cost = models.DecimalField("Общая стоимость", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    periods_count = models.PositiveIntegerField("Количество расчётных периодов", validators=[MinValueValidator(1)])
    comment = models.TextField("Комментарий", blank=True)
    is_active = models.BooleanField("Активен", default=True)
    finished_at = models.DateField("Завершён", null=True, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Распределяемый расход"
        verbose_name_plural = "Распределяемые расходы"
        ordering = ["-start_date", "name"]

    @property
    def per_period_amount(self):
        return money(Decimal(self.total_cost) / self.periods_count)

    @property
    def used_periods(self):
        return self.allocations.values("calculation__period_id").distinct().count()

    @property
    def distributed_amount(self):
        return money(self.allocations.aggregate(v=Sum("amount"))["v"] or 0)

    @property
    def remaining_amount(self):
        return money(max(Decimal(self.total_cost) - self.distributed_amount, Decimal("0.00")))

    def delete(self, *args, **kwargs):
        if self.allocations.exists():
            raise ValidationError("Распределение уже участвовало в расчётах. Его можно только завершить.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.name


class PayrollPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        CLOSED = "closed", "Закрыт"

    start_date = models.DateField("Начало периода")
    end_date = models.DateField("Окончание периода")
    status = models.CharField("Статус", max_length=12, choices=Status.choices, default=Status.OPEN)
    calculated_at = models.DateTimeField("Дата расчёта", null=True, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Расчётный период"
        verbose_name_plural = "Расчётные периоды"
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["start_date", "end_date"], name="uniq_payroll_period_dates")]

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "Конец периода не может быть раньше начала."})
        if self.pk and PayrollPeriod.objects.filter(pk=self.pk, status=self.Status.CLOSED).exists():
            old = PayrollPeriod.objects.get(pk=self.pk)
            if old.start_date != self.start_date or old.end_date != self.end_date:
                raise ValidationError("Даты закрытого периода нельзя изменять.")

    def __str__(self):
        return f"{self.start_date:%d.%m.%Y} — {self.end_date:%d.%m.%Y}"


class PayrollCalculation(models.Model):
    period = models.ForeignKey(PayrollPeriod, verbose_name="Период", on_delete=models.PROTECT, related_name="calculations")
    employee = models.ForeignKey(Employee, verbose_name="Мастер", on_delete=models.PROTECT, related_name="payroll_calculations")
    repairs_count = models.PositiveIntegerField("Ремонтов")
    revenue = models.DecimalField("Выручка", max_digits=12, decimal_places=2)
    direct_costs = models.DecimalField("Прямые затраты", max_digits=12, decimal_places=2)
    repair_margin = models.DecimalField("Маржа", max_digits=12, decimal_places=2)
    distributed_costs = models.DecimalField("Распределяемые расходы", max_digits=12, decimal_places=2)
    salary_base = models.DecimalField("База зарплаты", max_digits=12, decimal_places=2)
    percent = models.DecimalField("Процент", max_digits=5, decimal_places=2)
    salary_amount = models.DecimalField("К выплате", max_digits=12, decimal_places=2)
    created_at = models.DateTimeField("Рассчитан", auto_now_add=True)

    class Meta:
        verbose_name = "Расчёт зарплаты"
        verbose_name_plural = "Расчёты зарплаты"
        constraints = [models.UniqueConstraint(fields=["period", "employee"], name="uniq_period_employee_payroll")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Закрытый snapshot расчёта нельзя изменять.")
        super().save(*args, **kwargs)


class DistributedExpenseAllocation(models.Model):
    calculation = models.ForeignKey(PayrollCalculation, verbose_name="Расчёт", on_delete=models.PROTECT, related_name="allocations")
    distributed_expense = models.ForeignKey(DistributedExpense, verbose_name="Распределяемый расход", on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    name_snapshot = models.CharField("Название на момент расчёта", max_length=200, blank=True)
    total_cost_snapshot = models.DecimalField("Полная стоимость на момент расчёта", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    part_number = models.PositiveIntegerField("Номер части", default=1)
    parts_count = models.PositiveIntegerField("Всего частей", default=1)
    accounted_after = models.DecimalField("Учтено после расчёта", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    remaining_after = models.DecimalField("Остаток после расчёта", max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        verbose_name = "Списание распределяемого расхода"
        verbose_name_plural = "Списания распределяемых расходов"
        constraints = [models.UniqueConstraint(fields=["calculation", "distributed_expense"], name="uniq_payroll_distribution")]

    @property
    def progress_percent(self):
        return min(round(self.part_number / self.parts_count * 100), 100) if self.parts_count else 0

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Snapshot распределения в закрытом расчёте нельзя изменять.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Snapshot распределения в закрытом расчёте нельзя удалить.")


class PayrollRepairSnapshot(models.Model):
    calculation = models.ForeignKey(
        PayrollCalculation, verbose_name="Расчёт", on_delete=models.PROTECT,
        related_name="repair_snapshots",
    )
    repair = models.ForeignKey(
        RepairFinance, verbose_name="Исходный ремонт", on_delete=models.SET_NULL,
        related_name="payroll_snapshots", null=True, blank=True,
    )
    repair_date = models.DateField("Дата ремонта")
    description = models.CharField("Описание ремонта", max_length=255)
    revenue = models.DecimalField("Сумма ремонта", max_digits=12, decimal_places=2)
    parts_cost = models.DecimalField("Запчасти", max_digits=12, decimal_places=2)
    other_direct_costs = models.DecimalField("Другие прямые затраты", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    salary_base = models.DecimalField("База", max_digits=12, decimal_places=2)
    percent = models.DecimalField("Процент мастера", max_digits=5, decimal_places=2)
    salary_amount = models.DecimalField("Начислено", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "Ремонт в закрытом расчёте зарплаты"
        verbose_name_plural = "Ремонты в закрытых расчётах зарплаты"
        ordering = ["repair_date", "id"]
        constraints = [models.UniqueConstraint(fields=["calculation", "repair"], name="uniq_payroll_repair_snapshot")]

    @property
    def direct_costs(self):
        return money(self.parts_cost + self.other_direct_costs)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Snapshot ремонта в закрытом расчёте нельзя изменять.")
        super().save(*args, **kwargs)
