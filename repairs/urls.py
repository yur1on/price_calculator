# repairs/urls.py
from django.urls import path
from . import views
from .views_analytics import analytics_view
from django.urls import path
from .views_analytics import analytics_view, analytics_pages_view, analytics_page_detail_view

app_name = "repairs"

urlpatterns = [
    # 👉 СТАБИЛЬНЫЕ ПУТИ СНАЧАЛА
    path("admin/analytics/", analytics_view, name="analytics"),
    path("admin/analytics/pages/", analytics_pages_view, name="analytics_pages"),
    path("admin/analytics/pages/detail/", analytics_page_detail_view, name="analytics_page_detail"),
    path("contacts/", views.contacts, name="contacts"),
    path("reports/referrals/", views.referrals_report, name="referrals_report"),
    path("reports/referrals/<slug:code>/", views.referrals_partner_report, name="referrals_partner_report"),
    path("success/<int:appointment_id>/", views.booking_success, name="booking_success"),

    # СЛОТЫ/БРОНЬ
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/slots/", views.slot_select, name="slot_select"),
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/book/", views.book, name="book"),

    # ОБЩИЕ (динамика — В КОНЦЕ)
    path("<slug:brand_slug>/<slug:model_slug>/", views.repair_list, name="repair_list"),
    path("<slug:brand_slug>/", views.model_list, name="model_list"),
    path("", views.brand_list, name="brand_list"),
]
