from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import CRMClient, CRMDevice, CRMEvent, CRMOrder, CRMWorkItem
from finance.models import Employee, PayrollCalculation, PayrollPeriod, RepairFinance, SalaryPayment, Supplier
from finance.services import close_payroll_period
from django.utils import timezone
from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType

from .models import AccountProfile, PhoneVerification
from .services import approve_master, reject_master, request_phone_verification, verify_phone_code


class RegistrationTests(TestCase):
    def registration_data(self, role="client", email="user@example.com"):
        return {"role": role, "first_name": "Иван", "last_name": "Петров", "phone": "+375291112233", "email": email, "password1": "Secure-4826!", "password2": "Secure-4826!"}

    def test_client_registration_creates_safe_active_account(self):
        response = self.client.post(reverse("accounts:register"), self.registration_data())
        self.assertRedirects(response, reverse("accounts:client_dashboard"))
        user = get_user_model().objects.get(email="user@example.com")
        self.assertEqual(user.account_profile.role, AccountProfile.Role.CLIENT)
        self.assertEqual(user.account_profile.approval_status, AccountProfile.Approval.ACTIVE)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_perm("crm.access_crm"))
        self.assertIsNone(user.account_profile.crm_client)

    def test_login_and_registration_pages_are_public(self):
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:register")).status_code, 200)

    def test_master_registration_starts_pending_without_employee(self):
        response = self.client.post(reverse("accounts:register"), self.registration_data("master", "master@example.com"))
        self.assertRedirects(response, reverse("accounts:registration_pending"))
        user = get_user_model().objects.get(email="master@example.com")
        self.assertEqual(user.account_profile.approval_status, AccountProfile.Approval.PENDING)
        self.assertFalse(Employee.objects.filter(user=user).exists())
        self.assertFalse(user.has_perm("crm.access_crm"))

    def test_duplicate_email_and_password_validation(self):
        get_user_model().objects.create_user("user@example.com", email="user@example.com", password="Secure-4826!")
        response = self.client.post(reverse("accounts:register"), self.registration_data())
        self.assertContains(response, "уже существует")
        data = self.registration_data(email="weak@example.com"); data["password1"] = data["password2"] = "123"
        self.assertContains(self.client.post(reverse("accounts:register"), data), "too short")

    def test_login_logout_and_anonymous_workspace_redirect(self):
        user = get_user_model().objects.create_user("login@example.com", email="login@example.com", password="Secure-4826!")
        AccountProfile.objects.create(user=user, role="client", approval_status="active")
        self.assertRedirects(self.client.get(reverse("accounts:workspace")), f"{reverse('accounts:login')}?next={reverse('accounts:workspace')}")
        response = self.client.post(reverse("accounts:login"), {"username": "login@example.com", "password": "Secure-4826!"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:client_dashboard"))
        self.assertEqual(self.client.get(reverse("accounts:client_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("home"))

    def test_login_preserves_safe_next_parameter(self):
        user = get_user_model().objects.create_user("next@example.com", email="next@example.com", password="Secure-4826!")
        AccountProfile.objects.create(user=user, role="client", approval_status="active")
        target = reverse("accounts:client_dashboard")
        response = self.client.post(f"{reverse('accounts:login')}?next={target}", {
            "username": "next@example.com", "password": "Secure-4826!", "next": target,
        })
        self.assertRedirects(response, target)

    def test_public_navigation_changes_after_login(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:register"))
        user = get_user_model().objects.create_user("nav@example.com", email="nav@example.com", password="Secure-4826!", first_name="Иван")
        AccountProfile.objects.create(user=user, role="client", approval_status="active")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Личный кабинет")
        self.assertContains(response, reverse("accounts:client_dashboard"))
        self.assertContains(response, f'action="{reverse("accounts:logout")}"')
        self.assertNotContains(response, f'href="{reverse("accounts:logout")}"')


class RoleAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.master_user = User.objects.create_user("master@example.com", email="master@example.com", password="Secure-4826!", first_name="Сергей")
        self.profile = AccountProfile.objects.create(user=self.master_user, role="master", approval_status="pending", phone="+375291111111")
        self.customer = CRMClient.objects.create(name="Клиент 1", phone="80291111111")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Apple", model="iPhone 13")

    def test_pending_and_rejected_master_cannot_enter_workspace(self):
        self.client.force_login(self.master_user)
        self.assertEqual(self.client.get(reverse("accounts:master_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 403)
        reject_master(self.profile)
        self.assertEqual(self.client.get(reverse("accounts:master_dashboard")).status_code, 403)

    def test_master_login_redirect_depends_on_approval(self):
        response = self.client.post(reverse("accounts:login"), {"username": "master@example.com", "password": "Secure-4826!"})
        self.assertEqual(response.url, reverse("accounts:registration_pending"))
        self.client.logout()
        approve_master(self.profile)
        response = self.client.post(reverse("accounts:login"), {"username": "master@example.com", "password": "Secure-4826!"})
        self.assertEqual(response.url, reverse("crm:work_queue"))

    def test_rejected_master_sees_rejected_status(self):
        reject_master(self.profile)
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("accounts:registration_pending"))
        self.assertContains(response, "Заявка отклонена")
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 403)

    def test_approval_is_idempotent_and_grants_work_access(self):
        employee = approve_master(self.profile)
        self.assertEqual(approve_master(self.profile).pk, employee.pk)
        self.assertEqual(Employee.objects.filter(user=self.master_user).count(), 1)
        self.master_user = get_user_model().objects.get(pk=self.master_user.pk)
        self.client.force_login(self.master_user)
        self.assertRedirects(self.client.get(reverse("accounts:master_dashboard")), reverse("crm:work_queue"))
        self.assertEqual(self.client.get(reverse("accounts:master_stock")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:master_suppliers")).status_code, 200)

    def test_master_sees_all_workshop_repairs_and_search_results(self):
        employee = approve_master(self.profile)
        other = Employee.objects.create(name="Другой", default_percent="35")
        own = CRMOrder.objects.create(client=self.customer, device=self.device, employee=employee, issue_description="Свой ремонт")
        foreign = CRMOrder.objects.create(client=self.customer, device=self.device, employee=other, issue_description="Чужой секрет")
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        dashboard = self.client.get(reverse("crm:dashboard"))
        self.assertContains(dashboard, own.number)
        self.assertContains(dashboard, foreign.number)
        self.assertEqual(self.client.get(reverse("crm:order_detail", args=[foreign.pk])).status_code, 200)
        search = self.client.get(reverse("crm:global_search"), {"q": foreign.number})
        self.assertContains(search, foreign.number)

    def test_master_has_workshop_access_but_owner_finance_stays_closed(self):
        employee = approve_master(self.profile)
        order = CRMOrder.objects.create(client=self.customer, device=self.device, employee=employee, issue_description="Ремонт")
        other = Employee.objects.create(name="Другой", default_percent="35")
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        self.assertEqual(self.client.get(reverse("finance:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:income_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:expense_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:salary_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:supplier_payment_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:stock_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("finance:supplier_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("finance:warranty_list")).status_code, 200)
        self.assertEqual(self.client.post(reverse("crm:order_assignee", args=[order.pk]), {"employee": other.pk}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.employee, other)
        self.assertEqual(self.client.get(reverse("crm:order_edit", args=[order.pk])).status_code, 200)

    def test_master_can_create_client_and_repair(self):
        approve_master(self.profile)
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.post(reverse("crm:client_create"), {
            "name": "Новый клиент", "phone": "+375291234567", "additional_phone": "",
            "email": "", "source": "", "city": "Гомель", "note": "",
        })
        self.assertEqual(response.status_code, 302)
        new_client = CRMClient.objects.get(name="Новый клиент")
        response = self.client.post(reverse("crm:order_create"), {
            "existing_client": new_client.pk, "client_name": "", "phone": "",
            "device_type": "Телефон", "brand": "Xiaomi", "device_model": "14",
            "issue_description": "Не включается", "employee": self.profile.user.employee_profile.pk,
            "order_type": "paid", "agreed_price": "250", "warranty_days": "90",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CRMOrder.objects.filter(client=new_client, device__model="14").exists())

    def test_master_can_change_status_and_cannot_assign_inactive_employee(self):
        employee = approve_master(self.profile)
        order = CRMOrder.objects.create(client=self.customer, device=self.device, employee=employee, issue_description="Ремонт")
        inactive = Employee.objects.create(name="Неактивный", default_percent="35", is_active=False)
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.post(reverse("crm:order_status", args=[order.pk]), {"status": CRMOrder.Status.DIAGNOSTIC})
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.DIAGNOSTIC)
        self.assertEqual(self.client.post(reverse("crm:order_assignee", args=[order.pk]), {"employee": inactive.pk}).status_code, 404)

    def test_master_supplier_page_has_work_data_without_reconciliation(self):
        approve_master(self.profile)
        supplier = Supplier.objects.create(name="Рабочий поставщик", phone="+375291112233")
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.get(reverse("finance:supplier_detail", args=[supplier.pk]))
        self.assertContains(response, "Рабочий поставщик")
        self.assertContains(response, "+375291112233")
        self.assertNotContains(response, "Долг поставщику")
        self.assertNotContains(response, "Открыть сверку")
        self.assertEqual(self.client.get(reverse("finance:supplier_reconciliation", args=[supplier.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:supplier_reconciliation_excel", args=[supplier.pk])).status_code, 403)

    def test_master_can_open_only_own_salary_page(self):
        employee = approve_master(self.profile)
        PayrollPeriod.objects.create(start_date=date(2026, 9, 1), end_date=date(2026, 9, 14))
        RepairFinance.objects.create(date=date(2026, 9, 5), description="Работа мастера", revenue="300", part_cost="50", employee=employee, master_percent="35")
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.get(reverse("accounts:my_salary"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Работа мастера")
        self.assertTemplateUsed(response, "crm/base.html")
        self.assertContains(response, "87.50")
        other = Employee.objects.create(name="Другой мастер", default_percent="35")
        RepairFinance.objects.create(date=date(2026, 9, 5), description="ЧУЖОЙ-РЕМОНТ", revenue="999", employee=other, master_percent="35")
        response = self.client.get(reverse("accounts:my_salary"), {"employee": other.pk, "master": other.pk})
        self.assertNotContains(response, "ЧУЖОЙ-РЕМОНТ")
        self.assertContains(response, "Работа мастера")
        self.assertNotContains(response, 'href="/finance/"')

    def test_salary_anonymous_redirect_and_empty_state(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:my_salary")).status_code, 302)
        approve_master(self.profile)
        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.get(reverse("accounts:my_salary"))
        self.assertContains(response, "Открытого расчётного периода пока нет")

    def test_salary_journal_uses_closed_snapshots_and_only_own_employee(self):
        employee = approve_master(self.profile)
        period = PayrollPeriod.objects.create(start_date=date(2026, 9, 1), end_date=date(2026, 9, 14))
        repair = RepairFinance.objects.create(
            date=date(2026, 9, 5), description="Зафиксированный ремонт", revenue="300",
            part_cost="50", employee=employee, master_percent="35",
        )
        SalaryPayment.objects.create(employee=employee, date=timezone.localdate() - timedelta(days=1), amount="20", comment="Аванс")
        close_payroll_period(period)
        calculation = PayrollCalculation.objects.get(period=period, employee=employee)
        SalaryPayment.objects.create(employee=employee, date=timezone.localdate() + timedelta(days=1), amount="10", comment="Доплата")
        open_period = PayrollPeriod.objects.create(start_date=date(2026, 10, 1), end_date=date(2026, 10, 14))
        RepairFinance.objects.create(
            date=date(2026, 10, 2), description="Только прогноз", revenue="100",
            part_cost="0", employee=employee, master_percent="35",
        )
        other = Employee.objects.create(name="Другой мастер", default_percent="35")
        foreign_period = PayrollPeriod.objects.create(
            start_date=date(2026, 9, 15), end_date=date(2026, 9, 28), status=PayrollPeriod.Status.CLOSED,
        )
        foreign_calculation = PayrollCalculation.objects.create(
            period=foreign_period, employee=other, repairs_count=0, revenue=0, direct_costs=0,
            repair_margin=0, distributed_costs=0, salary_base=0, percent=35, salary_amount=0,
        )

        self.client.force_login(get_user_model().objects.get(pk=self.master_user.pk))
        response = self.client.get(reverse("accounts:my_salary"), {"employee_id": other.pk})
        self.assertContains(response, "Аванс")
        self.assertContains(response, "Доплата")
        self.assertContains(response, "87.50")
        self.assertContains(response, "92.50")
        self.assertContains(response, str(open_period))
        self.assertEqual(response.context["accrued"], calculation.salary_amount)
        self.assertEqual(response.context["preliminary"], Decimal("35.00"))
        self.assertEqual(response.context["earned"], calculation.salary_amount + Decimal("35.00"))
        self.assertEqual(response.context["remaining"], calculation.salary_amount + Decimal("35.00") - Decimal("30.00"))
        ledger = response.context["ledger"]
        self.assertEqual([item["kind"] for item in ledger], ["payment", "accrual", "payment"])
        self.assertEqual(ledger[0]["balance"], -20)
        self.assertEqual(ledger[-1]["balance"], calculation.salary_amount - 30)
        self.assertContains(response, "crm-salary-mobile")

        detail = self.client.get(reverse("accounts:salary_calculation_detail", args=[calculation.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Итог")
        self.assertContains(detail, "Зафиксированный ремонт")
        self.assertContains(detail, "Распределяемые расходы периода")
        self.assertEqual(self.client.get(reverse("accounts:salary_calculation_detail", args=[foreign_calculation.pk])).status_code, 404)

        repair.description = "Изменённый после закрытия"
        repair.revenue = "1"
        repair.save()
        detail = self.client.get(reverse("accounts:salary_calculation_detail", args=[calculation.pk]))
        self.assertContains(detail, "Зафиксированный ремонт")
        self.assertNotContains(detail, "Изменённый после закрытия")

    def test_client_cannot_access_internal_system(self):
        client_user = get_user_model().objects.create_user("client@example.com", password="Secure-4826!")
        AccountProfile.objects.create(user=client_user, role="client", approval_status="active")
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(reverse("accounts:client_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:stock_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:my_salary")).status_code, 403)

    def test_superuser_keeps_full_access(self):
        admin = get_user_model().objects.create_superuser("owner", password="Secure-4826!")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("finance:dashboard")).status_code, 200)
        self.assertEqual(self.client.get("/admin/accounts/accountprofile/").status_code, 200)

    def test_superuser_login_redirects_to_workbench(self):
        get_user_model().objects.create_superuser("admin-login", password="Secure-4826!")
        response = self.client.post(reverse("accounts:login"), {"username": "admin-login", "password": "Secure-4826!"})
        self.assertEqual(response.url, reverse("workbench"))


class ClientPortalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("client-portal@example.com", email="client-portal@example.com", password="Secure-4826!", first_name="Иван")
        self.profile = AccountProfile.objects.create(user=self.user, role="client", approval_status="active", phone="+375 29 111-22-33")
        self.crm_client = CRMClient.objects.create(name="Иван", phone="80291112233")
        self.device = CRMDevice.objects.create(client=self.crm_client, device_type="Телефон", brand="Apple", model="iPhone 13", serial_number="1234567890")
        self.order = CRMOrder.objects.create(client=self.crm_client, device=self.device, issue_description="Не включается", agreed_price="250", warranty_days=90)
        self.client.force_login(self.user)

    def test_unverified_phone_does_not_link_but_verified_unique_phone_does(self):
        self.assertIsNone(self.profile.crm_client)
        verification, code = request_phone_verification(self.profile)
        self.assertNotEqual(verification.code_hash, code)
        self.assertEqual(verify_phone_code(self.profile, code), "linked")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.crm_client, self.crm_client)

    def test_multiple_phone_matches_never_link_automatically(self):
        CRMClient.objects.create(name="Дубликат", phone="+375291112233")
        _, code = request_phone_verification(self.profile)
        self.assertEqual(verify_phone_code(self.profile, code), "multiple")
        self.profile.refresh_from_db(); self.assertIsNone(self.profile.crm_client)

    def test_client_only_sees_owned_order_and_foreign_id_is_404(self):
        self.profile.crm_client = self.crm_client; self.profile.save()
        other_client = CRMClient.objects.create(name="Другой", phone="+375291234567")
        other_device = CRMDevice.objects.create(client=other_client, device_type="Телефон", model="Secret")
        other = CRMOrder.objects.create(client=other_client, device=other_device, issue_description="Чужой ремонт")
        response = self.client.get(reverse("accounts:client_repairs"))
        self.assertContains(response, self.order.number); self.assertNotContains(response, other.number)
        self.assertEqual(self.client.get(reverse("accounts:client_repair_detail", args=[other.pk])).status_code, 404)

    def test_detail_whitelists_status_events_and_hides_internal_data(self):
        self.profile.crm_client = self.crm_client; self.profile.save()
        CRMWorkItem.objects.create(order=self.order, name="Замена дисплея", unit_price="100")
        CRMEvent.objects.create(order=self.order, event_type="status", description="Статус", new_value="Диагностика")
        CRMEvent.objects.create(order=self.order, event_type="comment", description="Внутренний секрет")
        response = self.client.get(reverse("accounts:client_repair_detail", args=[self.order.pk]))
        self.assertContains(response, "Диагностика"); self.assertContains(response, "Замена дисплея")
        self.assertNotContains(response, "Внутренний секрет"); self.assertNotContains(response, "Поставщик")

    def test_phone_change_revokes_verification_and_link(self):
        from django.utils import timezone
        self.profile.phone_verified_at=timezone.now(); self.profile.crm_client=self.crm_client; self.profile.save()
        response=self.client.post(reverse("accounts:client_profile"), {"first_name":"Иван", "last_name":"", "email":self.user.email, "phone":"+375291119999"})
        self.assertRedirects(response, reverse("accounts:client_profile"))
        self.profile.refresh_from_db(); self.assertIsNone(self.profile.phone_verified_at); self.assertIsNone(self.profile.crm_client)

    def test_phone_code_cannot_be_reused(self):
        _, code=request_phone_verification(self.profile); verify_phone_code(self.profile, code)
        with self.assertRaises(Exception): verify_phone_code(self.profile, code)

    def test_client_dashboard_uses_workbench_shell_and_safe_sidebar(self):
        response = self.client.get(reverse("accounts:client_dashboard"))
        self.assertContains(response, 'class="wb-app"')
        self.assertContains(response, "Мои ремонты")
        self.assertContains(response, "Мои устройства")
        self.assertContains(response, "Профиль")
        self.assertNotContains(response, ">Финансы<")
        self.assertNotContains(response, ">Поставщики<")
        self.assertNotContains(response, "Найти клиента, ремонт")


class AppointmentLifecycleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client_user = User.objects.create_user("booking-client@example.com", password="Secure-4826!", first_name="Анна")
        self.profile = AccountProfile.objects.create(user=self.client_user, role="client", approval_status="active", phone="+375291234567")
        brand = PhoneBrand.objects.create(name="Samsung", slug="samsung-life")
        self.phone_model = PhoneModel.objects.create(brand=brand, name="Galaxy S24", slug="galaxy-s24-life")
        self.repair_type = RepairType.objects.create(name="Замена дисплея", slug="display-life")
        self.appointment = Appointment.objects.create(phone_model=self.phone_model, repair_type=self.repair_type, start=timezone.now()+timedelta(days=2), end=timezone.now()+timedelta(days=2, hours=1), customer_name="Анна", customer_phone="+375291234567", price_original="200", price_final="200", account=self.client_user)

    def test_client_appointment_ownership_and_idor(self):
        other = get_user_model().objects.create_user("other-booking@example.com", password="Secure-4826!")
        AccountProfile.objects.create(user=other, role="client", approval_status="active")
        foreign = Appointment.objects.create(phone_model=self.phone_model, repair_type=self.repair_type, start=timezone.now()+timedelta(days=3), end=timezone.now()+timedelta(days=3, hours=1), customer_name="Другой", customer_phone="+375291111111", price_original="100", price_final="100", account=other)
        self.client.force_login(self.client_user)
        response=self.client.get(reverse("accounts:client_appointments"))
        self.assertContains(response, "Galaxy S24"); self.assertEqual(self.client.get(reverse("accounts:client_appointment_detail", args=[foreign.pk])).status_code, 404)

    def test_client_can_cancel_only_own_future_unconverted_appointment(self):
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("accounts:client_appointment_cancel", args=[self.appointment.pk])).status_code, 405)
        self.client.post(reverse("accounts:client_appointment_cancel", args=[self.appointment.pk]))
        self.appointment.refresh_from_db(); self.assertEqual(self.appointment.status, "cancelled")

    def test_approved_master_converts_once_and_reuses_linked_client(self):
        crm_client=CRMClient.objects.create(name="Анна", phone="+375291234567"); self.profile.crm_client=crm_client; self.profile.save()
        master=get_user_model().objects.create_user("converter@example.com", password="Secure-4826!")
        master_profile=AccountProfile.objects.create(user=master, role="master", approval_status="pending"); employee=approve_master(master_profile)
        self.client.force_login(master)
        data={"appointment":self.appointment.pk,"existing_client":crm_client.pk,"existing_device":"","client_name":"Анна","phone":"+375291234567","additional_phone":"","email":"","source":"Онлайн-запись","city":"","client_note":"","device_type":"Телефон","brand":"Samsung","device_model":"Galaxy S24","serial_number":"","color":"","condition":"","included_items":"","unlock_code":"","device_note":"","issue_template":"","issue_description":"Замена дисплея","internal_note":"","employee":employee.pk,"order_type":"paid","accepted_at":"","agreed_price":"200","warranty_days":"90"}
        response=self.client.post(reverse("crm:order_create"),data); self.assertEqual(response.status_code,302)
        self.appointment.refresh_from_db(); self.assertEqual(self.appointment.status,"done"); self.assertEqual(self.appointment.crm_order.client,crm_client)
        self.assertEqual(self.appointment.crm_order.estimated_work_minutes_snapshot, 60)
        count=CRMOrder.objects.count(); self.client.post(reverse("crm:order_create"),data); self.assertEqual(CRMOrder.objects.count(),count)

    def test_client_cannot_open_workbench_appointments_or_convert(self):
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("crm:appointment_list")).status_code,403)
        self.assertEqual(self.client.get(reverse("crm:order_create")+f"?appointment={self.appointment.pk}").status_code,403)
