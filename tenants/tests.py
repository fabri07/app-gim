"""
Tests de Fase 0: registro, login y aislamiento básico de datos entre
gimnasios. Los tests de TenantScopedMixin/TenantScopedModelForm contra un
modelo de dominio real (Alumno, etc.) se agregan en Fase 1, siguiendo el
patrón de ~/gestor-pedidos/core/tests.py — en Fase 0 todavía no existe ningún
TenantOwnedModel concreto para ejercitarlos.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenants.models import Gimnasio, Perfil


class RegisterViewTests(TestCase):
    def test_registro_crea_usuario_gimnasio_y_perfil_staff_y_loguea(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "dueno1",
                "password1": "una-clave-segura-123",
                "password2": "una-clave-segura-123",
                "nombre_gimnasio": "Gimnasio Central",
            },
        )
        self.assertRedirects(response, reverse("home"))

        user = User.objects.get(username="dueno1")
        gimnasio = Gimnasio.objects.get(nombre="Gimnasio Central")
        perfil = Perfil.objects.get(usuario=user)

        self.assertEqual(perfil.gimnasio, gimnasio)
        self.assertEqual(perfil.rol, Perfil.Rol.STAFF)
        self.assertEqual(gimnasio.slug, "gimnasio-central")

        # El registro deja al usuario logueado.
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_slug_no_colisiona_entre_gimnasios_con_el_mismo_nombre(self):
        Gimnasio.objects.create(nombre="Gimnasio Central", slug="gimnasio-central")

        self.client.post(
            reverse("register"),
            {
                "username": "dueno2",
                "password1": "otra-clave-segura-456",
                "password2": "otra-clave-segura-456",
                "nombre_gimnasio": "Gimnasio Central",
            },
        )

        segundo = Gimnasio.objects.exclude(slug="gimnasio-central").get(
            nombre="Gimnasio Central"
        )
        self.assertEqual(segundo.slug, "gimnasio-central-2")


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.user = User.objects.create_user("alumno1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_home_requiere_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('home')}"
        )

    def test_login_y_home_muestra_el_gimnasio_del_perfil(self):
        self.client.login(username="alumno1", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gimnasio de Prueba")

    def test_logout_saca_la_sesion(self):
        self.client.login(username="alumno1", password="clave-123456")
        self.client.post(reverse("logout"))
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)


class TenantIsolationTests(TestCase):
    """Confirma que dos gimnasios no comparten datos ni perfiles."""

    def test_cada_perfil_pertenece_a_un_solo_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        user = User.objects.create_user("staff-a", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=user, gimnasio=gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.assertEqual(perfil.gimnasio, gimnasio_a)
        self.assertNotEqual(perfil.gimnasio, gimnasio_b)
