# repairs/admin.py
from __future__ import annotations

import re
from django.contrib import admin, messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from unfold.admin import ModelAdmin  # базовый класс от Unfold

from .models import (
    PhoneBrand, PhoneModel, RepairType, ModelRepairPrice,
    ReferralPartner, ReferralRedemption,
    Technician, WorkingHour, TimeOff, Appointment,
)

# -------------------------------------------------------------------
# Заголовки админки (по желанию)
# -------------------------------------------------------------------
admin.site.site_header = "Мастерская — панель администратора"
admin.site.site_title = "Админка Мастерской"
admin.site.index_title = "Управление данными и заказами"

# -------------------------------------------------------------------
# Утилита: убираем фрагменты " (....)" в строках
# -------------------------------------------------------------------
_PAR_RE = re.compile(r"\s*\([^)]*\)")

def strip_parens_text(s: str) -> str:
    return _PAR_RE.sub("", s or "").strip()


# -------------------------------------------------------------------
# Mixin: меняем label у FK(phone_model), скрывая текст в скобках
# -------------------------------------------------------------------
class StripPhoneModelLabelsMixin:
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if field is not None and db_field.name == "phone_model":
            field.label_from_instance = lambda m: strip_parens_text(getattr(m, "name", ""))
        return field


# -------------------------------------------------------------------
# Инлайны
# -------------------------------------------------------------------
class ModelRepairPriceInline(admin.TabularInline):
    model = ModelRepairPrice
    extra = 0
    autocomplete_fields = ("repair_type",)
    fields = ("repair_type", "price", "duration_min", "is_active")
    show_change_link = True


# -------------------------------------------------------------------
# Бренды
# -------------------------------------------------------------------
@admin.register(PhoneBrand)
class PhoneBrandAdmin(ModelAdmin):
    list_display = ("logo_thumb", "name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("name",)

    @admin.display(description="Логотип")
    def logo_thumb(self, obj: PhoneBrand):
        if getattr(obj, "logo", None):
            try:
                return format_html(
                    '<img src="{}" style="height:28px;border-radius:6px;background:#fff;padding:2px">',
                    obj.logo.url,
                )
            except Exception:
                return "—"
        return "—"


# -------------------------------------------------------------------
# Модели устройств
# -------------------------------------------------------------------
@admin.register(PhoneModel)
class PhoneModelAdmin(ModelAdmin):
    list_display = ("name_no_parens", "brand", "category", "slug")
    list_filter = ("brand", "category")
    search_fields = ("name", "brand__name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("brand__name", "category", "name")
    inlines = (ModelRepairPriceInline,)
    list_select_related = ("brand",)

    @admin.display(ordering="name", description="Название")
    def name_no_parens(self, obj: PhoneModel):
        return strip_parens_text(obj.name)


# -------------------------------------------------------------------
# Типы ремонта
# -------------------------------------------------------------------
@admin.register(RepairType)
class RepairTypeAdmin(ModelAdmin):
    list_display = ("name", "default_duration_min", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    ordering = ("name",)


# -------------------------------------------------------------------
# Цены на ремонт по моделям
# -------------------------------------------------------------------
@admin.register(ModelRepairPrice)
class ModelRepairPriceAdmin(StripPhoneModelLabelsMixin, ModelAdmin):
    list_display = ("phone_model_no_parens", "repair_type", "price", "duration_min", "is_active")
    list_filter = ("phone_model__brand", "repair_type", "is_active")
    search_fields = ("phone_model__name", "repair_type__name")
    list_select_related = ("phone_model", "phone_model__brand", "repair_type")
    ordering = ("phone_model__brand__name", "phone_model__name", "repair_type__name")
    autocomplete_fields = ("phone_model", "repair_type")

    @admin.display(ordering="phone_model__name", description="Модель")
    def phone_model_no_parens(self, obj: ModelRepairPrice):
        return strip_parens_text(getattr(obj.phone_model, "name", ""))


# -------------------------------------------------------------------
# Реферальные партнёры
# -------------------------------------------------------------------
@admin.register(ReferralPartner)
class ReferralPartnerAdmin(ModelAdmin):
    list_display = ("name", "code", "client_discount_pct", "partner_commission_pct", "expires_at", "max_uses")
    search_fields = ("name", "code")
    ordering = ("name",)


# -------------------------------------------------------------------
# Начисления по рефералам
# -------------------------------------------------------------------
@admin.register(ReferralRedemption)
class ReferralRedemptionAdmin(ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "created_at", "partner", "appointment", "phone",
        "discount_amount", "commission_amount", "status_badge", "paid_at",
    )
    list_filter = ("partner", "status", "created_at")
    search_fields = ("partner__name", "partner__code", "phone", "appointment__customer_name")
    actions = ("mark_as_paid", "mark_as_unpaid", "show_totals")
    list_select_related = ("partner", "appointment", "appointment__phone_model", "appointment__repair_type")
    list_per_page = 50
    autocomplete_fields = ("partner", "appointment")
    ordering = ("-created_at",)

    @admin.display(description="Статус")
    def status_badge(self, obj: 'ReferralRedemption'):
        colors = {
            "pending": "#f59e0b",   # amber
            "accrued": "#10b981",   # emerald
            "paid": "#3b82f6",      # blue
        }
        color = colors.get(obj.status, "#6b7280")
        text = obj.get_status_display()
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            'background:{}20;color:{};border:1px solid {}33;font-size:12px">{}</span>',
            color, color, color, text
        )

    # --- действия ---
    @admin.action(description="Отметить как выплачено")
    def mark_as_paid(self, request, queryset):
        updated = 0
        for r in queryset.exclude(status="paid"):
            r.status = "paid"
            if not r.paid_at:
                r.paid_at = timezone.now()
            r.save(update_fields=["status", "paid_at"])
            updated += 1
        self.message_user(request, f"Отмечено выплаченными: {updated}", level=messages.SUCCESS)

    @admin.action(description="Снять отметку о выплате")
    def mark_as_unpaid(self, request, queryset):
        updated = queryset.filter(status="paid").update(status="accrued", paid_at=None)
        self.message_user(request, f"Снято отметок: {updated}", level=messages.SUCCESS)

    @admin.action(description="Показать итоги по выборке")
    def show_totals(self, request, queryset):
        agg = queryset.aggregate(
            total_discount=Sum("discount_amount"),
            total_commission=Sum("commission_amount"),
        )
        self.message_user(
            request,
            f"ИТОГО: скидка {agg['total_discount'] or 0} BYN • комиссия {agg['total_commission'] or 0} BYN",
            level=messages.INFO,
        )


# -------------------------------------------------------------------
# Персонал и расписание
# -------------------------------------------------------------------
@admin.register(Technician)
class TechnicianAdmin(ModelAdmin):
    list_display = ("name",)
    filter_horizontal = ("skills",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(WorkingHour)
class WorkingHourAdmin(ModelAdmin):
    list_display = ("weekday", "start", "end")
    list_filter = ("weekday",)
    ordering = ("weekday", "start")


@admin.register(TimeOff)
class TimeOffAdmin(ModelAdmin):
    list_display = ("technician", "start", "end", "reason")
    list_filter = ("technician",)
    list_select_related = ("technician",)
    ordering = ("-start",)


# -------------------------------------------------------------------
# Записи (Appointment) — печатные формы + бейджи статусов
# -------------------------------------------------------------------
@admin.register(Appointment)
class AppointmentAdmin(StripPhoneModelLabelsMixin, ModelAdmin):
    """
    При смене статуса:
      - new -> confirmed: ссылки на печать двух квитанций
      - любой -> done: ссылка на печать гарантийного талона
    Также кнопки печати показываются в карточке (change_form_template).
    """
    change_form_template = "admin/repairs/appointment/change_form.html"

    date_hierarchy = "start"
    list_display = (
        "customer_name", "customer_phone",
        "phone_model_no_parens", "repair_type",
        "start", "end", "status_badge",
        "price_original", "discount_amount", "price_final",
    )
    list_filter = ("status", "phone_model__brand", "repair_type")
    search_fields = ("customer_name", "customer_phone", "referral_code", "phone_model__name")
    list_select_related = ("phone_model", "phone_model__brand", "repair_type", "technician")
    autocomplete_fields = ("phone_model", "repair_type", "technician")
    ordering = ("-start",)
    readonly_fields = ("created_at",)

    fieldsets = (
        ("Клиент", {"fields": ("customer_name", "customer_phone", "referral_code", "status")}),
        ("Устройство и услуга", {"fields": ("phone_model", "repair_type", "technician")}),
        ("Время", {"fields": ("start", "end")}),
        ("Оплата", {"fields": ("price_original", "discount_amount", "price_final")}),
        ("Служебное", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    # ----- собственные урлы для печати -----
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("<int:pk>/print/receipt/<str:variant>/",
                 self.admin_site.admin_view(self.print_receipt),
                 name="repairs_appointment_receipt"),
            path("<int:pk>/print/warranty/",
                 self.admin_site.admin_view(self.print_warranty),
                 name="repairs_appointment_warranty"),
        ]
        return my + urls

    # ----- view: печать квитанций -----
    def print_receipt(self, request, pk: int, variant: str):
        """
        variant: 'client' | 'shop'
        """
        obj = get_object_or_404(Appointment, pk=pk)
        variant = "client" if variant not in {"client", "shop"} else variant
        return render(request, "admin/repairs/print_receipt.html", {
            "appointment": obj,
            "variant": variant,
        })

    # ----- view: печать гарантийного талона -----
    def print_warranty(self, request, pk: int):
        obj = get_object_or_404(Appointment, pk=pk)
        return render(request, "admin/repairs/print_warranty.html", {
            "appointment": obj,
        })

    # ----- вспомогательные колонки -----
    @admin.display(ordering="phone_model__name", description="Модель")
    def phone_model_no_parens(self, obj: Appointment):
        return strip_parens_text(getattr(obj.phone_model, "name", ""))

    @admin.display(description="Статус")
    def status_badge(self, obj: Appointment):
        colors = {
            "new": "#6366f1",        # indigo
            "confirmed": "#0ea5e9",  # sky
            "done": "#10b981",       # emerald
            "cancelled": "#ef4444",  # red
        }
        color = colors.get(obj.status, "#6b7280")
        text = obj.get_status_display()
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            'background:{}20;color:{};border:1px solid {}33;font-size:12px">{}</span>',
            color, color, color, text
        )

    # ----- уведомления при смене статуса -----
    def save_model(self, request, obj, form, change):
        old_status = None
        if change:
            try:
                old_status = Appointment.objects.only("status").get(pk=obj.pk).status
            except Appointment.DoesNotExist:
                pass

        super().save_model(request, obj, form, change)

        if old_status and old_status != obj.status:
            if old_status == "new" and obj.status == "confirmed":
                url_client = reverse("admin:repairs_appointment_receipt", args=[obj.pk, "client"])
                url_shop   = reverse("admin:repairs_appointment_receipt", args=[obj.pk, "shop"])
                self.message_user(
                    request,
                    format_html(
                        'Статус: <b>подтверждена</b>. Распечатать: '
                        '<a class="button" href="{}" target="_blank">Квитанция клиента</a> '
                        '<a class="button" href="{}" target="_blank">Квитанция мастерской</a>',
                        url_client, url_shop
                    ),
                    level=messages.SUCCESS
                )
            if obj.status == "done":
                url_w = reverse("admin:repairs_appointment_warranty", args=[obj.pk])
                self.message_user(
                    request,
                    format_html(
                        'Статус: <b>завершена</b>. Распечатайте: '
                        '<a class="button" href="{}" target="_blank">Гарантийный талон</a>',
                        url_w
                    ),
                    level=messages.SUCCESS
                )
