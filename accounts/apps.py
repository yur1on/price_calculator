from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Аккаунты Tehsfera"

    def ready(self):
        from django.db.models.signals import post_migrate
        from .services import configure_groups
        post_migrate.connect(lambda **kwargs: configure_groups(), sender=self, weak=False)
