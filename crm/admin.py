from django.contrib import admin

from .models import (
    CRMBrand,
    CRMClient,
    CRMDevice,
    CRMDeviceModel,
    CRMDeviceType,
    CRMDocumentSettings,
    CRMIssueTemplate,
    CRMOrder,
    CRMOrderPartUsage,
    CRMWarrantyTerm,
    CRMWorkTemplate,
)


admin.site.register([
    CRMClient,
    CRMDevice,
    CRMWorkTemplate,
    CRMIssueTemplate,
    CRMDeviceType,
    CRMBrand,
    CRMDeviceModel,
    CRMWarrantyTerm,
    CRMDocumentSettings,
])


@admin.register(CRMOrder)
class CRMOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "device", "status", "accepted_at")
    search_fields = ("number", "client__name", "client__phone", "device__model")
    # Workflow fields are service-owned. Editing them here would bypass CRMEvent,
    # issue/payment, warranty, Finance and Telegram side effects.
    readonly_fields = (
        "number", "status", "ready_at", "issued_at", "warranty_started_at",
        "finance_repair", "created_at", "updated_at",
    )


@admin.register(CRMOrderPartUsage)
class CRMOrderPartUsageAdmin(admin.ModelAdmin):
    list_display = ("order", "part_item", "status", "unit_cost_snapshot", "supplier_name_snapshot", "installed_at", "installed_by")
    list_filter = ("status", "installed_at")
    search_fields = ("order__number", "part_item__receipt__part__name", "supplier_name_snapshot")
    readonly_fields = (
        "order", "part_item", "finance_repair", "unit_cost_snapshot", "supplier_name_snapshot",
        "warranty_days_snapshot", "status", "installed_at", "installed_by", "removed_at", "removed_by",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
