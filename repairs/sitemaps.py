from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from news.models import NewsCategory, NewsPost
from repairs.models import PhoneBrand, PhoneModel


class StaticViewSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return ["home", "repairs:contacts", "privacy", "terms"]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 0.6


class RepairCategorySitemap(Sitemap):
    protocol = "https"

    def items(self):
        return ["phone", "tablet", "watch"]

    def location(self, item):
        return f"{reverse('repairs:brand_list')}?cat={item}"

    def priority(self, item):
        return 1.0 if item == "phone" else 0.8


class RepairBrandSitemap(Sitemap):
    protocol = "https"

    def items(self):
        categories = dict(PhoneModel.CATEGORY_CHOICES).keys()
        existing_pairs = (
            PhoneModel.objects.values_list("brand__slug", "category")
            .distinct()
        )
        return [(brand_slug, category) for brand_slug, category in existing_pairs if category in categories]

    def location(self, item):
        brand_slug, category = item
        return f"{reverse('repairs:model_list', kwargs={'brand_slug': brand_slug})}?cat={category}"

    def priority(self, item):
        return 0.9


class RepairModelSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return PhoneModel.objects.select_related("brand").order_by("brand__name", "name")

    def location(self, item):
        return reverse(
            "repairs:repair_list",
            kwargs={"brand_slug": item.brand.slug, "model_slug": item.slug},
        )

    def priority(self, item):
        return 0.8


class NewsHomeSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return ["news:home"]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 0.7


class NewsCategorySitemap(Sitemap):
    protocol = "https"

    def items(self):
        return NewsCategory.objects.filter(is_active=True).order_by("sort_order", "title")

    def location(self, item):
        return reverse("news:category", kwargs={"slug": item.slug})

    def priority(self, item):
        return 0.6


class NewsPostSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return (
            NewsPost.objects.filter(
                status=NewsPost.Status.PUBLISHED,
                published_at__isnull=False,
                published_at__lte=timezone.now(),
            )
            .select_related("category")
            .order_by("-published_at", "-created_at")
        )

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, item):
        return reverse("news:detail", kwargs={"slug": item.slug})

    def priority(self, item):
        return 0.7


SITEMAPS = {
    "static": StaticViewSitemap,
    "repair_categories": RepairCategorySitemap,
    "repair_brands": RepairBrandSitemap,
    "repair_models": RepairModelSitemap,
    "news_home": NewsHomeSitemap,
    "news_categories": NewsCategorySitemap,
    "news_posts": NewsPostSitemap,
}
