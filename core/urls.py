from django.contrib import admin
from django.urls import path, include, re_path
from django.contrib.sitemaps.views import sitemap
from django.views.generic import RedirectView, TemplateView
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.http import HttpResponse
from django.shortcuts import render
from repairs.views import home, yoomoney_webhook
from repairs.sitemaps import SITEMAPS

# --- error handlers ---
def err_404(request, exception):
    return render(request, "404.html", {"path": request.path}, status=404)

def err_403(request, exception):
    return render(request, "403.html", status=403)

def err_400(request, exception):
    return render(request, "400.html", status=400)

def err_500(request):
    return render(request, "500.html", status=500)


def robots_txt(request):
    content = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /payments/",
        "Sitemap: " + request.build_absolute_uri("/sitemap.xml"),
        "",
    ])
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="django.contrib.sitemaps.views.sitemap"),
    path(
        "yandex_735004e6e924285d.html",
        TemplateView.as_view(
            template_name="yandex_735004e6e924285d.html",
            content_type="text/html",
        ),
        name="yandex_verification",
    ),

    # статические страницы
    path("privacy/", TemplateView.as_view(template_name="legal/privacy.html"), name="privacy"),
    path("terms/", TemplateView.as_view(template_name="legal/terms.html"), name="terms"),
    path("payments/yoomoney/", yoomoney_webhook, name="yoomoney_webhook"),

    # приложение
    path("repairs/", include("repairs.urls")),
    path("news/", include("news.urls")),


    # корень
    path("", home, name="home"),
]

# ВАЖНО: в локальной среде отдаём media напрямую через Django,
# даже если DEBUG=False. В проде этот путь обычно перехватывает Caddy.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

# handlers
handler404 = "core.urls.err_404"
handler403 = "core.urls.err_403"
handler400 = "core.urls.err_400"
handler500 = "core.urls.err_500"
