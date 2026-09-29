from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.models import CRMAttachment, CRMClient, CRMDevice, CRMOrder
from core.storage import private_media_storage
from finance.models import Employee


class PrivateAttachmentTests(TestCase):
    def setUp(self):
        self.public_dir = TemporaryDirectory()
        self.private_dir = TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.public_dir.name,
            PRIVATE_MEDIA_ROOT=self.private_dir.name,
        )
        self.settings_override.enable()
        self.old_private_location = private_media_storage._location
        private_media_storage._location = self.private_dir.name
        private_media_storage.__dict__.pop("base_location", None)
        private_media_storage.__dict__.pop("location", None)
        self.customer = CRMClient.objects.create(name="Клиент", phone="80291111111")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", model="A55")
        self.employee = Employee.objects.create(name="Мастер", default_percent="35")
        self.order = CRMOrder.objects.create(
            client=self.customer, device=self.device, employee=self.employee,
            issue_description="Тест", agreed_price="100",
        )
        self.uploader = get_user_model().objects.create_superuser("admin-private", "admin@example.com", "pass")
        self.attachment = CRMAttachment.objects.create(
            order=self.order, uploaded_by=self.uploader, original_name="internal.txt",
            file=SimpleUploadedFile("internal.txt", b"private-data", content_type="text/plain"),
        )

    def tearDown(self):
        private_media_storage._location = self.old_private_location
        private_media_storage.__dict__.pop("base_location", None)
        private_media_storage.__dict__.pop("location", None)
        self.settings_override.disable()
        self.private_dir.cleanup()
        self.public_dir.cleanup()

    def test_attachment_is_outside_public_media_and_has_no_public_url(self):
        self.assertFalse((Path(self.public_dir.name) / self.attachment.file.name).exists())
        self.assertTrue((Path(self.private_dir.name) / self.attachment.file.name).exists())
        with self.assertRaises(ValueError):
            _ = self.attachment.file.url
        self.assertEqual(self.client.get(f"/media/{self.attachment.file.name}").status_code, 404)

    def test_endpoint_permissions(self):
        url = reverse("crm:attachment_open", args=[self.attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 302)

        client_user = get_user_model().objects.create_user("client-private", password="pass")
        AccountProfile.objects.create(user=client_user, role=AccountProfile.Role.CLIENT)
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(url).status_code, 403)

        pending = get_user_model().objects.create_user("pending-private", password="pass")
        AccountProfile.objects.create(
            user=pending, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.PENDING,
        )
        self.client.force_login(pending)
        self.assertEqual(self.client.get(url).status_code, 403)

        rejected = get_user_model().objects.create_user("rejected-private", password="pass")
        AccountProfile.objects.create(
            user=rejected, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.REJECTED,
        )
        self.client.force_login(rejected)
        self.assertEqual(self.client.get(url).status_code, 403)

        master = get_user_model().objects.create_user("master-private", password="pass")
        profile = AccountProfile.objects.create(
            user=master, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.PENDING,
        )
        approve_master(profile)
        self.client.force_login(master)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.uploader)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_public_images_still_use_media_url(self):
        name = default_storage.save("news/cover/public.txt", ContentFile(b"public-image"))
        self.assertTrue((Path(self.public_dir.name) / name).exists())
        self.assertTrue(default_storage.url(name).startswith("/media/"))


class PrivateAttachmentMigrationCommandTests(TestCase):
    def setUp(self):
        self.public_dir = TemporaryDirectory()
        self.private_dir = TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.public_dir.name,
            PRIVATE_MEDIA_ROOT=self.private_dir.name,
        )
        self.settings_override.enable()
        customer = CRMClient.objects.create(name="Клиент", phone="80292222222")
        device = CRMDevice.objects.create(client=customer, device_type="Телефон", model="A55")
        order = CRMOrder.objects.create(client=customer, device=device, issue_description="Тест")
        uploader = get_user_model().objects.create_user("migration-user")
        self.name = "crm/orders/1/legacy.txt"
        self.attachment = CRMAttachment.objects.create(
            order=order, uploaded_by=uploader, original_name="legacy.txt", file=self.name,
        )

    def tearDown(self):
        self.settings_override.disable()
        self.private_dir.cleanup()
        self.public_dir.cleanup()

    def _public(self):
        return Path(self.public_dir.name) / self.name

    def _private(self):
        return Path(self.private_dir.name) / self.name

    def test_dry_run_does_not_move_file(self):
        self._public().parent.mkdir(parents=True)
        self._public().write_bytes(b"legacy")
        output = StringIO()
        call_command("migrate_private_crm_attachments", "--dry-run", stdout=output)
        self.assertIn("migrate=1", output.getvalue())
        self.assertTrue(self._public().exists())
        self.assertFalse(self._private().exists())

    def test_migration_is_successful_and_repeatable(self):
        self._public().parent.mkdir(parents=True)
        self._public().write_bytes(b"legacy")
        call_command("migrate_private_crm_attachments", stdout=StringIO())
        self.assertFalse(self._public().exists())
        self.assertEqual(self._private().read_bytes(), b"legacy")
        output = StringIO()
        call_command("migrate_private_crm_attachments", stdout=output)
        self.assertIn("already_private=1", output.getvalue())

    def test_missing_source_is_reported_and_does_not_abort(self):
        output, errors = StringIO(), StringIO()
        call_command("migrate_private_crm_attachments", stdout=output, stderr=errors)
        self.assertIn("missing=1", output.getvalue())
        self.assertIn("MISSING", errors.getvalue())

    def test_identical_existing_destination_is_treated_as_already_migrated(self):
        self._public().parent.mkdir(parents=True)
        self._private().parent.mkdir(parents=True)
        self._public().write_bytes(b"same")
        self._private().write_bytes(b"same")
        call_command("migrate_private_crm_attachments", stdout=StringIO())
        self.assertFalse(self._public().exists())
        self.assertEqual(self._private().read_bytes(), b"same")

    def test_conflicting_destination_is_not_overwritten(self):
        self._public().parent.mkdir(parents=True)
        self._private().parent.mkdir(parents=True)
        self._public().write_bytes(b"old-public")
        self._private().write_bytes(b"unknown-private")
        output, errors = StringIO(), StringIO()
        call_command("migrate_private_crm_attachments", stdout=output, stderr=errors)
        self.assertIn("conflicts=1", output.getvalue())
        self.assertIn("CONFLICT", errors.getvalue())
        self.assertEqual(self._public().read_bytes(), b"old-public")
        self.assertEqual(self._private().read_bytes(), b"unknown-private")
