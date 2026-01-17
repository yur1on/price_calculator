from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, DetailView

from .models import NewsPost, NewsCategory, NewsReaction, ReactionType


def _get_session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ""


class NewsHomeView(TemplateView):
    template_name = "news/home.html"
    per_block = 8  # сколько новостей в каждом блоке

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
            .filter(status=NewsPost.Status.PUBLISHED, published_at__lte=timezone.now())
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        post = self.object

        # Счётчики по реакциям
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

        # Активные реакции текущего пользователя/сессии
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
