from django.urls import path

from reconocimientos import views

app_name = "reconocimientos"

urlpatterns = [
    path("", views.RankingView.as_view(), name="ranking"),
]
