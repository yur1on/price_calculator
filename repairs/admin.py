"""Админка приложения repairs (на русском)."""
from django.contrib import admin, messages
from django.utils import timezone
from django.db.models import Sum
from django.utils.html import format_html  # ← для превью логотипа

from .models import (
    PhoneBrand, PhoneModel, RepairType, ModelRepairPrice,
    ReferralPartner, ReferralRedemption,
    Technician, WorkingHour, TimeOff, Appointment,
)

# Русские заголовки панели администрирования
admin.site.site_header = "Мастерская — панель администратора"
admin.site.site_title = "Админка Мастерской"
admin.site.index_title = "Управление данными и заказами"


@admin.register(PhoneBrand)
class PhoneBrandAdmin(admin.ModelAdmin):
    list_display = ("logo_thumb", "name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def logo_thumb(self, obj: PhoneBrand):
        if obj.logo:
            return format_html('<img src="{}" style="height:28px;border-radius:6px;background:#fff;padding:2px">', obj.logo.url)
        return "—"
    logo_thumb.short_description = "Логотип"


@admin.register(PhoneModel)
class PhoneModelAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "category", "slug")
    list_filter = ("brand", "category")
    search_fields = ("name", "brand__name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(RepairType)
class RepairTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "default_duration_min", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ModelRepairPrice)
class ModelRepairPriceAdmin(admin.ModelAdmin):
    list_display = ("phone_model", "repair_type", "price", "duration_min", "is_active")
    list_filter = ("phone_model__brand", "repair_type", "is_active")
    search_fields = ("phone_model__name", "repair_type__name")


@admin.register(ReferralPartner)
class ReferralPartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "client_discount_pct", "partner_commission_pct", "expires_at", "max_uses")
    search_fields = ("name", "code")


@admin.register(ReferralRedemption)
class ReferralRedemptionAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("created_at", "partner", "appointment", "phone",
                    "discount_amount", "commission_amount", "status", "paid_at")
    list_filter = ("partner", "status", "created_at")
    search_fields = ("partner__name", "partner__code", "phone", "appointment__customer_name")
    actions = ("mark_as_paid", "mark_as_unpaid", "show_totals")

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


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = ("name",)
    filter_horizontal = ("skills",)


@admin.register(WorkingHour)
class WorkingHourAdmin(admin.ModelAdmin):
    list_display = ("weekday", "start", "end")
    list_filter = ("weekday",)


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    list_display = ("technician", "start", "end", "reason")
    list_filter = ("technician",)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name", "customer_phone",
        "phone_model", "repair_type",
        "start", "end", "status",
        "price_original", "discount_amount", "price_final",
    )
    list_filter = ("status", "phone_model__brand", "repair_type")
    search_fields = ("customer_name", "customer_phone")
