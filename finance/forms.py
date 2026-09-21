from decimal import Decimal

from django import forms
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth import get_user_model, password_validation

from .models import (
    Employee, Expense, ExpenseCategory, OtherIncome, PartCatalog, PartItem,
    RepairFinance, RepairPart, SalaryPayment, StockReceipt, Supplier,
    SupplierPayment, WarrantyClaim,
    DistributedExpense, PayrollPeriod,
)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "finance-input")


class RepairFinanceForm(StyledModelForm):
    manual_part_cost = forms.DecimalField(
        label="Дополнительная стоимость вне склада",
        min_value=0,
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Необязательно. Оставьте пустым, если все запчасти выбраны со склада.",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
    )
    master_percent = forms.DecimalField(
        label="Процент исполнителя", min_value=0, max_value=100,
        max_digits=5, decimal_places=2, required=False,
    )
    part_items = forms.ModelMultipleChoiceField(
        label="Запчасти со склада", queryset=PartItem.objects.none(), required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Можно выбрать несколько экземпляров. Их закупочная стоимость добавится автоматически.",
    )

    class Meta:
        model = RepairFinance
        fields = ["date", "description", "revenue", "manual_part_cost", "part_items", "employee", "master_percent", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True) | Employee.objects.filter(pk=getattr(self.instance, "employee_id", None))
        available = PartItem.objects.filter(status=PartItem.Status.IN_STOCK).select_related("receipt__part", "receipt__supplier")
        if self.instance.pk:
            current_ids = self.instance.repair_parts.values_list("part_item_id", flat=True)
            available = PartItem.objects.filter(Q(status=PartItem.Status.IN_STOCK) | Q(pk__in=current_ids)).select_related("receipt__part", "receipt__supplier")
            self.fields["part_items"].initial = current_ids
        self.fields["part_items"].queryset = available.order_by("receipt__part__brand", "receipt__part__device_model", "id")
        self.fields["part_items"].label_from_instance = lambda item: f"#{item.inventory_code} · {item.receipt.part} · {item.receipt.supplier} · {item.unit_cost:.2f} BYN"
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        cleaned["manual_part_cost"] = cleaned.get("manual_part_cost") or Decimal("0.00")
        employee = cleaned.get("employee")
        if employee and employee.is_owner:
            cleaned["master_percent"] = Decimal("0.00")
        elif employee and cleaned.get("master_percent") is None:
            cleaned["master_percent"] = employee.default_percent
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        repair = super().save(commit=True)
        selected = set(self.cleaned_data.get("part_items", []).values_list("pk", flat=True))
        existing = {row.part_item_id: row for row in repair.repair_parts.select_related("part_item")}
        for item_id, row in existing.items():
            if item_id not in selected:
                row.delete()
        for item in PartItem.objects.select_for_update().filter(pk__in=selected):
            if item.pk not in existing:
                RepairPart.objects.create(repair=repair, part_item=item, cost_used=item.unit_cost, installed_at=repair.date)
        repair.recalculate_part_cost()
        repair.refresh_from_db()
        return repair


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["date", "category", "description", "amount", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True) | ExpenseCategory.objects.filter(pk=getattr(self.instance, "category_id", None))
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()


class OtherIncomeForm(StyledModelForm):
    class Meta:
        model = OtherIncome
        fields = ["date", "description", "amount", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()


class SalaryPaymentForm(StyledModelForm):
    class Meta:
        model = SalaryPayment
        fields = ["date", "employee", "amount", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True, is_owner=False)
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()


class EmployeeForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["name", "default_percent", "is_owner", "is_active"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_owner"):
            cleaned["default_percent"] = Decimal("0.00")
        return cleaned


class MasterAccessCreateForm(StyledModelForm):
    password1 = forms.CharField(label="Пароль", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Повторите пароль", strip=False, widget=forms.PasswordInput)

    class Meta:
        model = get_user_model()
        fields = ["username"]
        labels = {"username": "Логин"}

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password1")
        if password and password != cleaned.get("password2"):
            self.add_error("password2", "Пароли не совпадают.")
        if password:
            password_validation.validate_password(password, self.instance)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class MasterPasswordForm(forms.Form):
    password1 = forms.CharField(label="Новый пароль", strip=False, widget=forms.PasswordInput(attrs={"class": "finance-input"}))
    password2 = forms.CharField(label="Повторите пароль", strip=False, widget=forms.PasswordInput(attrs={"class": "finance-input"}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password1")
        if password and password != cleaned.get("password2"):
            self.add_error("password2", "Пароли не совпадают.")
        if password:
            password_validation.validate_password(password, self.user)
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["password1"])
        self.user.save(update_fields=["password"])
        return self.user


class ExpenseCategoryForm(StyledModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["name", "is_active", "sort_order"]


class SupplierForm(StyledModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "messenger", "comment", "is_active"]
        widgets = {"comment": forms.Textarea(attrs={"rows": 3})}


class PartCatalogForm(StyledModelForm):
    class Meta:
        model = PartCatalog
        fields = ["brand", "device_model", "name", "comment", "is_active"]
        widgets = {"comment": forms.Textarea(attrs={"rows": 3})}


class StockReceiptForm(StyledModelForm):
    part_name = forms.CharField(
        label="Запчасть",
        max_length=360,
        help_text="Введите вручную, например: Samsung — A60 — дисплей.",
        widget=forms.TextInput(attrs={"placeholder": "Samsung — A60 — дисплей", "autocomplete": "off"}),
    )

    class Meta:
        model = StockReceipt
        fields = ["date", "supplier", "part_name", "quantity", "unit_cost", "order_number", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True) | Supplier.objects.filter(pk=getattr(self.instance, "supplier_id", None))
        if self.instance.pk and self.instance.part_id:
            self.fields["part_name"].initial = str(self.instance.part)
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()

    def clean_part_name(self):
        value = " ".join((self.cleaned_data["part_name"] or "").split()).strip(" —-")
        if not value:
            raise forms.ValidationError("Укажите запчасть.")
        return value

    def save(self, commit=True):
        value = self.cleaned_data["part_name"]
        chunks = [chunk.strip() for chunk in value.replace("–", "—").split("—") if chunk.strip()]
        if len(chunks) >= 3:
            brand, device_model, name = chunks[0], chunks[1], " — ".join(chunks[2:])
        else:
            brand, device_model, name = "", "", value
        part, _ = PartCatalog.objects.get_or_create(
            brand__iexact=brand,
            device_model__iexact=device_model,
            name__iexact=name,
            defaults={"brand": brand, "device_model": device_model, "name": name},
        )
        self.instance.part = part
        return super().save(commit=commit)


class SupplierPaymentForm(StyledModelForm):
    class Meta:
        model = SupplierPayment
        fields = ["date", "supplier", "receipt", "amount", "comment"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}), "comment": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True) | Supplier.objects.filter(pk=getattr(self.instance, "supplier_id", None))
        if not self.is_bound and not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()


class WarrantyClaimForm(StyledModelForm):
    class Meta:
        model = WarrantyClaim
        fields = ["part_item", "opened_at", "reason", "defect_description", "inspection_result", "status", "comment", "sent_to_supplier_at", "supplier_decision", "closed_at"]
        widgets = {
            "opened_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "sent_to_supplier_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "closed_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "defect_description": forms.Textarea(attrs={"rows": 3}), "inspection_result": forms.Textarea(attrs={"rows": 3}),
            "comment": forms.Textarea(attrs={"rows": 3}), "supplier_decision": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        eligible = PartItem.objects.filter(Q(status=PartItem.Status.INSTALLED) | Q(status=PartItem.Status.WARRANTY)).select_related("receipt__part")
        self.fields["part_item"].queryset = eligible
        if not self.is_bound and not self.instance.pk:
            self.fields["opened_at"].initial = timezone.localdate()


class DistributedExpenseForm(StyledModelForm):
    class Meta:
        model = DistributedExpense
        fields = ["name", "category", "source_expense", "employees", "start_date", "total_cost", "periods_count", "comment", "is_active"]
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "employees": forms.SelectMultiple(attrs={"size": 6}), "comment": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employees"].queryset = Employee.objects.filter(is_active=True, is_owner=False)
        if not self.is_bound and not self.instance.pk:
            self.fields["start_date"].initial = timezone.localdate()


class PayrollPeriodForm(StyledModelForm):
    class Meta:
        model = PayrollPeriod
        fields = ["start_date", "end_date", "comment"]
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }
