from django.test import TestCase, override_settings
from django.urls import reverse
from repairs.models import PhoneBrand, PhoneModel, RepairType, ModelRepairPrice
from repairs.templatetags.repairs_extras import display_model_name


class TargetedSEOTests(TestCase):
    def setUp(self):
        self.apple = PhoneBrand.objects.create(name='Apple', slug='apple')
        self.google = PhoneBrand.objects.create(name='Google', slug='google')
        self.xr = PhoneModel.objects.create(brand=self.apple, name='Iphone Xr', slug='ip')
        self.xs = PhoneModel.objects.create(brand=self.apple, name='iphone Xs Max', slug='6s-plus')
        self.pixel = PhoneModel.objects.create(brand=self.google, name='Pixel', slug='pixel')

    def test_home_brand_context_and_preserved_heading(self):
        r = self.client.get('/')
        self.assertContains(r, '<h1>Ремонт телефонов<br>и техники в Гомеле</h1>')
        self.assertContains(r, '<title>Ремонт телефонов и техники в Гомеле — Tehsfera</title>')
        self.assertContains(r, 'Ремонтируем iPhone, Google Pixel, Samsung, Xiaomi')
        self.assertContains(r, 'content="Ремонт iPhone, Google Pixel, Samsung, Xiaomi')
        self.assertContains(r, '<strong>Apple iPhone</strong>')
        self.assertContains(r, '<strong>Google Pixel</strong>')
        self.assertContains(r, 'href="/repairs/apple/?cat=phone"')

    def test_brand_phone_metadata(self):
        for slug, label in [('apple', 'iPhone'), ('google', 'Google Pixel')]:
            r = self.client.get(f'/repairs/{slug}/?cat=phone')
            self.assertContains(r, f'<title>Ремонт {label} в Гомеле — модели и цены | Tehsfera</title>')
            self.assertContains(r, f'<h1 class="brand-title">Ремонт {label} в Гомеле</h1>', html=True)
            self.assertContains(r, f'href="http://testserver/repairs/{slug}/?cat=phone"')
            self.assertContains(r, 'content="index, follow"')

    def test_other_categories_are_not_renamed(self):
        for brand in [self.apple, self.google]:
            for category, label in [('tablet', 'планшетов'), ('watch', 'смарт-часов')]:
                PhoneModel.objects.create(brand=brand, name=category, slug=brand.slug+category, category=category)
                r = self.client.get(f'/repairs/{brand.slug}/?cat={category}')
                self.assertContains(r, f'Ремонт {label} {brand.name} в Гомеле')
                self.assertNotContains(r, 'Ремонт iPhone в Гомеле')
                self.assertNotContains(r, 'Ремонт Google Pixel в Гомеле')
                home = self.client.get('/', {'cat': category})
                self.assertContains(home, f'<strong>{brand.name}</strong>')

    def test_catalog_heading(self):
        self.assertContains(self.client.get('/repairs/?cat=phone'), '<h1>Стоимость ремонта телефонов в Гомеле</h1>')

    def test_sitemap_unique_brands_and_models(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.client.get('/sitemap.xml').content)
        urls = [node.text for node in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        self.assertEqual(len(urls), len(set(urls)))
        for path in ['/repairs/apple/?cat=phone', '/repairs/apple/ip/', '/repairs/apple/6s-plus/', '/repairs/google/pixel/']:
            self.assertTrue(any(url.endswith(path) for url in urls), path)

    def test_normalization_does_not_mutate_data(self):
        for model, expected in [(self.xr, 'iPhone XR'), (self.xs, 'iPhone XS Max')]:
            original = (model.name, model.slug)
            r = self.client.get(f'/repairs/apple/{model.slug}/')
            self.assertContains(r, f'<h1>Apple {expected} — ремонт</h1>')
            self.assertContains(r, f'Ремонт Apple {expected}')
            self.assertContains(r, f'href="http://testserver/repairs/apple/{model.slug}/"')
            model.refresh_from_db()
            self.assertEqual((model.name, model.slug), original)
        self.assertEqual(display_model_name('Galaxy Xr'), 'Galaxy Xr')
        self.assertContains(self.client.get('/repairs/apple/'), 'iPhone XS Max')

    def test_back_preserves_each_category(self):
        for category in ['phone', 'tablet', 'watch']:
            model = PhoneModel.objects.create(brand=self.apple, name=category, slug=category, category=category)
            self.assertContains(self.client.get(f'/repairs/apple/{model.slug}/'), f'href="/repairs/apple/?cat={category}"')

    def test_empty_services_do_not_promise_prices(self):
        r = self.client.get('/repairs/apple/ip/')
        self.assertContains(r, 'Для этой модели стоимость ремонта уточняется.')
        self.assertContains(r, 'href="/repairs/contacts/"')
        self.assertNotContains(r, 'актуальные цены')
        self.assertNotContains(r, 'Цены на ремонт')

    def test_active_services_keep_price_copy(self):
        kind = RepairType.objects.create(name='Экран', slug='screen')
        ModelRepairPrice.objects.create(phone_model=self.xr, repair_type=kind, price=100, duration_min=60)
        r = self.client.get('/repairs/apple/ip/')
        self.assertContains(r, 'актуальные цены')
        self.assertContains(r, 'Цены на ремонт Apple iPhone XR')


class CatalogPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = PhoneBrand.objects.create(name="Apple", slug="apple")
        PhoneModel.objects.bulk_create([
            PhoneModel(
                brand=cls.brand,
                name=f"Series & {number:03d}",
                slug=f"series-{number:03d}",
                category="phone",
            )
            for number in range(170)
        ])
        PhoneModel.objects.bulk_create([
            PhoneModel(
                brand=cls.brand,
                name=f"Tablet Series & {number:03d}",
                slug=f"tablet-series-{number:03d}",
                category="tablet",
            )
            for number in range(85)
        ])
        cls.url = reverse("repairs:model_list", kwargs={"brand_slug": cls.brand.slug})

    def test_modes_have_identical_order_page_count_and_no_gaps(self):
        pages = {}
        for mode in ("grid", "list"):
            pages[mode] = []
            for page, expected_size in ((1, 80), (2, 80), (3, 10)):
                params = {"cat": "phone", "view": mode}
                if page > 1:
                    params["page"] = page
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["page_obj"].paginator.num_pages, 3)
                models = [model.pk for model in response.context["models"]]
                self.assertEqual(len(models), expected_size)
                pages[mode].append(models)
                canonical = f"http://testserver/repairs/apple/?cat=phone"
                if page > 1:
                    canonical += f"&amp;page={page}"
                self.assertContains(response, f'<link rel="canonical" href="{canonical}">')
                self.assertContains(response, 'content="index, follow"')
        self.assertEqual(pages["grid"], pages["list"])
        all_ids = [pk for page in pages["grid"] for pk in page]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(set(all_ids), set(PhoneModel.objects.filter(brand=self.brand, category="phone").values_list("pk", flat=True)))

    def test_html_navigation_and_view_switch_keep_page(self):
        response = self.client.get(self.url, {"cat": "phone", "view": "list", "page": 2})
        self.assertEqual(response.context["grid_url"], "/repairs/apple/?cat=phone&page=2")
        self.assertEqual(response.context["list_url"], "/repairs/apple/?cat=phone&view=list&page=2")
        self.assertContains(response, 'href="/repairs/apple/?cat=phone&amp;page=2"')
        self.assertContains(response, 'href="/repairs/apple/?cat=phone&amp;view=list&amp;page=2"')
        self.assertContains(response, '<nav class="catalog-pagination" aria-label="Страницы моделей">')
        self.assertContains(response, 'href="/repairs/apple/?cat=phone&amp;view=list" rel="prev"')
        self.assertContains(response, 'href="/repairs/apple/?cat=phone&amp;view=list&amp;page=3"')
        self.assertContains(response, 'aria-current="page">2</span>')
        self.assertContains(response, "Следующая")
        grid = self.client.get(response.context["grid_url"])
        self.assertEqual(grid.status_code, 200)
        self.assertEqual(grid.context["page_obj"].number, 2)
        self.assertEqual(
            [model.pk for model in response.context["models"]],
            [model.pk for model in grid.context["models"]],
        )
        model = response.context["models"][0]
        model_url = reverse("repairs:repair_list", kwargs={
            "brand_slug": self.brand.slug, "model_slug": model.slug,
        })
        self.assertContains(response, f'href="{model_url}"')
        self.assertEqual(self.client.get(model_url).status_code, 200)

    def test_search_and_category_persist_without_indexable_query_duplicates(self):
        params = {"cat": "tablet", "view": "list", "q": "Series &", "page": 2}
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["models"]), 5)
        self.assertTrue(all(model.category == "tablet" for model in response.context["models"]))
        self.assertContains(response, 'href="/repairs/apple/?cat=tablet&amp;q=Series+%26&amp;page=2"')
        self.assertContains(response, 'href="/repairs/apple/?cat=tablet&amp;view=list&amp;q=Series+%26&amp;page=2"')
        self.assertContains(response, 'href="/repairs/apple/?cat=tablet&amp;view=list&amp;q=Series+%26" rel="prev"')
        self.assertContains(response, '<link rel="canonical" href="http://testserver/repairs/apple/?cat=tablet&amp;q=Series+%26&amp;page=2">')
        self.assertContains(response, 'content="noindex, follow"')
        first = self.client.get(self.url, {"cat": "tablet", "q": "Series &"})
        self.assertContains(first, '<link rel="canonical" href="http://testserver/repairs/apple/?cat=tablet">')
        self.assertContains(first, 'content="noindex, follow"')

    @override_settings(ALLOWED_HOSTS=["testserver", "tehsfera.by"])
    def test_canonical_uses_https_request_host_without_view(self):
        response = self.client.get(
            self.url, {"cat": "phone", "view": "list", "page": 2},
            secure=True, HTTP_HOST="tehsfera.by",
        )
        self.assertContains(response, '<link rel="canonical" href="https://tehsfera.by/repairs/apple/?cat=phone&amp;page=2">')

    def test_first_page_query_redirects_and_invalid_pages_are_not_duplicates(self):
        response = self.client.get(self.url, {
            "cat": "phone", "view": "list", "q": "Series &", "page": "1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/repairs/apple/?cat=phone&view=list&q=Series+%26")
        for page in ("0", "-1", "abc", "02", "999"):
            with self.subTest(page=page):
                self.assertEqual(self.client.get(self.url, {"cat": "phone", "page": page}).status_code, 404)

    def test_booking_route_is_unchanged(self):
        model = PhoneModel.objects.get(slug="series-000")
        repair_type = RepairType.objects.create(name="Battery", slug="battery")
        ModelRepairPrice.objects.create(
            phone_model=model, repair_type=repair_type, price=100, duration_min=60,
        )
        model_url = reverse("repairs:repair_list", kwargs={
            "brand_slug": self.brand.slug, "model_slug": model.slug,
        })
        booking_url = reverse("repairs:slot_select", kwargs={
            "brand_slug": self.brand.slug,
            "model_slug": model.slug,
            "repair_slug": repair_type.slug,
        })
        self.assertContains(self.client.get(model_url), f'href="{booking_url}"')
        self.assertEqual(self.client.get(booking_url).status_code, 200)
