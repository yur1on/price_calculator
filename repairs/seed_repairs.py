"""Создать примерные данные для приложения repairs.

Команда заполняет БД брендами, моделями (телефоны/планшеты/часы),
типами ремонта, ценами и часами работы, чтобы сразу протестировать
полный сценарий бронирования.

Запуск:
    python manage.py seed_repairs
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from repairs.models import (
    PhoneBrand,
    PhoneModel,
    RepairType,
    ModelRepairPrice,
    WorkingHour,
    # необязательно, но если модель есть — создадим пару партнёров
    # ReferralPartner,
)


class Command(BaseCommand):
    help = "Заполняет базу примерными данными (repairs)"

    def handle(self, *args, **options):
        # -----------------------------
        # 1) БРЕНДЫ И МОДЕЛИ (с категориями)
        # -----------------------------
        # Категории: phone | tablet | watch
        brands = {
            "Apple": [
                ("iPhone 13", "phone"),
                ("iPad Air (4th gen)", "tablet"),
                ("Watch Series 7", "watch"),
            ],
            "Samsung": [
                ("Galaxy S23", "phone"),
                ("Galaxy Tab S8", "tablet"),
                ("Galaxy Watch 5", "watch"),
            ],
            "Xiaomi": [
                ("Mi 11", "phone"),
                ("Pad 5", "tablet"),
                ("Mi Watch", "watch"),
            ],
        }

        for brand_name, items in brands.items():
            brand, _ = PhoneBrand.objects.get_or_create(
                name=brand_name,
                defaults={"slug": brand_name.lower().replace(" ", "-")},
            )
            for model_name, category in items:
                # аккуратный слаг: нижний регистр, дефисы, без скобок
                slug = (
                    model_name.lower()
                    .replace(" ", "-")
                    .replace("(", "")
                    .replace(")", "")
                )
                PhoneModel.objects.update_or_create(
                    brand=brand,
                    name=model_name,
                    defaults={"slug": slug, "category": category},
                )

        self.stdout.write(self.style.SUCCESS("Бренды и модели: ок"))

        # -----------------------------
        # 2) ТИПЫ РЕМОНТА
        # -----------------------------
        repairs = [
            ("Замена экрана", 60, "screen"),
            ("Замена батареи", 45, "battery"),
            ("Ремонт зарядного порта", 45, "charging-port"),
        ]
        repair_objs = []
        for name, duration, slug in repairs:
            obj, _ = RepairType.objects.get_or_create(
                name=name,
                defaults={"slug": slug, "default_duration_min": duration},
            )
            repair_objs.append(obj)

        self.stdout.write(self.style.SUCCESS("Типы ремонта: ок"))

        # -----------------------------
        # 3) ЦЕНЫ ДЛЯ КАЖДОЙ МОДЕЛИ × ТИП РЕМОНТА
        # -----------------------------
        # Немного отличающиеся демо-цены по категориям
        # (значения в вашей валюте отображаются в шаблонах)
        base_prices = {
            "screen": {"phone": 120, "tablet": 150, "watch": 100},
            "battery": {"phone": 80, "tablet": 95, "watch": 70},
            "charging-port": {"phone": 60, "tablet": 75, "watch": 65},
        }

        for phone_model in PhoneModel.objects.all():
            cat = getattr(phone_model, "category", "phone")
            for repair in repair_objs:
                slug = repair.slug  # 'screen' | 'battery' | 'charging-port'
                price_value = base_prices.get(slug, {}).get(cat, 80)
                ModelRepairPrice.objects.update_or_create(
                    phone_model=phone_model,
                    repair_type=repair,
                    defaults={
                        "price": price_value,
                        "duration_min": repair.default_duration_min,
                        "is_active": True,
                    },
                )

        self.stdout.write(self.style.SUCCESS("Цены по моделям: ок"))

        # -----------------------------
        # 4) ЧАСЫ РАБОТЫ (Пн–Пт 09:00–17:00, Сб 10:00–14:00)
        # -----------------------------
        WorkingHour.objects.all().delete()
        hours = [
            (0, "09:00", "17:00"),  # Пн
            (1, "09:00", "17:00"),
            (2, "09:00", "17:00"),
            (3, "09:00", "17:00"),
            (4, "09:00", "17:00"),
            (5, "10:00", "14:00"),  # Сб
            # Воскресенье — выходной
        ]
        for weekday, start_str, end_str in hours:
            start = timezone.datetime.strptime(start_str, "%H:%M").time()
            end = timezone.datetime.strptime(end_str, "%H:%M").time()
            WorkingHour.objects.create(weekday=weekday, start=start, end=end)

        self.stdout.write(self.style.SUCCESS("Часы работы: ок"))

        # -----------------------------
        # 5) (Опционально) ПАРТНЁРЫ С РЕФЕРАЛ-КОДАМИ
        # -----------------------------
        # Раскомментируйте блок ниже, если хотите заодно создать продавцов.
        """
        from decimal import Decimal
        from repairs.models import ReferralPartner

        partners = [
            # name, code, скидка клиенту %, комиссия партнёру %, срок
            ("Магазин «Альфа»", "ALFA5", Decimal("5.00"), Decimal("5.00"), None),
            ("Салон «Мобайл+»", "MOBI5", Decimal("5.00"), Decimal("5.00"), None),
        ]
        for name, code, disc, comm, exp in partners:
            ReferralPartner.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "client_discount_pct": disc,
                    "partner_commission_pct": comm,
                    "expires_at": exp,
                },
            )
        self.stdout.write(self.style.SUCCESS("Партнёры (реферальные коды): ок"))
        """

        self.stdout.write(self.style.SUCCESS("Примерные данные успешно созданы."))
