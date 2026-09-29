from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from .models import AccountProfile, PhoneVerification
from .services import approve_master, reject_master
from .services import ADMIN_GROUP
from django.contrib.auth.models import Group


@admin.action(description="Одобрить выбранных мастеров")
def approve_masters(modeladmin, request, queryset):
    count = 0
    for profile in queryset.filter(role=AccountProfile.Role.MASTER):
        approve_master(profile); count += 1
    modeladmin.message_user(request, f"Одобрено мастеров: {count}.", messages.SUCCESS)


@admin.action(description="Отклонить выбранных мастеров")
def reject_masters(modeladmin, request, queryset):
    count = 0
    for profile in queryset.filter(role=AccountProfile.Role.MASTER):
        reject_master(profile); count += 1
    modeladmin.message_user(request, f"Отклонено мастеров: {count}.", messages.WARNING)


@admin.register(AccountProfile)
class AccountProfileAdmin(ModelAdmin):
    list_display = ("display_name", "role", "approval_status", "phone", "phone_verified_at", "email", "created_at")
    list_filter = ("role", "approval_status", "user__is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "phone")
    autocomplete_fields = ("user",)
    raw_id_fields = ("crm_client",)
    readonly_fields = ("phone_verified_at", "created_at", "updated_at")
    actions = (approve_masters, reject_masters)

    @admin.display(description="Имя")
    def display_name(self, obj): return obj.user.get_full_name() or obj.user.username

    @admin.display(description="Email")
    def email(self, obj): return obj.user.email

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.role == AccountProfile.Role.ADMIN:
            group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
            obj.user.groups.add(group)
            if not obj.user.is_staff:
                obj.user.is_staff = True
                obj.user.save(update_fields=["is_staff"])


@admin.register(PhoneVerification)
class PhoneVerificationAdmin(ModelAdmin):
    list_display = ("user", "phone", "created_at", "expires_at", "attempts", "verified_at")
    readonly_fields = ("user", "phone", "code_hash", "created_at", "expires_at", "attempts", "verified_at")
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
