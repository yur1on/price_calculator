# core/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("repairs/", include("repairs.urls")),
    # корень -> на список брендов
    path("", RedirectView.as_view(pattern_name="repairs:brand_list", permanent=False)),
]
