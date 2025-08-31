# core/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("repairs/", include("repairs.urls")),
    # корень -> список брендов
    path("", RedirectView.as_view(pattern_name="repairs:brand_list", permanent=False)),
]

# В режиме DEBUG отдаём медиа-файлы (загруженные логотипы и т.п.)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
