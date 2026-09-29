from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """Filesystem storage that deliberately has no public URL."""

    def __init__(self, location=None):
        default_location = settings.BASE_DIR / "private_media"
        super().__init__(location=location or getattr(settings, "PRIVATE_MEDIA_ROOT", default_location), base_url=None)

    def url(self, name):
        raise ValueError("Private files do not have a public URL.")


private_media_storage = PrivateMediaStorage()
