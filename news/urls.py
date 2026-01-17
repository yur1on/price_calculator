from django.urls import path
from . import views

app_name = "news"

urlpatterns = [
    path("", views.NewsHomeView.as_view(), name="list"),
    path("<slug:slug>/react/", views.toggle_reaction, name="react"),
    path("<slug:slug>/", views.NewsDetailView.as_view(), name="detail"),
]
