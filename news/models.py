from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse
from django.core.exceptions import ValidationError


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
    content = models.TextField(
        "Текст",
        blank=True,
        help_text="Можно вставлять картинки плейсхолдерами: {{img:1}} ... {{img:5}}",
    )

    cover = models.ImageField("Обложка", upload_to="news/cover/", blank=True, null=True)

    # Автор
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_posts",
        verbose_name="Автор (пользователь)",
    )
    author_name = models.CharField("Автор (текст)", max_length=120, blank=True)

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
            models.Index(fields=["author_user"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("news:detail", kwargs={"slug": self.slug})

    def author_display(self) -> str:
        if (self.author_name or "").strip():
            return self.author_name.strip()
        if self.author_user_id:
            try:
                full_name = (self.author_user.get_full_name() or "").strip()
            except Exception:
                full_name = ""
            return full_name or getattr(self.author_user, "username", "") or ""
        return ""

    def clean(self):
        # Ограничение источников и картинок (мягко: контролируем на уровне связанных моделей в их clean)
        super().clean()

    def save(self, *args, **kwargs):
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class NewsSource(models.Model):
    """
    Источник новости. До 3 на одну новость.
    """
    post = models.ForeignKey(NewsPost, on_delete=models.CASCADE, related_name="sources", verbose_name="Новость")
    title = models.CharField("Название источника", max_length=160)
    url = models.URLField("Ссылка", help_text="Полная ссылка, обязательно с https://")
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Источник"
        verbose_name_plural = "Источники"
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["post", "sort_order"])]

    def __str__(self):
        return f"{self.title}"

    def clean(self):
        super().clean()
        if self.post_id:
            qs = NewsSource.objects.filter(post_id=self.post_id)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.count() >= 3:
                raise ValidationError("Для одной новости можно указать максимум 3 источника.")


class NewsImage(models.Model):
    """
    Картинки в статье. До 5 на одну новость.
    В тексте вставляйте плейсхолдеры: {{img:1}} ... {{img:5}}
    """
    post = models.ForeignKey(NewsPost, on_delete=models.CASCADE, related_name="images", verbose_name="Новость")
    position = models.PositiveSmallIntegerField(
        "Номер (1-5)",
        help_text="Какой плейсхолдер заменяет: {{img:1}} соответствует номеру 1",
    )
    image = models.ImageField("Изображение", upload_to="news/body/")
    caption = models.CharField("Подпись", max_length=200, blank=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Изображение в тексте"
        verbose_name_plural = "Изображения в тексте"
        ordering = ["position", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["post", "position"], name="uniq_post_image_position")
        ]
        indexes = [
            models.Index(fields=["post", "position"]),
        ]

    def __str__(self):
        return f"{self.post_id} img:{self.position}"

    def clean(self):
        super().clean()
        if self.position < 1 or self.position > 5:
            raise ValidationError({"position": "Номер картинки должен быть от 1 до 5."})

        if self.post_id:
            qs = NewsImage.objects.filter(post_id=self.post_id)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.count() >= 5:
                raise ValidationError("Для одной новости можно добавить максимум 5 изображений.")


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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="news_reactions",
        verbose_name="Пользователь",
    )

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
