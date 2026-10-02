from itertools import product
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from crm.document_context import document_contact_context, print_status_url
from crm.forms import DocumentSettingsForm
from crm.models import CRMDocumentSettings
from crm.tests.test_crm import CRMBase


class ContactFormattingTests(SimpleTestCase):
    def test_all_eight_contact_combinations(self):
        request = RequestFactory().get('/')
        for present in product((False, True), repeat=3):
            with self.subTest(present=present):
                values = [value if included else '' for included, value in zip(
                    present, ('+375 29 000-00-00', 'tehsfera.by', 'info@example.com'))]
                config = SimpleNamespace(phone=values[0], website=values[1], email=values[2])
                context = document_contact_context(config, request)
                html = render_to_string('crm/documents/contact_line.html', context).strip()
                expected = ' · '.join(value for value in values if value)
                self.assertEqual(html, f'<div class="document-contacts">{expected}</div>' if expected else '')

    def test_status_origins_and_fallback(self):
        request = RequestFactory().get('/', HTTP_HOST='localhost:8000')
        for value, expected in (
            ('tehsfera.by', 'https://tehsfera.by/repair-status/'),
            ('https://tehsfera.by', 'https://tehsfera.by/repair-status/'),
            ('http://tehsfera.by', 'http://tehsfera.by/repair-status/'),
            (' https://tehsfera.by/path?q=x#fragment ', 'https://tehsfera.by/repair-status/'),
            ('', 'http://localhost:8000/repair-status/'),
            ('ftp://example.com', 'http://localhost:8000/repair-status/'),
            ('https://example.com:bad', 'http://localhost:8000/repair-status/'),
        ):
            with self.subTest(value=value):
                self.assertEqual(print_status_url(value, request), expected)

    def test_contact_values_are_escaped_and_whitespace_is_empty(self):
        request = RequestFactory().get('/')
        config = SimpleNamespace(phone=' ', website='', email='<script>alert(1)</script>')
        html = render_to_string('crm/documents/contact_line.html', document_contact_context(config, request))
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn(' · ', html)


class DocumentContactTests(CRMBase):
    def setUp(self):
        super().setUp()
        self.repair = self.order()
        self.config = CRMDocumentSettings.load()
        self.config.phone = '+375 29 000-00-00'
        self.config.email = 'info@example.com'
        self.config.website = 'tehsfera.by'
        self.config.address = 'Тестовый адрес'
        self.config.save()

    def test_website_optional_and_available_in_form_and_admin(self):
        field = CRMDocumentSettings._meta.get_field('website')
        self.assertTrue(field.blank)
        self.assertEqual(field.default, '')
        self.assertEqual(field.verbose_name, 'Сайт')
        form = DocumentSettingsForm(instance=self.config)
        self.assertFalse(form.fields['website'].required)
        self.assertIn('website', admin.site._registry[CRMDocumentSettings].get_form(
            RequestFactory().get('/'), self.config).base_fields)
        self.config.refresh_from_db()
        self.assertEqual(self.config.website, 'tehsfera.by')

    def test_website_saved_by_existing_settings_screen(self):
        self.user.user_permissions.add(Permission.objects.get(codename='manage_crm_settings'))
        response = self.client.post(reverse('crm:settings'), {
            'action': 'save_documents', 'section': 'documents',
            'documents-workshop_name': self.config.workshop_name,
            'documents-website': 'tehsfera.by', 'documents-phone': self.config.phone,
            'documents-email': self.config.email, 'documents-address': self.config.address,
        })
        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.website, 'tehsfera.by')
        self.assertEqual(self.config.email, 'info@example.com')
        self.assertEqual(self.config.address, 'Тестовый адрес')
        self.assertContains(self.client.get(reverse('crm:settings') + '?section=documents'), 'name="documents-website"')

    def test_contacts_and_status_url_in_two_receipts_and_one_warranty(self):
        line = '<div class="document-contacts">+375 29 000-00-00 · tehsfera.by · info@example.com</div>'
        for route, copies in (('crm:receipt', 2), ('crm:warranty', 1)):
            response = self.client.get(reverse(route, args=[self.repair.pk]))
            self.assertContains(response, line, count=copies)
            self.assertContains(response, 'https://tehsfera.by/repair-status/', count=copies)
            self.assertContains(response, self.config.address, count=copies)
            self.assertNotContains(response, 'http://testserver/repair-status/')
        self.assertContains(self.client.get(reverse('crm:receipt', args=[self.repair.pk])), 'class="receipt-cut"', count=1)
        warranty = self.client.get(reverse('crm:warranty', args=[self.repair.pk]))
        self.assertContains(warranty, 'class="warranty-cut"', count=1)
        self.assertNotContains(warranty, 'Экземпляр сервиса')

    def test_empty_contacts_and_fallback_remain_valid(self):
        self.config.phone = self.config.email = self.config.website = ''
        self.config.save()
        for route in ('crm:receipt', 'crm:warranty'):
            response = self.client.get(reverse(route, args=[self.repair.pk]))
            self.assertNotContains(response, '<div class="document-contacts">')
            self.assertContains(response, 'http://testserver/repair-status/')

    def test_long_receipt_terms_are_preserved_and_escaped_in_both_copies(self):
        self.config.receipt_terms = 'Тест длинных условий. ' * 200 + '<script>test</script>'
        self.config.save()
        response = self.client.get(reverse('crm:receipt', args=[self.repair.pk]))
        self.assertContains(response, 'Тест длинных условий.', count=400)
        self.assertContains(response, '&lt;script&gt;test&lt;/script&gt;', count=2)
        self.assertNotContains(response, 'overflow:hidden')

    def test_act_appearance_does_not_depend_on_new_contact_field(self):
        response = self.client.get(reverse('crm:act', args=[self.repair.pk]))
        self.assertContains(response, 'Акт выполненных работ')
        self.assertContains(response, self.config.email)
        self.assertNotContains(response, 'tehsfera.by')
        self.assertNotContains(response, 'document-contacts')
        self.assertNotContains(response, 'warranty-cut')
