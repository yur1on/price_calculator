from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import NewsPost, NewsCategory, NewsReaction, NewsSource, NewsImage


@admin.register(NewsCategory)
class NewsCategoryAdmin(ModelAdmin):
    list_display = ("title", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("sort_order", "title")


class NewsSourceInline(TabularInline):
    model = NewsSource
    extra = 0
    min_num = 0
    max_num = 3
    fields = ("title", "url", "sort_order")
    ordering = ("sort_order", "id")


class NewsImageInline(TabularInline):
    model = NewsImage
    extra = 0
    min_num = 0
    max_num = 5
    fields = ("position", "image", "caption", "sort_order")
    ordering = ("position", "sort_order", "id")


@admin.register(NewsPost)
class NewsPostAdmin(ModelAdmin):
    inlines = [NewsSourceInline, NewsImageInline]

    list_display = (
        "title",
        "category",
        "status",
        "published_at",
        "author_user",
        "updated_at",
    )
    list_filter = ("status", "category")
    search_fields = ("title", "excerpt", "content", "author_name")
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
        ("Автор", {
            "fields": ("author_user", "author_name")
        }),
        ("Служебное", {
            "fields": ("created_at", "updated_at")
        }),
    )

    def save_model(self, request, obj, form, change):
        if not obj.author_user and request.user.is_authenticated and request.user.is_staff:
            obj.author_user = request.user
        super().save_model(request, obj, form, change)


@admin.register(NewsReaction)
class NewsReactionAdmin(ModelAdmin):
    list_display = ("post", "reaction", "user", "session_key", "created_at")
    list_filter = ("reaction",)
    search_fields = ("post__title", "session_key", "user__username")
    ordering = ("-created_at",)
