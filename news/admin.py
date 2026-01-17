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
    list_display = (
        "title",
        "category",
        "status",
        "published_at",
        "author_user",
        "source_name",
        "updated_at",
    )
    list_filter = ("status", "category")
    search_fields = ("title", "excerpt", "content", "author_name", "source_name", "source_url")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-published_at", "-created_at")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (None, {
            "fields": (
                "category",
                "title",
                "slug",
                "status",
                "published_at",
                "cover",
                "excerpt",
                "content",
            )
        }),
        ("Автор и источник", {
            "fields": (
                "author_user",
                "author_name",
                "source_name",
                "source_url",
            )
        }),
        ("Служебное", {
            "fields": ("created_at", "updated_at")
        }),
    )

    def save_model(self, request, obj, form, change):
        # Авто-автор: если не указан и сохраняет staff-пользователь — подставим его
        if not obj.author_user and request.user.is_authenticated and request.user.is_staff:
            obj.author_user = request.user
        super().save_model(request, obj, form, change)


@admin.register(NewsReaction)
class NewsReactionAdmin(ModelAdmin):
    list_display = ("post", "reaction", "user", "session_key", "created_at")
    list_filter = ("reaction",)
    search_fields = ("post__title", "session_key", "user__username")
    ordering = ("-created_at",)
