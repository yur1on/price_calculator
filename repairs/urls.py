from django.urls import path
from . import views

app_name = "repairs"

urlpatterns = [
    path("contacts/", views.contacts, name="contacts"),

    # СТАБИЛЬНЫЕ ОТЧЁТЫ — СНАЧАЛА
    path("reports/referrals/", views.referrals_report, name="referrals_report"),
    path("reports/referrals/<slug:code>/", views.referrals_partner_report, name="referrals_partner_report"),

    # УСПЕШНОЕ БРОНИРОВАНИЕ
    path("success/<int:appointment_id>/", views.booking_success, name="booking_success"),

    # СЛОТЫ/БРОНЬ
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/slots/", views.slot_select, name="slot_select"),
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/book/", views.book, name="book"),

    # ОБЩИЕ
    path("<slug:brand_slug>/<slug:model_slug>/", views.repair_list, name="repair_list"),
    path("<slug:brand_slug>/", views.model_list, name="model_list"),
    path("", views.brand_list, name="brand_list"),
]
