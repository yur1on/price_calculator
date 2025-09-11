from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render


# --- error handlers ---
def err_404(request, exception):
    return render(request, "404.html", {"path": request.path}, status=404)

def err_403(request, exception):
    return render(request, "403.html", status=403)

def err_400(request, exception):
    return render(request, "400.html", status=400)

def err_500(request):
    return render(request, "500.html", status=500)


urlpatterns = [
    path("admin/", admin.site.urls),

    # статические страницы
    path("privacy/", TemplateView.as_view(template_name="legal/privacy.html"), name="privacy"),
    path("terms/", TemplateView.as_view(template_name="legal/terms.html"), name="terms"),

    # приложение
    path("repairs/", include("repairs.urls")),

    # корень
    path("", RedirectView.as_view(pattern_name="repairs:brand_list", permanent=False)),
]

# ВАЖНО: отдаём медиа ВСЕГДА (и в dev, и в prod)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# handlers
handler404 = "core.urls.err_404"
handler403 = "core.urls.err_403"
handler400 = "core.urls.err_400"
handler500 = "core.urls.err_500"
