from django.urls import path

from . import master_views

app_name = "master_portal"

urlpatterns = [
    path("login/", master_views.master_login, name="login"),
    path("logout/", master_views.master_logout, name="logout"),
    path("", master_views.dashboard, name="dashboard"),
    path("period/<int:pk>/", master_views.period_detail, name="period_detail"),
]
