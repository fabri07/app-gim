"""
Tests de Fase 1 para la biblioteca de ejercicios: creación básica, choices de
`grupo_muscular` y aislamiento por gimnasio. Sigue el patrón de
tenants/tests.py::TenantIsolationTests.

Fase 2 agrega tests de las vistas de gestión (`EjercicioListView`,
`EjercicioCreateView`, `EjercicioUpdateView`): login/rol requerido,
aislamiento por gimnasio y el filtro `?grupo_muscular=`.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ejercicios.models import Ejercicio
from tenants.models import Gimnasio, Perfil


class EjercicioModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

        self.assertEqual(str(ejercicio), "Sentadilla")
        self.assertTrue(ejercicio.activo)
        self.assertEqual(ejercicio.descripcion, "")
        self.assertEqual(ejercicio.url_video, "")

    def test_grupo_muscular_expone_la_etiqueta_legible(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )

        self.assertEqual(ejercicio.grupo_muscular, "pecho")
        self.assertEqual(ejercicio.get_grupo_muscular_display(), "Pecho")


class EjercicioTenantIsolationTests(TestCase):
    """Confirma que la biblioteca de ejercicios de un gimnasio no se mezcla
    con la de otro."""

    def test_for_gimnasio_devuelve_solo_los_ejercicios_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b,
            nombre="Dominadas",
            grupo_muscular=Ejercicio.GrupoMuscular.ESPALDA,
        )

        resultado = Ejercicio.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(resultado), [ejercicio_a])


class EjercicioViewsTests(TestCase):
    """Tests de punta a punta de las vistas de gestión de Fase 2."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_anonimo_es_redirigido_al_login(self):
        url = reverse("ejercicios:listado")
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self.client.login(username="alumno-a", password="clave-123456")
        response = self.client.get(reverse("ejercicios:listado"))
        self.assertEqual(response.status_code, 403)

    def test_staff_puede_listar_ejercicios_de_su_gimnasio(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(reverse("ejercicios:listado"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sentadilla")

    def test_staff_puede_crear_un_ejercicio(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            reverse("ejercicios:crear"),
            {
                "nombre": "Press militar",
                "grupo_muscular": Ejercicio.GrupoMuscular.HOMBROS,
                "descripcion": "",
                "url_video": "",
                "activo": "on",
            },
        )
        self.assertRedirects(response, reverse("ejercicios:listado"))
        ejercicio = Ejercicio.objects.get(nombre="Press militar")
        self.assertEqual(ejercicio.gimnasio, self.gimnasio)
        self.assertEqual(ejercicio.grupo_muscular, Ejercicio.GrupoMuscular.HOMBROS)

    def test_staff_puede_editar_un_ejercicio_de_su_gimnasio(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Curl de biceps",
            grupo_muscular=Ejercicio.GrupoMuscular.BRAZOS,
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            reverse("ejercicios:editar", args=[ejercicio.pk]),
            {
                "nombre": "Curl de biceps con barra",
                "grupo_muscular": Ejercicio.GrupoMuscular.BRAZOS,
                "descripcion": "",
                "url_video": "https://youtube.com/watch?v=abc123",
                "activo": "",  # desmarcado: prueba el toggle de `activo` vía el form
            },
        )
        self.assertRedirects(response, reverse("ejercicios:listado"))
        ejercicio.refresh_from_db()
        self.assertEqual(ejercicio.nombre, "Curl de biceps con barra")
        self.assertEqual(ejercicio.url_video, "https://youtube.com/watch?v=abc123")
        self.assertFalse(ejercicio.activo)

    def test_staff_de_otro_gimnasio_recibe_404_al_editar(self):
        gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        ejercicio_b = Ejercicio.objects.create(
            gimnasio=gimnasio_b,
            nombre="Dominadas",
            grupo_muscular=Ejercicio.GrupoMuscular.ESPALDA,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response_get = self.client.get(
            reverse("ejercicios:editar", args=[ejercicio_b.pk])
        )
        self.assertEqual(response_get.status_code, 404)

        response_post = self.client.post(
            reverse("ejercicios:editar", args=[ejercicio_b.pk]),
            {
                "nombre": "Hackeado",
                "grupo_muscular": Ejercicio.GrupoMuscular.ESPALDA,
                "descripcion": "",
                "url_video": "",
                "activo": "on",
            },
        )
        self.assertEqual(response_post.status_code, 404)
        ejercicio_b.refresh_from_db()
        self.assertEqual(ejercicio_b.nombre, "Dominadas")

    def test_filtro_por_grupo_muscular(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla búlgara",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:listado"),
            {"grupo_muscular": Ejercicio.GrupoMuscular.PIERNAS},
        )

        self.assertEqual(response.status_code, 200)
        nombres = {e.nombre for e in response.context["ejercicios"]}
        self.assertEqual(nombres, {"Sentadilla", "Sentadilla búlgara"})
        self.assertNotContains(response, "Press de banca")
