from django.urls import path
from . import views

app_name = "repairs"

urlpatterns = [
    # СТАБИЛЬНЫЕ ПРЕФИКСЫ — СНАЧАЛА
    path("success/<int:appointment_id>/", views.booking_success, name="booking_success"),
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/slots/", views.slot_select, name="slot_select"),
    path("<slug:brand_slug>/<slug:model_slug>/<slug:repair_slug>/book/", views.book, name="book"),

    # ОБЩИЕ ШАБЛОНЫ — В КОНЦЕ
    path("<slug:brand_slug>/<slug:model_slug>/", views.repair_list, name="repair_list"),
    path("<slug:brand_slug>/", views.model_list, name="model_list"),
    path("", views.brand_list, name="brand_list"),
]
