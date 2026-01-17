from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings

from .models import NewsPost
from notify_tg.utils import notify_admins

@receiver(post_save, sender=NewsPost)
def on_news_saved(sender, instance: NewsPost, created: bool, **kwargs):
    # Уведомляем только при публикации
    if instance.status != NewsPost.Status.PUBLISHED:
        return
    if not instance.published_at:
        return

    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    url = f"{base}{instance.get_absolute_url()}" if base else instance.get_absolute_url()

    text = f"📰 Новость опубликована: {instance.title}\n{url}"
    notify_admins(text)
