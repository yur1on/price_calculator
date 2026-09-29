import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from crm.models import CRMAttachment


def _safe_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe storage path")
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes storage root")
    return candidate


def _same_file(first, second):
    if first.stat().st_size != second.stat().st_size:
        return False
    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.digest()
    return digest(first) == digest(second)


class Command(BaseCommand):
    help = (
        "Move legacy CRM attachments from public MEDIA_ROOT to PRIVATE_MEDIA_ROOT. "
        "Back up the database and media directory before production migration."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report actions without changing files.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        public_root = Path(settings.MEDIA_ROOT)
        private_root = Path(getattr(settings, "PRIVATE_MEDIA_ROOT", settings.BASE_DIR / "private_media"))
        if public_root.resolve() == private_root.resolve() or public_root.resolve() in private_root.resolve().parents:
            raise CommandError("PRIVATE_MEDIA_ROOT must be outside MEDIA_ROOT.")

        stats = {"found": 0, "migrate": 0, "private": 0, "missing": 0, "conflicts": 0, "errors": 0}
        for attachment in CRMAttachment.objects.only("id", "file").iterator():
            stats["found"] += 1
            try:
                source = _safe_path(public_root, attachment.file.name)
                destination = _safe_path(private_root, attachment.file.name)
                source_exists, destination_exists = source.is_file(), destination.is_file()

                if destination_exists:
                    if source_exists and not _same_file(source, destination):
                        stats["conflicts"] += 1
                        self.stderr.write(self.style.WARNING(f"CONFLICT attachment #{attachment.pk}: {attachment.file.name}"))
                        continue
                    stats["private"] += 1
                    if source_exists and not dry_run:
                        source.unlink()
                    continue

                if not source_exists:
                    stats["missing"] += 1
                    self.stderr.write(self.style.WARNING(f"MISSING attachment #{attachment.pk}: {attachment.file.name}"))
                    continue

                stats["migrate"] += 1
                if dry_run:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary_name = tempfile.mkstemp(prefix=".crm-attachment-", dir=destination.parent)
                os.close(fd)
                temporary = Path(temporary_name)
                try:
                    shutil.copy2(source, temporary)
                    os.replace(temporary, destination)
                    source.unlink()
                finally:
                    temporary.unlink(missing_ok=True)
            except Exception as exc:
                stats["errors"] += 1
                self.stderr.write(self.style.ERROR(f"ERROR attachment #{attachment.pk}: {exc}"))

        mode = "DRY RUN" if dry_run else "DONE"
        self.stdout.write(
            f"{mode}: found={stats['found']} migrate={stats['migrate']} "
            f"already_private={stats['private']} missing={stats['missing']} "
            f"conflicts={stats['conflicts']} errors={stats['errors']}"
        )
