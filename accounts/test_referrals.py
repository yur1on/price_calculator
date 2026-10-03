from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import AccountProfile
from notify_tg.models import PartnerTelegram
from django.utils import timezone
from repairs.models import ReferralPartner, ReferralRedemption
from repairs import test_referrals as fixtures


class ClientReferralTests(TestCase):
    appointment = fixtures.ReferralFoundationTests.appointment
    operation = fixtures.ReferralFoundationTests.operation

    def setUp(self):
        fixtures.ReferralFoundationTests.setUp(self)
        self.url = reverse('accounts:client_referrals')
        self.client.force_login(self.user)

    def link(self):
        self.profile.referral_partner = self.partner
        self.profile.save()

    def test_anonymous_redirect(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_master_denied(self):
        self.profile.role = 'master'; self.profile.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_staff_client_denied(self):
        self.user.is_staff = True; self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_inactive_user_no_access(self):
        self.user.is_active = False; self.user.save()
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def test_onboarding_get_creates_nothing(self):
        before = ReferralPartner.objects.count()
        response = self.client.get(self.url)
        self.assertContains(response, 'Стать участником')
        self.assertNotContains(response, 'Ваш реферальный код')
        self.assertNotContains(response, 'История операций')
        self.assertEqual(ReferralPartner.objects.count(), before)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.referral_partner_id)

    def test_unverified_cannot_enroll(self):
        self.profile.phone_verified_at = None; self.profile.save()
        self.assertNotContains(self.client.get(self.url), 'Стать участником')
        self.client.post(self.url)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.referral_partner_id)
        self.assertEqual(ReferralPartner.objects.count(), 1)

    @override_settings(DEBUG=False)
    def test_production_does_not_promise_sms(self):
        self.profile.phone_verified_at = None; self.profile.save()
        response = self.client.get(self.url)
        self.assertContains(response, 'Отправка SMS через сайт пока недоступна')
        self.assertNotContains(response, 'href="'+reverse('accounts:phone_verify')+'"')

    @override_settings(DEBUG=True)
    def test_local_verification_link(self):
        self.profile.phone_verified_at = None; self.profile.save()
        self.assertContains(self.client.get(self.url), 'href="'+reverse('accounts:phone_verify')+'"')

    def test_enroll_post_idempotent(self):
        self.profile.phone = '+375291110000'; self.profile.save()
        self.assertRedirects(self.client.post(self.url), self.url)
        self.profile.refresh_from_db(); partner_id = self.profile.referral_partner_id
        self.client.post(self.url)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.referral_partner_id, partner_id)
        self.assertEqual(ReferralPartner.objects.count(), 2)

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(client.post(self.url).status_code, 403)

    def test_existing_safe_link_only_on_post_preserves_code(self):
        PartnerTelegram.objects.create(partner=self.partner, chat_id=123, verified_phone=self.profile.phone,
                                       phone_verified_at=timezone.now())
        self.client.get(self.url)
        self.profile.refresh_from_db(); self.assertIsNone(self.profile.referral_partner_id)
        self.client.post(self.url)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.referral_partner_id, self.partner.pk)
        self.assertEqual(self.profile.referral_partner.code, 'EXIST123')
        self.assertEqual(ReferralPartner.objects.count(), 1)

    def test_ambiguous_existing_owner_not_disclosed(self):
        ReferralPartner.objects.create(name='OTHER PRIVATE', code='OTHERSECRET', contact=self.profile.phone)
        response = self.client.post(self.url, follow=True)
        self.assertNotContains(response, 'OTHERSECRET')
        self.assertNotContains(response, 'OTHER PRIVATE')
        self.profile.refresh_from_db(); self.assertIsNone(self.profile.referral_partner_id)

    def test_own_code_and_zero_balance(self):
        self.link(); response = self.client.get(self.url)
        self.assertContains(response, self.partner.code)
        self.assertEqual(response.context['balance']['available_balance'], Decimal('0'))

    def test_balance_pending_and_spent(self):
        self.link()
        self.operation('50', 'accrued'); self.operation('15', 'pending'); self.operation('-20', 'paid')
        response = self.client.get(self.url)
        self.assertEqual(response.context['balance']['available_balance'], Decimal('30'))
        self.assertEqual(response.context['balance']['pending_balance'], Decimal('15'))
        self.assertContains(response, 'Использовано')

    def test_other_wallet_query_parameters_and_post_ignored(self):
        self.link()
        other = ReferralPartner.objects.create(name='OTHER PRIVATE', code='OTHERSECRET')
        ReferralRedemption.objects.create(partner=other, appointment=self.appointment(), phone='OTHERPHONE',
            commission_amount='9999', discount_amount=0, status='accrued')
        params = {'code': other.code, 'partner': other.pk, 'id': other.pk, 'referral_partner_id': other.pk}
        response = self.client.get(self.url, params)
        self.assertEqual(response.context['balance']['available_balance'], 0)
        self.assertNotContains(response, other.code)
        self.assertNotContains(response, '9999')
        self.client.post(self.url, params)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.referral_partner_id, self.partner.pk)

    def test_individual_rates_and_decimal_example(self):
        self.link()
        self.partner.client_discount_pct = Decimal('7'); self.partner.partner_commission_pct = Decimal('3'); self.partner.save()
        response = self.client.get(self.url)
        self.assertContains(response, 'скидку 7%')
        self.assertContains(response, 'начисляется 3%')
        self.assertEqual(response.context['example'], dict(base=Decimal('200'), discount=Decimal('14'), payable=Decimal('186'), commission=Decimal('6')))
        self.assertIsInstance(response.context['example']['commission'], Decimal)

    def test_history_has_no_friend_or_internal_identifiers(self):
        self.link(); self.operation('10', 'pending')
        response = self.client.get(self.url)
        for secret in ('Private friend', '+375299999999', 'PRIVATE-IMEI', 'private issue', '/crm/orders/', 'appointment_id'):
            self.assertNotContains(response, secret)
        self.assertEqual(set(response.context['history'][0]), {'date','kind','status','amount','sign'})

    def test_cancelled_and_positive_paid_are_distinct(self):
        self.link(); self.operation('11', 'cancelled'); self.operation('12', 'paid')
        response = self.client.get(self.url)
        self.assertContains(response, 'Отменено'); self.assertContains(response, 'Выплачено')
        self.assertEqual(response.context['balance']['available_balance'], 0)

    def test_pagination_is_scoped_and_read_only(self):
        self.link()
        for _ in range(14): self.operation('1', 'pending')
        before = list(ReferralRedemption.objects.values())
        first = self.client.get(self.url)
        second = self.client.get(self.url, {'page': 2})
        self.assertEqual(len(first.context['history']), 12)
        self.assertEqual(len(second.context['history']), 2)
        self.assertContains(first, '?page=2#ref-history-title')
        self.assertEqual(list(ReferralRedemption.objects.values()), before)

    def test_share_text_contains_only_code_and_discount(self):
        self.link(); response = self.client.get(self.url)
        share = response.context['share_text']
        self.assertIn(self.partner.code, share)
        for private in (self.partner.name, self.profile.phone, self.user.username, 'localhost', '/crm/'):
            self.assertNotIn(private, share)

    def test_wrong_http_method(self):
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_navigation_and_accessible_actions(self):
        self.link(); response = self.client.get(self.url)
        self.assertContains(response, 'href="'+self.url+'" class="is-active"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'Скопировать код')

    def test_referrals_reuse_client_components(self):
        self.link()
        response = self.client.get(self.url)
        self.assertContains(response, 'class="client-title"')
        self.assertContains(response, 'class="client-panel ref-balance"')
        self.assertNotContains(response, '<header class="ref-header"')

    def test_appointments_reuse_client_header_and_empty_state(self):
        response = self.client.get(reverse('accounts:client_appointments'))
        self.assertContains(response, 'class="client-title"')
        self.assertContains(response, 'class="client-empty"')
        self.assertNotContains(response, '<header class="client-title"')
