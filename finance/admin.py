from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from unfold.admin import ModelAdmin

from .models import (
    Employee, Expense, ExpenseCategory, FinanceDashboard, OtherIncome, PartCatalog,
    PartItem, RepairFinance, RepairPart, SalaryPayment, StockReceipt, Supplier,
    SupplierPayment, SupplierReturn, WarrantyClaim,
    DistributedExpense, DistributedExpenseAllocation, PayrollCalculation, PayrollPeriod, PayrollRepairSnapshot,
)


@admin.register(FinanceDashboard)
class FinanceDashboardAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return HttpResponseRedirect(reverse("finance:dashboard"))


@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    list_display = ("name", "default_percent", "is_owner", "is_active", "created_at")
    list_filter = ("is_owner", "is_active")
    search_fields = ("name",)


@admin.register(RepairFinance)
class RepairFinanceAdmin(ModelAdmin):
    list_display = ("date", "description", "revenue", "part_cost", "employee", "master_percent", "master_salary", "workshop_profit")
    list_filter = ("date", "employee")
    search_fields = ("description", "comment", "employee__name")
    readonly_fields = ("repair_margin", "master_salary", "workshop_profit", "created_at")
    list_select_related = ("employee",)


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Expense)
class ExpenseAdmin(ModelAdmin):
    list_display = ("date", "description", "category", "amount")
    list_filter = ("date", "category")
    search_fields = ("description", "comment")


@admin.register(OtherIncome)
class OtherIncomeAdmin(ModelAdmin):
    list_display = ("date", "description", "amount")
    list_filter = ("date",)
    search_fields = ("description", "comment")


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(ModelAdmin):
    list_display = ("date", "employee", "amount", "comment")
    list_filter = ("date", "employee")
    search_fields = ("employee__name", "comment")


for model in (Supplier, PartCatalog, StockReceipt, PartItem, RepairPart, WarrantyClaim, SupplierPayment, SupplierReturn, DistributedExpense, DistributedExpenseAllocation, PayrollCalculation, PayrollPeriod, PayrollRepairSnapshot):
    admin.site.register(model, ModelAdmin)
