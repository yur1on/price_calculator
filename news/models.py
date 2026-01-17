from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse


class NewsCategory(models.Model):
    """
    Категории новостей.
    Рекомендуемые slug:
      - workshop (Новости мастерской)
      - tech (Новости технологий)
    """
    title = models.CharField("Название", max_length=80)
    slug = models.SlugField("Slug (URL)", max_length=90, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Категория новостей"
        verbose_name_plural = "Категории новостей"
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title


class NewsPost(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PUBLISHED = "published", "Опубликовано"
        ARCHIVED = "archived", "Архив"

    category = models.ForeignKey(
        NewsCategory,
        on_delete=models.PROTECT,
        related_name="posts",
        verbose_name="Категория",
    )

    title = models.CharField("Заголовок", max_length=200)
    slug = models.SlugField("Slug (URL)", max_length=220, unique=True)
    excerpt = models.TextField("Кратко", blank=True)
    content = models.TextField("Текст", blank=True)

    cover = models.ImageField("Обложка", upload_to="news/", blank=True, null=True)

    status = models.CharField("Статус", max_length=12, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField("Дата публикации", blank=True, null=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["category", "status", "-published_at"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("news:detail", kwargs={"slug": self.slug})

    def save(self, *args, **kwargs):
        # Автопубликация: если статус PUBLISHED и даты нет — ставим сейчас
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class ReactionType(models.TextChoices):
    LIKE = "like", "👍"
    LOVE = "love", "❤️"
    FIRE = "fire", "🔥"
    WOW = "wow", "😮"


class NewsReaction(models.Model):
    post = models.ForeignKey(
        NewsPost,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name="Новость",
    )
    reaction = models.CharField("Реакция", max_length=16, choices=ReactionType.choices)

    # Для авторизованных
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="news_reactions",
        verbose_name="Пользователь",
    )

    # Для гостей
    session_key = models.CharField("Session key", max_length=40, blank=True, default="")

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Реакция"
        verbose_name_plural = "Реакции"
        indexes = [
            models.Index(fields=["post", "reaction"]),
            models.Index(fields=["post", "user"]),
            models.Index(fields=["post", "session_key"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "reaction", "user"],
                condition=Q(user__isnull=False),
                name="uniq_post_reaction_user",
            ),
            models.UniqueConstraint(
                fields=["post", "reaction", "session_key"],
                condition=Q(session_key__gt=""),
                name="uniq_post_reaction_session",
            ),
        ]

    def __str__(self):
        who = self.user_id or self.session_key or "unknown"
        return f"{self.post_id} {self.reaction} ({who})"
