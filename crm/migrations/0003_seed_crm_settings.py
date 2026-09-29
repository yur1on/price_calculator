from django.db import migrations


def seed_settings(apps, schema_editor):
    DeviceType = apps.get_model("crm", "CRMDeviceType")
    WarrantyTerm = apps.get_model("crm", "CRMWarrantyTerm")

    for sort_order, name in enumerate(
        ["Телефон", "Планшет", "Ноутбук", "Смарт-часы", "Другое"],
        start=10,
    ):
        DeviceType.objects.get_or_create(
            name=name,
            defaults={"sort_order": sort_order * 10, "is_active": True},
        )

    for sort_order, days in enumerate([14, 30, 60, 90, 120], start=10):
        WarrantyTerm.objects.get_or_create(
            days=days,
            defaults={
                "label": f"{days} дней",
                "sort_order": sort_order * 10,
                "is_active": True,
                "is_default": days == 90,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("crm", "0002_crmbrand_crmdevicetype_crmwarrantyterm_and_more")]

    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
