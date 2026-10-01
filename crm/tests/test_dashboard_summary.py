from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.models import CRMClient, CRMDevice, CRMOrder
from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType


class DashboardSummaryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('summary-admin', '', 'pass')
        self.client.force_login(self.admin)

    def test_empty_summary_and_existing_links(self):
        response = self.client.get(reverse('crm:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="crm-summary-count">0</strong>', count=2)
        for name in ('capacity', 'attention', 'appointments'):
            self.assertContains(response, f'aria-expanded="false" aria-controls="dashboard-{name}"')
        self.assertNotContains(response, '<details open')
        capacity = response.context['capacity']['today']
        self.assertContains(response, f'{capacity["percent"]}%')
        self.assertContains(response, f'свободно {capacity["available"]} мин')
        for name in ('capacity', 'work_queue', 'appointment_list'):
            self.assertContains(response, reverse('crm:' + name))

    def test_populated_summary_matches_loaded_lists(self):
        customer = CRMClient.objects.create(name='Клиент', phone='80291234567')
        device = CRMDevice.objects.create(client=customer, brand='Samsung', model='A60')
        order = CRMOrder.objects.create(client=customer, device=device, status='diagnostic',
                                        accepted_at=timezone.now() - timedelta(days=60))
        brand = PhoneBrand.objects.create(name='Samsung', slug='summary-brand')
        phone = PhoneModel.objects.create(brand=brand, name='A60', slug='summary-phone')
        repair = RepairType.objects.create(name='Дисплей', slug='summary-repair')
        start = timezone.now() + timedelta(hours=4)
        appointment = Appointment.objects.create(phone_model=phone, repair_type=repair,
            start=start, end=start + timedelta(hours=1), customer_name='Запись',
            customer_phone='80291234567', price_original=100, price_final=100)
        response = self.client.get(reverse('crm:dashboard'))
        self.assertEqual(len(response.context['attention_orders']), 1)
        self.assertEqual(len(response.context['nearest_appointments']), 1)
        self.assertContains(response, 'class="crm-summary-count is-warning">1</strong>')
        self.assertContains(response, 'class="crm-summary-count is-info">1</strong>')
        self.assertContains(response, reverse('crm:order_detail', args=[order.pk]))
        self.assertContains(response, reverse('crm:appointment_detail', args=[appointment.pk]))

    def test_existing_role_permissions(self):
        user = get_user_model().objects.create_user('summary-master', password='pass')
        profile = AccountProfile.objects.create(user=user, role='master', approval_status='pending')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('crm:dashboard')).status_code, 403)
        approve_master(profile)
        self.assertEqual(self.client.get(reverse('crm:dashboard')).status_code, 200)
        customer = get_user_model().objects.create_user('summary-client', password='pass')
        AccountProfile.objects.create(user=customer, role='client', approval_status='active')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse('crm:dashboard')).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('crm:dashboard')).status_code, 302)

    def test_htmx_search_does_not_replace_summary(self):
        response = self.client.get(reverse('crm:dashboard'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'crm-order-results')
        self.assertNotContains(response, 'data-dashboard-panel')
