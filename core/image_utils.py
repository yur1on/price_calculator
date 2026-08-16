from __future__ import annotations

from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def convert_field_image_to_webp(instance, field_name: str, quality: int = 85) -> None:
    field_file = getattr(instance, field_name, None)
    if not field_file or getattr(field_file, "_committed", True):
        return

    field_file.seek(0)
    with Image.open(field_file) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

        buffer = BytesIO()
        save_kwargs = {"format": "WEBP", "quality": quality, "method": 6}
        if img.mode == "RGBA":
            save_kwargs["lossless"] = False
        img.save(buffer, **save_kwargs)

    original_name = Path(field_file.name or field_name).stem or field_name
    webp_name = f"{original_name}.webp"
    getattr(instance, field_name).save(webp_name, ContentFile(buffer.getvalue()), save=False)
