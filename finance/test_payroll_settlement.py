"""Regression checks for payroll-period balances and date-range statistics."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Employee, PayrollCalculation, PayrollPeriod, RepairFinance, SalaryPayment
from .services import employee_rows, payroll_balance, payroll_preview, payroll_settlement


class PayrollSettlementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("settlement-admin", password="test")
        self.master_user = get_user_model().objects.create_user("settlement-master", password="test")
        self.master = Employee.objects.create(
            user=self.master_user, name="Андрей", default_percent=Decimal("35.00"),
        )
        self.other = Employee.objects.create(name="Другой мастер", default_percent=Decimal("35.00"))
        self.closed = PayrollPeriod.objects.create(
            start_date=date(2026, 10, 3), end_date=date(2026, 10, 17),
            status=PayrollPeriod.Status.CLOSED,
        )
        self.open = PayrollPeriod.objects.create(
            start_date=date(2026, 10, 19), end_date=date(2026, 10, 31),
        )

    def calculation(self, period, employee, amount):
        return PayrollCalculation.objects.create(
            period=period, employee=employee, repairs_count=1, revenue=Decimal("600.00"),
            direct_costs=Decimal("100.00"), repair_margin=Decimal("500.00"),
            distributed_costs=Decimal("0.00"), salary_base=Decimal("500.00"),
            percent=Decimal("35.00"), salary_amount=Decimal(amount),
        )

    def payment(self, day, amount, employee=None, comment=""):
        return SalaryPayment.objects.create(
            employee=employee or self.master, date=day,
            amount=Decimal(amount), comment=comment,
        )

    def test_300_payment_in_open_period_carries_only_previous_residual(self):
        historical = self.calculation(self.closed, self.master, "500.00")
        self.payment(date(2026, 10, 15), "400.00")
        self.payment(date(2026, 10, 20), "300.00", comment="Аванс")
        RepairFinance.objects.create(
            date=date(2026, 10, 20), description="Ремонт", revenue="331.76",
            part_cost="60.00", employee=self.master, master_percent="35.00",
        )
        preview = payroll_preview(self.open, self.master)
        self.assertEqual(preview["salary_amount"], Decimal("95.12"))
        settlement = payroll_settlement(self.open, self.master, preview["salary_amount"])
        self.assertEqual(settlement["opening_balance"], Decimal("100.00"))
        self.assertEqual(settlement["period_paid"], Decimal("300.00"))
        self.assertEqual(settlement["closing_balance"], Decimal("-104.88"))
        self.assertEqual([item.comment for item in settlement["payments"]], ["Аванс"])
        self.assertEqual(payroll_balance(self.master, self.open)["balance"], Decimal("-104.88"))
        historical.refresh_from_db()
        self.assertEqual(historical.salary_amount, Decimal("500.00"))

    def test_positive_and_negative_carry_multiple_payments_and_other_master(self):
        self.calculation(self.closed, self.master, "500.00")
        self.calculation(self.closed, self.other, "150.00")
        self.payment(date(2026, 10, 10), "400.00")
        self.payment(date(2026, 10, 20), "20.00")
        self.payment(date(2026, 10, 21), "30.00")
        self.payment(date(2026, 10, 21), "50.00", employee=self.other)
        own = payroll_settlement(self.open, self.master, Decimal("150.00"))
        self.assertEqual((own["opening_balance"], own["period_paid"], own["closing_balance"]),
                         (Decimal("100.00"), Decimal("50.00"), Decimal("200.00")))
        self.assertEqual(payroll_balance(self.master)["balance"], Decimal("50.00"))
        self.assertEqual(payroll_balance(self.other)["balance"], Decimal("100.00"))
        self.payment(date(2026, 10, 16), "200.00")
        negative = payroll_settlement(self.open, self.master, Decimal("95.12"))
        self.assertEqual(negative["opening_balance"], Decimal("-100.00"))
        self.assertEqual(negative["closing_balance"], Decimal("-54.88"))

    def test_payment_between_periods_and_after_close_is_counted_once(self):
        self.calculation(self.closed, self.master, "500.00")
        self.payment(date(2026, 10, 18), "300.00", comment="После закрытия")
        self.assertEqual(payroll_settlement(self.closed, self.master, Decimal("500.00"))["closing_balance"],
                         Decimal("500.00"))
        self.assertEqual(payroll_settlement(self.open, self.master, Decimal("95.12"))["opening_balance"],
                         Decimal("200.00"))
        self.assertEqual(payroll_balance(self.master)["balance"], Decimal("200.00"))
        self.assertEqual(payroll_balance(self.master, self.closed)["balance"], Decimal("200.00"))
        self.payment(date(2026, 10, 20), "300.00")
        self.assertEqual(payroll_settlement(self.open, self.master, Decimal("95.12"))["closing_balance"],
                         Decimal("-4.88"))

    def test_filter_changes_statistics_not_total_balance_or_period_card(self):
        self.calculation(self.closed, self.master, "500.00")
        self.payment(date(2026, 10, 20), "300.00")
        RepairFinance.objects.create(
            date=date(2026, 10, 20), description="Сегодня", revenue="200.00",
            employee=self.master, master_percent="35.00",
        )
        RepairFinance.objects.create(
            date=date(2026, 10, 5), description="Ранее в месяце", revenue="100.00",
            employee=self.master, master_percent="35.00",
        )
        today = employee_rows(date(2026, 10, 20), date(2026, 10, 20), self.master)[0]
        month = employee_rows(date(2026, 10, 1), date(2026, 10, 31), self.master)[0]
        self.assertEqual(today["date_difference"], Decimal("-230.00"))
        self.assertEqual(month["date_difference"], Decimal("-195.00"))
        self.client.force_login(self.admin)
        for period_filter in ("today", "month"):
            response = self.client.get(reverse("finance:master_list"), {"period": period_filter})
            self.assertEqual(response.status_code, 200)
            row = next(item for item in response.context["rows"] if item["employee"] == self.master)
            self.assertEqual(row["settlement"]["balance"], Decimal("270.00"))
            self.assertContains(response, "Баланс расчётов с мастером")
            self.assertContains(response, "Разница за выбранные даты")
        detail = self.client.get(reverse("finance:payroll_period_detail", args=[self.open.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Выплаты и авансы за период")
        self.assertContains(detail, "300.00 BYN")
        self.assertContains(detail, "Остаток с предыдущих периодов")

    def test_master_sees_own_balance_only_and_admin_route_forbidden(self):
        self.calculation(self.closed, self.master, "100.00")
        self.calculation(self.closed, self.other, "999.00")
        self.payment(date(2026, 10, 20), "30.00")
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("accounts:my_salary"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["remaining"], Decimal("70.00"))
        self.assertEqual(response.context["current_settlement"]["opening_balance"], Decimal("100.00"))
        self.assertNotContains(response, "Другой мастер")
        self.assertEqual(self.client.get(reverse("finance:payroll_period_detail", args=[self.open.pk])).status_code, 302)
        portal = self.client.get(reverse("master_portal:dashboard"))
        self.assertEqual(portal.status_code, 200)
        self.assertEqual(portal.context["remaining"], Decimal("70.00"))
        self.assertNotContains(portal, "Другой мастер")
        period = self.client.get(reverse("master_portal:period_detail", args=[self.open.pk]))
        self.assertEqual(period.status_code, 200)
        self.assertEqual(period.context["settlement"]["opening_balance"], Decimal("100.00"))
        self.assertNotContains(period, "Другой мастер")

    def test_uncovered_preperiod_repairs_warn_instead_of_asserting_zero_opening(self):
        RepairFinance.objects.create(
            date=date(2026, 10, 1), description="До первого периода", revenue="100.00",
            employee=self.master, master_percent="35.00",
        )
        self.assertTrue(payroll_balance(self.master, self.open)["opening_unknown"])
