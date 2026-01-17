import re

from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import linebreaks
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, DetailView

from .models import (
    NewsPost, NewsCategory, NewsReaction, ReactionType,
    NewsSource, NewsImage
)


def _get_session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ""


# поддерживаем {{img:1}}..{{img:5}} с пробелами и любым регистром
_IMG_RE = re.compile(r"\{\{\s*img\s*:\s*([1-5])\s*\}\}", re.IGNORECASE)


def build_rendered_parts(post: NewsPost):
    """
    Вариант №1 (правильный): превращаем текст в HTML-абзацы через linebreaks,
    чтобы переносы/пустые строки из админки отображались красиво.

    - Любой текст экранируется escape() (безопасно)
    - Потом linebreaks() делает <p> и <br>
    - Вставки картинок по {{img:N}} берутся из NewsImage(position=N)
    """
    # Картинки по позиции
    images = {img.position: img for img in post.images.all()}

    content = post.content or ""
    parts = []
    last = 0

    for m in _IMG_RE.finditer(content):
        start, end = m.span()
        pos = int(m.group(1))

        # Текст до плейсхолдера
        chunk = content[last:start]
        if chunk.strip():
            # ВАЖНО: escape -> linebreaks
            parts.append({"type": "html", "html": linebreaks(escape(chunk))})

        # Картинка
        img = images.get(pos)
        if img and img.image:
            parts.append({
                "type": "img",
                "url": img.image.url,
                "caption": (img.caption or "").strip(),
                "position": pos,
            })

        last = end

    # Хвост текста
    tail = content[last:]
    if tail.strip():
        parts.append({"type": "html", "html": linebreaks(escape(tail))})

    return parts


class NewsHomeView(TemplateView):
    template_name = "news/home.html"
    per_block = 8

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        published_qs = (
            NewsPost.objects
            .select_related("category")
            .filter(status=NewsPost.Status.PUBLISHED, published_at__lte=timezone.now())
            .order_by("-published_at", "-created_at")
        )

        workshop_cat = NewsCategory.objects.filter(is_active=True, slug="workshop").first()
        tech_cat = NewsCategory.objects.filter(is_active=True, slug="tech").first()

        if not workshop_cat or not tech_cat:
            active = list(NewsCategory.objects.filter(is_active=True).order_by("sort_order", "title")[:2])
            if not workshop_cat and len(active) >= 1:
                workshop_cat = active[0]
            if not tech_cat and len(active) >= 2:
                tech_cat = active[1] if active[1].id != workshop_cat.id else None

        ctx["workshop_category"] = workshop_cat
        ctx["tech_category"] = tech_cat
        ctx["workshop_posts"] = published_qs.filter(category=workshop_cat)[: self.per_block] if workshop_cat else []
        ctx["tech_posts"] = published_qs.filter(category=tech_cat)[: self.per_block] if tech_cat else []
        return ctx


class NewsDetailView(DetailView):
    model = NewsPost
    template_name = "news/detail.html"
    context_object_name = "post"

    def get_queryset(self):
        return (
            NewsPost.objects
            .select_related("category")
            .prefetch_related("sources", "images")
            .filter(status=NewsPost.Status.PUBLISHED, published_at__lte=timezone.now())
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        post = self.object

        # Источники (сортируем, чтобы в шаблоне было стабильно)
        ctx["sources"] = list(post.sources.all().order_by("sort_order", "id"))

        # Текст + картинки по {{img:N}}
        ctx["rendered_parts"] = build_rendered_parts(post)

        # Счётчики реакций
        counts_qs = (
            NewsReaction.objects
            .filter(post=post)
            .values("reaction")
            .annotate(c=Count("id"))
        )
        counts = {row["reaction"]: row["c"] for row in counts_qs}
        ctx["count_like"] = counts.get("like", 0)
        ctx["count_love"] = counts.get("love", 0)
        ctx["count_fire"] = counts.get("fire", 0)
        ctx["count_wow"] = counts.get("wow", 0)

        # Мои реакции (пользователь или session)
        if self.request.user.is_authenticated:
            mine = set(
                NewsReaction.objects
                .filter(post=post, user=self.request.user)
                .values_list("reaction", flat=True)
            )
        else:
            sk = _get_session_key(self.request)
            mine = set(
                NewsReaction.objects
                .filter(post=post, user__isnull=True, session_key=sk)
                .values_list("reaction", flat=True)
            )

        ctx["my_reactions"] = mine
        return ctx


@require_POST
@csrf_protect
def toggle_reaction(request, slug: str):
    post = get_object_or_404(
        NewsPost,
        slug=slug,
        status=NewsPost.Status.PUBLISHED,
        published_at__lte=timezone.now(),
    )

    reaction = (request.POST.get("reaction") or "").strip()
    allowed = {r.value for r in ReactionType}
    if reaction not in allowed:
        return JsonResponse({"ok": False, "error": "bad_reaction"}, status=400)

    # Toggle для user / session
    if request.user.is_authenticated:
        lookup = {"post": post, "reaction": reaction, "user": request.user}
        existing = NewsReaction.objects.filter(**lookup)
        if existing.exists():
            existing.delete()
        else:
            NewsReaction.objects.create(post=post, reaction=reaction, user=request.user, session_key="")
    else:
        sk = _get_session_key(request)
        lookup = {"post": post, "reaction": reaction, "user__isnull": True, "session_key": sk}
        existing = NewsReaction.objects.filter(**lookup)
        if existing.exists():
            existing.delete()
        else:
            NewsReaction.objects.create(post=post, reaction=reaction, user=None, session_key=sk)

    # Новые счётчики
    counts_qs = (
        NewsReaction.objects
        .filter(post=post)
        .values("reaction")
        .annotate(c=Count("id"))
    )
    counts = {row["reaction"]: row["c"] for row in counts_qs}

    # Мои реакции для подсветки
    if request.user.is_authenticated:
        mine = set(
            NewsReaction.objects.filter(post=post, user=request.user).values_list("reaction", flat=True)
        )
    else:
        sk = _get_session_key(request)
        mine = set(
            NewsReaction.objects.filter(post=post, user__isnull=True, session_key=sk).values_list("reaction", flat=True)
        )

    return JsonResponse({
        "ok": True,
        "counts": {
            "like": counts.get("like", 0),
            "love": counts.get("love", 0),
            "fire": counts.get("fire", 0),
            "wow": counts.get("wow", 0),
        },
        "mine": list(mine),
    })
