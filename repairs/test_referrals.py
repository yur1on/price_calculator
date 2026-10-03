from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from crm.models import CRMClient, CRMDevice, CRMOrder
from crm.services import change_status
from notify_tg.models import PartnerTelegram
from notify_tg.referrals import confirm_owner, verified_partner
from notify_tg.management.commands import run_tg_bot as bot
from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType, ReferralPartner, ReferralRedemption
from repairs.referrals import enroll_client, link_existing_participant, referral_balance, referral_operations


class ReferralFoundationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ref-client")
        self.profile = AccountProfile.objects.create(user=self.user, phone="+375291234567", phone_verified_at=timezone.now())
        self.partner = ReferralPartner.objects.create(name="Owner", contact="+375291234567", code="EXIST123")
        brand = PhoneBrand.objects.create(name="Brand", slug="brand")
        self.model = PhoneModel.objects.create(brand=brand, name="Model", slug="model")
        self.kind = RepairType.objects.create(name="Repair", slug="repair", default_duration_min=60)
        self.customer = CRMClient.objects.create(name="Private friend", phone="+375299999999")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="phone", imei="PRIVATE-IMEI")

    def appointment(self, **kwargs):
        now = timezone.now()
        combo = kwargs.pop("combo", None)
        values = dict(phone_model=self.model, repair_type=self.kind, start=now, end=now+timedelta(hours=1),
                      customer_name="Private friend", customer_phone="+375299999999", price_original=Decimal("200"))
        values.update(kwargs)
        app = Appointment(**values)
        app.apply_referral(services_count=1, combo_discount_amount=combo)
        app.save()
        app.refresh_from_db()
        return app

    def order(self, app, **kwargs):
        return CRMOrder.objects.create(client=self.customer, device=self.device, source_appointment=app,
                                       issue_description="private issue", **kwargs)

    def operation(self, amount, status):
        return ReferralRedemption.objects.create(partner=self.partner, appointment=self.appointment(),
            phone="private", discount_amount=0, commission_amount=Decimal(amount), status=status)

    def verify_tg(self, **kwargs):
        values = dict(chat_id=123, user_id=123, contact_user_id=123, phone=self.partner.contact, name="Owner")
        values.update(kwargs)
        return confirm_owner(**values)

    def test_profile_can_have_no_partner(self):
        self.assertIsNone(self.profile.referral_partner)

    def test_partner_link_is_unique(self):
        self.profile.referral_partner = self.partner
        self.profile.save()
        other = get_user_model().objects.create_user("other")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccountProfile.objects.create(user=other, referral_partner=self.partner)

    def test_existing_partner_link_preserves_code_and_operations(self):
        row = self.operation("10", "accrued")
        self.verify_tg()
        self.assertEqual(link_existing_participant(self.user), self.partner)
        self.assertEqual(link_existing_participant(self.user), self.partner)
        self.partner.refresh_from_db(); row.refresh_from_db()
        self.assertEqual(self.partner.code, "EXIST123")
        self.assertEqual(row.commission_amount, Decimal("10"))

    def test_no_match_does_not_link(self):
        self.profile.phone = "+375291110000"; self.profile.save()
        self.assertIsNone(link_existing_participant(self.user))

    def test_unverified_contact_is_not_proof(self):
        self.assertIsNone(link_existing_participant(self.user))

    def test_ambiguous_phone_not_linked_or_duplicated(self):
        self.verify_tg()
        ReferralPartner.objects.create(name="duplicate", contact=self.partner.contact, code="DUP123")
        self.assertIsNone(link_existing_participant(self.user))
        with self.assertRaises(ValidationError): enroll_client(self.user)
        self.assertEqual(ReferralPartner.objects.count(), 2)

    def test_new_participant_is_idempotent_and_uses_defaults(self):
        self.profile.phone = "+375291110000"; self.profile.save()
        partner = enroll_client(self.user)
        self.assertEqual(enroll_client(self.user).pk, partner.pk)
        self.assertRegex(partner.code, r"^[A-Z0-9]{8}$")
        self.assertEqual(partner.client_discount_pct, Decimal("5"))
        self.assertEqual(partner.partner_commission_pct, Decimal("5"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.referral_partner, partner)

    def test_unverified_phone_cannot_enroll(self):
        self.profile.phone_verified_at = None; self.profile.save()
        with self.assertRaises(ValidationError): enroll_client(self.user)
        self.assertEqual(ReferralPartner.objects.count(), 1)

    def test_master_cannot_enroll(self):
        self.profile.role = "master"; self.profile.save()
        with self.assertRaises(ValidationError): enroll_client(self.user)

    def test_short_phone_not_used_for_ownership(self):
        self.profile.phone = "291234567"; self.profile.save()
        with self.assertRaises(ValidationError): link_existing_participant(self.user)

    def test_partner_already_owned_cannot_be_claimed(self):
        self.verify_tg()
        other = get_user_model().objects.create_user("owner2")
        AccountProfile.objects.create(user=other, referral_partner=self.partner)
        self.assertIsNone(link_existing_participant(self.user))

    def test_shared_balance_matches_legacy_formula(self):
        for amount, status in [("10", "pending"), ("50", "accrued"), ("20", "paid"), ("-15", "paid"), ("30", "cancelled")]:
            self.operation(amount, status)
        balance = referral_balance(self.partner.pk)
        self.assertEqual(balance["available_balance"], Decimal("35"))
        self.assertEqual(balance["pending_balance"], Decimal("10"))
        self.assertEqual(balance["accrued_balance"], Decimal("50"))
        self.assertEqual(balance["used_balance"], Decimal("15"))
        self.assertEqual(async_to_sync(bot.db_calc_balance)(self.partner.pk)["available"], Decimal("35"))

    def test_full_credit_payment_preserved(self):
        self.operation("250", "accrued")
        app = self.appointment(customer_phone=self.partner.contact)
        self.assertEqual(app.price_final, Decimal("0"))
        self.assertEqual(app.referrals.get().commission_amount, Decimal("-200"))
        self.assertEqual(referral_balance(self.partner.pk)["available_balance"], Decimal("50"))
        app.save(); app.refresh_from_db()
        self.assertEqual(app.price_final, Decimal("0"))
        self.assertEqual(app.referrals.count(), 1)

    def test_self_referral_does_not_award(self):
        app = self.appointment(customer_phone=self.partner.contact, referral_code=self.partner.code)
        self.assertEqual(app.price_final, Decimal("190"))
        self.assertFalse(app.referrals.exists())

    def test_individual_percentages_and_combo_base(self):
        self.partner.client_discount_pct = Decimal("7")
        self.partner.partner_commission_pct = Decimal("3")
        self.partner.save()
        app = self.appointment(referral_code=self.partner.code)
        row = app.referrals.get()
        self.assertEqual(app.price_final, Decimal("186"))
        self.assertEqual(row.commission_amount, Decimal("6"))
        self.assertIn("7%", bot.rules_text(self.partner.code, self.partner))

    def test_expired_code_has_no_discount_or_award(self):
        self.partner.expires_at = timezone.now()-timedelta(days=1); self.partner.save()
        app = self.appointment(referral_code=self.partner.code)
        self.assertEqual(app.price_final, Decimal("200"))
        self.assertFalse(app.referrals.exists())

    def test_combo_is_deducted_before_both_percentages(self):
        app = self.appointment(referral_code=self.partner.code, combo=Decimal("20"))
        self.assertEqual(app.price_final, Decimal("171"))
        self.assertEqual(app.referrals.get().commission_amount, Decimal("9"))

    def test_later_activation_does_not_create_unearned_historical_award(self):
        self.partner.max_uses = 0; self.partner.save()
        app = self.appointment(referral_code=self.partner.code)
        self.partner.max_uses = None; self.partner.save()
        app.save()
        self.assertFalse(app.referrals.exists())

    def test_cancel_spend_keeps_existing_refund_mechanism(self):
        self.operation("250", "accrued")
        app = self.appointment(customer_phone=self.partner.contact)
        app.status = "cancelled"; app.save(); app.refresh_from_db()
        self.assertEqual(app.price_final, Decimal("200"))
        self.assertEqual(referral_balance(self.partner.pk)["available_balance"], Decimal("250"))

    def test_temporary_code_cannot_award(self):
        self.partner.code = "PEND123456789012"; self.partner.save()
        app = self.appointment(referral_code=self.partner.code)
        self.assertEqual(app.price_final, Decimal("200"))
        self.assertFalse(app.referrals.exists())

    def test_new_site_participant_is_reused_by_telegram(self):
        self.profile.phone = "+375291110000"; self.profile.save()
        partner = enroll_client(self.user)
        telegram_partner = self.verify_tg(phone=self.profile.phone)
        self.assertEqual(telegram_partner.pk, partner.pk)
        self.assertEqual(telegram_partner.code, partner.code)

    def test_telegram_ambiguous_phone_does_not_claim_first(self):
        ReferralPartner.objects.create(name="duplicate", contact=self.partner.contact, code="DUP123")
        with self.assertRaises(ValidationError): self.verify_tg()
        self.assertFalse(PartnerTelegram.objects.exists())

    def test_legacy_pending_partner_completes_without_duplicate_wallet(self):
        self.partner.code = "PEND123456789012"; self.partner.contact = "@old"; self.partner.save()
        PartnerTelegram.objects.create(partner=self.partner, chat_id=123)
        partner = self.verify_tg(phone=self.profile.phone)
        self.assertEqual(partner.pk, self.partner.pk)
        self.assertRegex(partner.code, r"^[A-Z0-9]{8}$")
        self.assertEqual(ReferralPartner.objects.count(), 1)

    def test_usage_limit_blocks_new_award_but_not_existing(self):
        self.partner.max_uses = 1; self.partner.save()
        first = self.appointment(referral_code=self.partner.code)
        second = self.appointment(referral_code=self.partner.code)
        self.assertEqual(first.referrals.count(), 1)
        self.assertFalse(second.referrals.exists())
        order = self.order(first, status="ready")
        change_status(order, "issued", self.user)
        self.assertEqual(first.referrals.get().status, "accrued")

    def test_intake_done_is_pending_not_available(self):
        app = self.appointment(referral_code=self.partner.code)
        self.order(app)
        app.status = "done"; app.save()
        self.assertEqual(app.referrals.get().status, "pending")
        self.assertEqual(referral_balance(self.partner.pk)["available_balance"], 0)

    def test_legacy_done_without_crm_is_not_completion(self):
        app = self.appointment(referral_code=self.partner.code, status="done")
        self.assertEqual(app.referrals.get().status, "pending")

    def test_issued_accrues_once_and_freezes_amount(self):
        app = self.appointment(referral_code=self.partner.code)
        order = self.order(app, status="ready")
        self.assertEqual(app.referrals.get().status, "pending")
        change_status(order, "issued", self.user)
        order.save(); order.save()
        app.price_original = 500; app.save()
        self.assertEqual(app.referrals.count(), 1)
        self.assertEqual(app.referrals.get().status, "accrued")
        self.assertEqual(app.referrals.get().commission_amount, Decimal("10"))

    def test_cancel_appointment_retains_cancelled_history(self):
        app = self.appointment(referral_code=self.partner.code)
        app.status = "cancelled"; app.save()
        self.assertEqual(app.referrals.get().status, "cancelled")
        self.assertEqual(referral_balance(self.partner.pk)["pending_balance"], 0)
        self.order(app, status="issued", issued_at=timezone.now())
        self.assertEqual(app.referrals.get().status, "cancelled")

    def test_cancel_order_excludes_pending_and_cannot_reaccrue(self):
        app = self.appointment(referral_code=self.partner.code)
        order = self.order(app)
        change_status(order, "canceled", self.user)
        app.status = "done"; app.save()
        self.assertEqual(app.referrals.get().status, "cancelled")
        self.assertEqual(referral_balance(self.partner.pk)["available_balance"], 0)

    def test_historical_paid_amount_untouched(self):
        app = self.appointment(referral_code=self.partner.code)
        row = app.referrals.get(); row.status = "paid"; row.save()
        app.price_original = 999; app.save()
        row.refresh_from_db()
        self.assertEqual(row.status, "paid")
        self.assertEqual(row.commission_amount, Decimal("10"))

    def test_reports_require_staff_and_permission(self):
        urls = [reverse("repairs:referrals_report"), reverse("repairs:referrals_partner_report", args=[self.partner.code])]
        for url in urls: self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        for url in urls: self.assertEqual(self.client.get(url).status_code, 403)
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        for url in urls: self.assertEqual(self.client.get(url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_referralredemption"))
        for url in urls: self.assertEqual(self.client.get(url).status_code, 200)

    def test_phone_without_user_id_or_foreign_contact_rejected(self):
        for contact_id in (None, 456):
            with self.assertRaises(ValidationError): self.verify_tg(contact_user_id=contact_id)
        self.assertFalse(PartnerTelegram.objects.exists())

    def test_group_contact_rejected(self):
        with self.assertRaises(ValidationError): self.verify_tg(chat_id=-123)

    def test_legacy_binding_requires_new_proof(self):
        PartnerTelegram.objects.create(partner=self.partner, chat_id=123)
        self.assertIsNone(verified_partner(123))
        self.assertEqual(self.verify_tg(), self.partner)
        self.assertEqual(verified_partner(123), self.partner)
        self.assertEqual(self.partner.code, "EXIST123")

    def test_wrong_phone_cannot_take_existing_binding(self):
        PartnerTelegram.objects.create(partner=self.partner, chat_id=123)
        with self.assertRaises(ValidationError): self.verify_tg(phone="+375291110000")
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.contact, "+375291234567")

    def test_changed_partner_phone_revokes_telegram_access(self):
        self.verify_tg()
        self.partner.contact = "+375291110000"; self.partner.save()
        self.assertIsNone(verified_partner(123))

    def test_new_telegram_owner_gets_same_wallet_on_repeat(self):
        values = dict(chat_id=456, user_id=456, contact_user_id=456, phone="+375291110000")
        partner = self.verify_tg(**values)
        self.assertEqual(self.verify_tg(**values).pk, partner.pk)
        self.assertEqual(ReferralPartner.objects.count(), 2)

    def test_code_argument_does_not_link_chat(self):
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=456))
        context = SimpleNamespace(args=[self.partner.code])
        with patch.object(bot, "_reply", new_callable=AsyncMock) as reply:
            async_to_sync(bot.cmd_start)(update, context)
            self.assertFalse(reply.call_args.kwargs["full_keyboard"])
            async_to_sync(bot.cmd_link)(update, context)
        self.assertFalse(PartnerTelegram.objects.exists())

    def test_confirmed_owner_commands_share_safe_data(self):
        self.verify_tg()
        self.appointment(referral_code=self.partner.code)
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=123), message=SimpleNamespace(text=""))
        with patch.object(bot, "_reply", new_callable=AsyncMock) as reply:
            for command in (bot.BTN_MY_CODE, bot.BTN_BALANCE, bot.BTN_REPORT):
                update.message.text = command
                async_to_sync(bot.on_text)(update, SimpleNamespace())
                text = reply.call_args.args[1]
                self.assertTrue(reply.call_args.kwargs["full_keyboard"])
                for secret in ("Private friend", "+375299999999", "PRIVATE-IMEI", "private issue"):
                    self.assertNotIn(secret, text)
        self.assertNotIn("appointment_id", referral_operations(self.partner.pk)[0])

    def test_referral_notification_contains_no_friend_details(self):
        with patch("repairs.signals.notify_partner") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                self.appointment(referral_code=self.partner.code)
        text = notify.call_args.args[1]
        for secret in ("Private friend", "+375299999999", "Model", "Заявка #"):
            self.assertNotIn(secret, text)
