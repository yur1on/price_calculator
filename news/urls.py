from django.urls import path
from . import views

app_name = "news"

urlpatterns = [
    path("", views.NewsHomeView.as_view(), name="home"),
    path("", views.NewsHomeView.as_view(), name="list"),  # совместимость со старыми шаблонами
    path("category/<slug:slug>/", views.NewsCategoryView.as_view(), name="category"),
    path("<slug:slug>/react/", views.toggle_reaction, name="react"),
    path("<slug:slug>/", views.NewsDetailView.as_view(), name="detail"),
]