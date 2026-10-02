from decimal import Decimal

from django import forms
from django.utils import timezone

from finance.models import Employee

from .models import (
    CRMAttachment, CRMBrand, CRMClient, CRMComment, CRMDevice, CRMDeviceModel,
    CRMDeviceType, CRMDocumentSettings, CRMIssueTemplate, CRMOrder,
    CRMWarrantyTerm, CRMWorkItem, CRMWorkTemplate,
)
from repairs.models import WorkshopDayCapacity


class StyledFormMixin:
    def style_fields(self):
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.HiddenInput, forms.RadioSelect, forms.CheckboxInput)):
                field.widget.attrs.setdefault("class", "crm-input")


class PublicRepairStatusForm(forms.Form):
    order_number = forms.CharField(label="Номер ремонта", max_length=20)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order_number"].widget.attrs.update({
            "autocomplete": "off", "placeholder": "Например, R7K2M9P4A",
            "aria-describedby": "tracking-help", "spellcheck": "false", "autocapitalize": "characters",
        })

    def clean_order_number(self):
        return self.cleaned_data["order_number"].strip().upper()


class IntakeForm(StyledFormMixin, forms.Form):
    existing_client = forms.ModelChoiceField(CRMClient.objects.none(), required=False, widget=forms.HiddenInput())
    existing_device = forms.ModelChoiceField(CRMDevice.objects.none(), required=False, widget=forms.HiddenInput())
    client_name = forms.CharField(label="Имя клиента", max_length=160, required=False)
    phone = forms.CharField(label="Телефон", max_length=40, required=False)
    additional_phone = forms.CharField(label="Дополнительный телефон", max_length=40, required=False)
    email = forms.EmailField(label="Email", required=False)
    source = forms.CharField(label="Откуда узнал", max_length=120, required=False)
    city = forms.CharField(label="Город", max_length=120, required=False)
    client_note = forms.CharField(label="Заметка о клиенте", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    device_type = forms.CharField(label="Тип устройства", max_length=80, required=False)
    brand = forms.CharField(label="Бренд", max_length=100, required=False)
    device_model = forms.CharField(label="Модель", max_length=140, required=False)
    imei = forms.CharField(label="IMEI", max_length=32, required=False)
    serial_number = forms.CharField(label="Серийный номер", max_length=120, required=False)
    color = forms.CharField(label="Цвет", max_length=80, required=False)
    condition = forms.CharField(label="Состояние при приёме", required=False, widget=forms.Textarea(attrs={"rows": 2, "data-chip-target": "condition"}))
    included_items = forms.CharField(label="Комплектность", required=False, widget=forms.Textarea(attrs={"rows": 2, "data-chip-target": "accessories"}))
    unlock_code = forms.CharField(label="Код устройства", max_length=255, required=False, widget=forms.PasswordInput(render_value=True))
    device_note = forms.CharField(label="Заметка об устройстве", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    issue_template = forms.ModelChoiceField(CRMIssueTemplate.objects.none(), label="Шаблон неисправности", required=False)
    issue_description = forms.CharField(label="Причина обращения", widget=forms.Textarea(attrs={"rows": 3}))
    internal_note = forms.CharField(label="Внутренняя заметка", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    employee = forms.ModelChoiceField(Employee.objects.none(), label="Мастер", required=False)
    order_type = forms.ChoiceField(label="Тип заказа", choices=CRMOrder.Type.choices)
    accepted_at = forms.DateTimeField(label="Дата и время приёмки", required=False, input_formats=["%Y-%m-%dT%H:%M"], widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    agreed_price = forms.DecimalField(label="Согласованная стоимость", min_value=0, decimal_places=2, initial=0)
    estimated_work_minutes_snapshot = forms.IntegerField(label="Плановое время работы, минут", min_value=0, initial=60, required=False)
    warranty_days = forms.IntegerField(label="Гарантия, дней", min_value=0, initial=0)

    def __init__(self, *args, warranty_source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.warranty_source = warranty_source
        self.fields["existing_client"].queryset = CRMClient.objects.all()
        self.fields["existing_device"].queryset = CRMDevice.objects.select_related("client")
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)
        self.fields["issue_template"].queryset = CRMIssueTemplate.objects.filter(is_active=True)
        self.fields["device_type"].widget.attrs["list"] = "crm-device-types"
        self.fields["brand"].widget.attrs["list"] = "crm-brands"
        self.fields["device_model"].widget.attrs["list"] = "crm-device-models"
        self.fields["brand"].widget.attrs["data-models-url"] = "/crm/device-models/"
        self.fields["warranty_days"].widget.attrs["list"] = "crm-warranty-terms"
        default_term = CRMWarrantyTerm.objects.filter(is_active=True, is_default=True).first()
        if default_term and not self.initial.get("warranty_days"):
            self.initial["warranty_days"] = default_term.days
        self.initial.setdefault("accepted_at", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
        if warranty_source:
            self.initial.update({"existing_client": warranty_source.client_id, "existing_device": warranty_source.device_id, "order_type": CRMOrder.Type.WARRANTY})
        self.style_fields()

    def clean(self):
        data = super().clean()
        client = self.warranty_source.client if self.warranty_source else data.get("existing_client")
        device = self.warranty_source.device if self.warranty_source else data.get("existing_device")
        if not client and (not data.get("client_name") or not data.get("phone")):
            self.add_error("phone", "Выберите клиента или заполните имя и телефон.")
        if device and client and device.client_id != client.id:
            self.add_error("existing_device", "Устройство принадлежит другому клиенту.")
        if not device and not data.get("device_type"):
            self.add_error("device_type", "Выберите устройство или укажите его тип.")
        if self.warranty_source:
            data["order_type"] = CRMOrder.Type.WARRANTY
        if data.get("estimated_work_minutes_snapshot") is None:
            data["estimated_work_minutes_snapshot"] = 60
        return data


class OrderEditForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CRMOrder
        fields = ["employee", "issue_description", "diagnostic_result", "internal_note", "agreed_price", "warranty_days", "estimated_work_minutes_snapshot"]
        widgets = {"issue_description": forms.Textarea(attrs={"rows": 3}), "diagnostic_result": forms.Textarea(attrs={"rows": 4}), "internal_note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)
        self.fields["estimated_work_minutes_snapshot"].required = True
        self.style_fields()


class IssueOrderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CRMOrder
        fields = ["final_price", "paid_amount", "warranty_days"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order = self.instance
        if not order.pk:
            return
        if not self.is_bound:
            if order.order_type == CRMOrder.Type.WARRANTY:
                self.initial.update({"final_price": 0, "paid_amount": 0})
            else:
                suggested_total = order.final_price if order.final_price is not None else (order.agreed_price or None)
                self.initial["final_price"] = suggested_total
                self.initial["paid_amount"] = order.paid_amount or suggested_total
        self.fields["final_price"].required = order.order_type != CRMOrder.Type.WARRANTY
        self.style_fields()

    def clean(self):
        data = super().clean()
        if self.instance.order_type == CRMOrder.Type.WARRANTY:
            data["final_price"] = Decimal("0.00")
            data["paid_amount"] = Decimal("0.00")
            return data
        final_price = data.get("final_price")
        paid_amount = data.get("paid_amount")
        if final_price is None:
            self.add_error("final_price", "Укажите итоговую сумму перед выдачей.")
        elif paid_amount is not None and paid_amount != final_price:
            self.add_error("paid_amount", "Подтверждённая оплата должна совпадать с итоговой суммой.")
        return data


class WorkItemForm(StyledFormMixin, forms.ModelForm):
    template = forms.ModelChoiceField(CRMWorkTemplate.objects.none(), label="Шаблон", required=False)

    class Meta:
        model = CRMWorkItem
        fields = ["name", "quantity", "unit_price", "comment"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = CRMWorkTemplate.objects.filter(is_active=True)
        self.fields["name"].required = False
        self.style_fields()

    def clean(self):
        data = super().clean()
        template = data.get("template")
        if template and not data.get("name"):
            data["name"] = template.name
        if template and not data.get("unit_price"):
            data["unit_price"] = template.default_price
        if not data.get("name"):
            self.add_error("name", "Укажите работу или выберите шаблон.")
        return data


class CommentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CRMComment
        fields = ["text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 3, "placeholder": "Комментарий по ремонту"})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.style_fields()


class AttachmentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CRMAttachment
        fields = ["file", "description"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.style_fields()


class CompactModelForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class ClientForm(CompactModelForm):
    class Meta:
        model = CRMClient
        fields = ["name", "phone", "additional_phone", "email", "source", "city", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class IssueTemplateForm(CompactModelForm):
    class Meta:
        model = CRMIssueTemplate
        fields = ["name", "text", "sort_order", "is_active"]
        widgets = {"text": forms.Textarea(attrs={"rows": 2})}


class WorkTemplateForm(CompactModelForm):
    class Meta:
        model = CRMWorkTemplate
        fields = ["name", "default_price", "description", "sort_order", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class DeviceTypeForm(CompactModelForm):
    class Meta:
        model = CRMDeviceType
        fields = ["name", "sort_order", "is_active"]


class BrandForm(CompactModelForm):
    class Meta:
        model = CRMBrand
        fields = ["name", "sort_order", "is_active"]


class DeviceModelForm(CompactModelForm):
    class Meta:
        model = CRMDeviceModel
        fields = ["brand", "name", "sort_order", "is_active"]


class WarrantyTermForm(CompactModelForm):
    class Meta:
        model = CRMWarrantyTerm
        fields = ["days", "label", "sort_order", "is_default", "is_active"]


class DocumentSettingsForm(CompactModelForm):
    class Meta:
        model = CRMDocumentSettings
        fields = [
            "workshop_name", "phone", "website", "email", "address", "receipt_terms", "act_terms", "warranty_terms",
            "diagnostic_warning_hours", "approval_warning_hours", "waiting_part_warning_days", "ready_warning_days",
        ]
        widgets = {
            "receipt_terms": forms.Textarea(attrs={"rows": 3}),
            "act_terms": forms.Textarea(attrs={"rows": 3}),
            "warranty_terms": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "diagnostic_warning_hours", "approval_warning_hours",
            "waiting_part_warning_days", "ready_warning_days",
        ):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        defaults = {
            "diagnostic_warning_hours": 24, "approval_warning_hours": 24,
            "waiting_part_warning_days": 3, "ready_warning_days": 3,
        }
        for name, default in defaults.items():
            if data.get(name) is None:
                data[name] = getattr(self.instance, name, None) or default
        return data


class WorkshopDayCapacityForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = WorkshopDayCapacity
        fields = ["date", "capacity_minutes", "reserve_minutes", "manual_adjustment_minutes", "is_closed", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()
