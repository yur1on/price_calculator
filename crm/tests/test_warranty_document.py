from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMDocumentSettings, CRMOrder, CRMWorkItem
from crm.services import install_part
from crm.tests.test_crm import CRMBase
from finance.models import PartCatalog, StockReceipt, Supplier


class WarrantyDocumentTests(CRMBase):
    def setUp(self):
        super().setUp()
        self.repair = self.order(status=CRMOrder.Status.ISSUED, issued_at=timezone.now(),
            warranty_started_at=timezone.now(), warranty_days=90, final_price='330.00',
            internal_note='INTERNAL-NOTE-SECRET')
        CRMWorkItem.objects.create(order=self.repair, name='Замена дисплея', unit_price=250,
                                   comment='WORK-COMMENT-SECRET')
        self.url = reverse('crm:warranty', args=[self.repair.pk])

    def test_single_client_ticket_has_existing_repair_data(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<article class="warranty-ticket">', count=1)
        self.assertContains(response, '<h1>ГАРАНТИЙНЫЙ ТАЛОН</h1>', count=1)
        for value in (self.repair.number, self.device.display_name, 'Замена дисплея',
                      '330,00 BYN', '90 дней', self.repair.issued_at.strftime('%d.%m.%Y'),
                      self.repair.warranty_until.strftime('%d.%m.%Y')):
            self.assertContains(response, value)
        self.assertNotContains(response, 'Экземпляр сервиса')
        self.assertNotContains(response, 'receipt-copy')

    def test_permissions_unchanged(self):
        self.user.user_permissions.remove(Permission.objects.get(codename='manage_documents'))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        user = get_user_model().objects.create_user('warranty-client', password='pass')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_identifiers_only_when_present(self):
        self.device.imei = ''
        self.device.serial_number = ''
        self.device.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, 'IMEI:')
        self.assertNotContains(response, 'S/N:')
        self.device.imei = '356789012345678'
        self.device.serial_number = 'CLIENT-SERIAL'
        self.device.save()
        response = self.client.get(self.url)
        self.assertContains(response, 'IMEI: 356789012345678')
        self.assertContains(response, 'S/N: CLIENT-SERIAL')

    def test_internal_data_and_part_cost_are_not_disclosed(self):
        self.repair.status = CRMOrder.Status.REPAIR
        self.repair.save()
        supplier = Supplier.objects.create(name='SUPPLIER-SECRET')
        part = PartCatalog.objects.create(name='Клиентский дисплей', brand='Samsung', device_model='A55')
        receipt = StockReceipt.objects.create(date=date.today(), supplier=supplier, part=part,
                                             quantity=1, unit_cost='917.37')
        usage = install_part(self.repair, receipt.items.first().pk, self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Клиентский дисплей')
        for secret in ('SUPPLIER-SECRET', '917,37', '917.37', 'INTERNAL-NOTE-SECRET',
                       'WORK-COMMENT-SECRET', self.device.unlock_code,
                       usage.part_item.inventory_code, 'Прибыль', 'Маржа', 'Зарплата', '/crm/', '/private/'):
            self.assertNotContains(response, secret)

    def test_long_terms_are_preserved_and_escaped(self):
        settings = CRMDocumentSettings.load()
        settings.warranty_terms = ('Длинный текст условий. ' * 200) + '<script>alert(1)</script>'
        settings.save()
        response = self.client.get(self.url)
        self.assertContains(response, 'Длинный текст условий.', count=200)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, 'overflow:hidden')

    def test_receipt_still_has_two_copies_without_warranty_styles(self):
        response = self.client.get(reverse('crm:receipt', args=[self.repair.pk]))
        self.assertContains(response, 'class="receipt-copy"', count=2)
        self.assertContains(response, 'Экземпляр клиента', count=1)
        self.assertContains(response, 'Экземпляр сервиса', count=1)
        self.assertNotContains(response, 'warranty-ticket')
