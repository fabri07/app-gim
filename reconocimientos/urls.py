from django.urls import path

from reconocimientos import views

app_name = "reconocimientos"

urlpatterns = [
    path("", views.RankingView.as_view(), name="ranking"),
    path("premios/", views.PremiosListView.as_view(), name="premios"),
    path("premios/nuevo/", views.PremioCreateView.as_view(), name="premio_nuevo"),
    path("premios/<int:pk>/editar/", views.PremioUpdateView.as_view(), name="premio_editar"),
]
