"""
Tests de `TenantScopedMixin` (capa de vista) contra un `TenantOwnedModel` de
dominio real. Se agregan en Fase 1, apenas existe un modelo concreto para
ejercitarlos (`Alumno`) — ver REUSO.md, sección "Qué queda pendiente" y
`tenants/tests.py` para el motivo por el que no se escribieron en Fase 0.

Sigue el patrón de ~/gestor-pedidos/core/tests.py (Cliente -> Alumno,
Negocio -> Gimnasio): `RequestFactory` en vez de `self.client` porque lo que
se prueba es el mixin de vista de forma aislada, sin pasar por urls.py.
"""

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.views.generic import ListView

from alumnos.models import Alumno
from core.mixins import TenantScopedMixin
from tenants.models import Gimnasio, Perfil


class _AlumnoListView(TenantScopedMixin, ListView):
    """Vista mínima de prueba; no se registra en urls."""

    model = Alumno


class TenantScopedMixinTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        self.user_a = User.objects.create_user("ana", password="x")
        Perfil.objects.create(usuario=self.user_a, gimnasio=self.gimnasio_a)
        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Alumno", apellido="A"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Alumno", apellido="B"
        )

    def _view_for(self, user):
        request = self.factory.get("/")
        request.user = user
        view = _AlumnoListView()
        view.setup(request)
        return view

    def test_gimnasio_resuelve_el_del_perfil(self):
        self.assertEqual(self._view_for(self.user_a).gimnasio, self.gimnasio_a)

    def test_get_queryset_scopea_al_gimnasio_del_usuario(self):
        view = self._view_for(self.user_a)
        self.assertEqual(list(view.get_queryset()), [self.alumno_a])
        self.assertNotIn(self.alumno_b, view.get_queryset())

    def test_usuario_sin_perfil_lanza_permission_denied(self):
        user = User.objects.create_user("sinperfil", password="x")
        with self.assertRaises(PermissionDenied):
            _ = self._view_for(user).gimnasio
