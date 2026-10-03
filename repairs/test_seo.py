from django.test import TestCase
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
