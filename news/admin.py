from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import NewsPost, NewsCategory, NewsReaction


@admin.register(NewsCategory)
class NewsCategoryAdmin(ModelAdmin):
    list_display = ("title", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("sort_order", "title")


@admin.register(NewsPost)
class NewsPostAdmin(ModelAdmin):
    list_display = ("title", "category", "status", "published_at", "updated_at")
    list_filter = ("status", "category")
    search_fields = ("title", "excerpt", "content")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-published_at", "-created_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(NewsReaction)
class NewsReactionAdmin(ModelAdmin):
    list_display = ("post", "reaction", "user", "session_key", "created_at")
    list_filter = ("reaction",)
    search_fields = ("post__title", "session_key", "user__username")
    ordering = ("-created_at",)
