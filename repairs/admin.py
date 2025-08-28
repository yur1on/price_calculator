"""Админка приложения repairs (на русском)."""
from django.contrib import admin
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
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PhoneModel)
class PhoneModelAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "category", "slug")
    list_filter = ("brand", "category")
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
    list_display = ("partner", "phone", "appointment", "discount_amount", "commission_amount", "created_at")
    list_filter = ("partner", "created_at")
    search_fields = ("partner__name", "phone")


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
