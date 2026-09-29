from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_roles_and_groups(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Profile = apps.get_model("accounts", "AccountProfile")
    Employee = apps.get_model("finance", "Employee")

    admin_group, _ = Group.objects.get_or_create(name="Tehsfera Admin")
    master_group, _ = Group.objects.get_or_create(name="Tehsfera Master")
    client_group, _ = Group.objects.get_or_create(name="Tehsfera Client")
    master_group.permissions.set(Permission.objects.filter(
        content_type__app_label="crm",
        codename__in=["access_crm", "view_crmorder", "view_crmclient", "view_crmdevice", "change_crmorder", "change_order_status"],
    ))
    admin_group.permissions.set(Permission.objects.filter(content_type__app_label__in=["crm", "finance"]))

    linked = {row.user_id: row for row in Employee.objects.exclude(user_id=None)}
    for user in User.objects.all():
        if user.is_superuser:
            role, status = "admin", "active"
        elif user.pk in linked:
            role, status = "master", "approved"
        else:
            role, status = "client", "active"
        profile, _ = Profile.objects.get_or_create(user_id=user.pk, defaults={"role": role, "approval_status": status})
        if role == "master": user.groups.add(master_group)
        elif role == "admin": user.groups.add(admin_group)
        else: user.groups.add(client_group)


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0003_seed_crm_settings"),
        ("finance", "0008_employee_user"),
    ]
    operations = [
        migrations.CreateModel(
            name="AccountProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("admin", "Администратор"), ("master", "Мастер"), ("client", "Клиент")], db_index=True, default="client", max_length=16, verbose_name="Роль")),
                ("approval_status", models.CharField(choices=[("active", "Активен"), ("pending", "Ожидает подтверждения"), ("approved", "Одобрен"), ("rejected", "Отклонён")], db_index=True, default="active", max_length=16, verbose_name="Статус")),
                ("phone", models.CharField(blank=True, max_length=40, verbose_name="Телефон")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлён")),
                ("crm_client", models.OneToOneField(blank=True, help_text="Назначается администратором после проверки владения. Автоматически по телефону не связывается.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="account_profile", to="crm.crmclient", verbose_name="Подтверждённый клиент CRM")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="account_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Профиль аккаунта", "verbose_name_plural": "Профили аккаунтов", "ordering": ["-created_at"]},
        ),
        migrations.RunPython(seed_roles_and_groups, migrations.RunPython.noop),
    ]
