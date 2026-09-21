from django.db import migrations


DEFAULT_CATEGORIES = [
    "Расходные материалы",
    "Аренда",
    "Реклама",
    "Инструмент",
    "Коммунальные платежи",
    "Доставка",
    "Налоги",
    "Хозяйственные расходы",
    "Прочее",
]


def seed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    for order, name in enumerate(DEFAULT_CATEGORIES, start=10):
        ExpenseCategory.objects.get_or_create(
            name=name,
            defaults={"sort_order": order * 10, "is_active": True},
        )


def unseed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    ExpenseCategory.objects.filter(name__in=DEFAULT_CATEGORIES, expenses__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("finance", "0001_initial")]
    operations = [migrations.RunPython(seed_categories, unseed_categories)]
