"""
Tests de Fase 1 para el modelo `Novedad`: creación básica, la regla de
"visible ahora" (`NovedadQuerySet.visibles`) y el aislamiento por gimnasio
heredado de `TenantQuerySet.for_gimnasio` (core).

Fase 2 agrega tests de las vistas de gestión (`NovedadListView`,
`NovedadCreateView`, `NovedadUpdateView`, `NovedadOcultarView`): login/rol
requerido, aislamiento por gimnasio y el atajo de "ocultar".

Fase 5 agrega el modelo `NovedadLeida` (Feature B: read-receipts) — tests de
creación, unicidad y cascade delete.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from alumnos.models import Alumno
from novedades.models import Novedad, NovedadLeida
from tenants.models import Gimnasio, Perfil


class NovedadModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_y_str(self):
        novedad = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Cerrado el feriado",
            mensaje="El gimnasio no abre el lunes por feriado nacional.",
        )

        self.assertEqual(str(novedad), "Cerrado el feriado")
        self.assertTrue(novedad.activa)
        self.assertEqual(novedad.fecha_publicacion, now().date())
        self.assertIsNone(novedad.visible_hasta)


class NovedadVisiblesTests(TestCase):
    """Cubre la regla de negocio de `NovedadQuerySet.visibles()`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.hoy = now().date()

    def test_visibles_devuelve_solo_la_activa_publicada_y_no_vencida(self):
        vigente = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vigente",
            mensaje="Novedad actualmente visible.",
            fecha_publicacion=self.hoy,
            visible_hasta=None,
            activa=True,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vencida",
            mensaje="Ya venció ayer.",
            fecha_publicacion=self.hoy - timedelta(days=10),
            visible_hasta=self.hoy - timedelta(days=1),
            activa=True,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Inactiva",
            mensaje="Fue ocultada por el staff.",
            fecha_publicacion=self.hoy,
            visible_hasta=None,
            activa=False,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Futura",
            mensaje="Todavía no se publicó.",
            fecha_publicacion=self.hoy + timedelta(days=1),
            visible_hasta=None,
            activa=True,
        )

        visibles = Novedad.objects.for_gimnasio(self.gimnasio).visibles()

        self.assertEqual(list(visibles), [vigente])


class NovedadTenantIsolationTests(TestCase):
    """Confirma que `for_gimnasio()` aísla novedades entre gimnasios."""

    def test_for_gimnasio_no_devuelve_novedades_de_otro_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        novedad_a = Novedad.objects.create(
            gimnasio=gimnasio_a, titulo="Aviso A", mensaje="Solo para A."
        )
        Novedad.objects.create(
            gimnasio=gimnasio_b, titulo="Aviso B", mensaje="Solo para B."
        )

        novedades_de_a = Novedad.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(novedades_de_a), [novedad_a])
        self.assertNotIn(
            "Aviso B", novedades_de_a.values_list("titulo", flat=True)
        )


class NovedadViewsAccesoTests(TestCase):
    """Login y rol requeridos para entrar a cualquier vista de gestión."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_anonimo_es_redirigido_a_login(self):
        url = reverse("novedades:listado")
        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        user = User.objects.create_user("alumno-1", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.get(reverse("novedades:listado"))

        self.assertEqual(response.status_code, 403)


class NovedadViewsStaffTests(TestCase):
    """El staff puede listar, crear y editar las novedades de su gimnasio."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")
        self.hoy = now().date()

    def test_listado_muestra_las_novedades_del_gimnasio(self):
        novedad = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso", mensaje="Contenido."
        )

        response = self.client.get(reverse("novedades:listado"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aviso")
        self.assertIn(novedad, response.context["novedades"])

    def test_crear_novedad_la_asocia_al_gimnasio_del_staff(self):
        response = self.client.post(
            reverse("novedades:crear"),
            {
                "titulo": "Cerrado por refacciones",
                "mensaje": "El gimnasio cierra la próxima semana.",
                "fecha_publicacion": self.hoy,
                "visible_hasta": "",
                "activa": "on",
            },
        )

        self.assertRedirects(response, reverse("novedades:listado"))
        novedad = Novedad.objects.get(titulo="Cerrado por refacciones")
        self.assertEqual(novedad.gimnasio, self.gimnasio)

    def test_editar_novedad_del_propio_gimnasio(self):
        novedad = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Original", mensaje="Texto original."
        )

        response = self.client.post(
            reverse("novedades:editar", args=[novedad.pk]),
            {
                "titulo": "Editado",
                "mensaje": "Texto editado.",
                "fecha_publicacion": self.hoy,
                "visible_hasta": "",
                "activa": "on",
            },
        )

        self.assertRedirects(response, reverse("novedades:listado"))
        novedad.refresh_from_db()
        self.assertEqual(novedad.titulo, "Editado")


class NovedadTenantIsolationViewsTests(TestCase):
    """Staff de un gimnasio no puede tocar novedades de otro por pk."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        self.staff_a = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )
        self.novedad_b = Novedad.objects.create(
            gimnasio=self.gimnasio_b, titulo="Aviso de B", mensaje="Solo para B."
        )
        self.client.login(username="staff-a", password="clave-123456")

    def test_editar_novedad_de_otro_gimnasio_da_404(self):
        response = self.client.get(
            reverse("novedades:editar", args=[self.novedad_b.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_ocultar_novedad_de_otro_gimnasio_da_404(self):
        response = self.client.post(
            reverse("novedades:ocultar", args=[self.novedad_b.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.novedad_b.refresh_from_db()
        self.assertTrue(self.novedad_b.activa)


class NovedadOcultarViewTests(TestCase):
    """El atajo de un clic "ocultar" solo acepta POST y apaga `activa`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")
        self.novedad = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso", mensaje="Contenido.", activa=True
        )

    def test_post_oculta_la_novedad(self):
        response = self.client.post(
            reverse("novedades:ocultar", args=[self.novedad.pk])
        )

        self.assertRedirects(response, reverse("novedades:listado"))
        self.novedad.refresh_from_db()
        self.assertFalse(self.novedad.activa)

    def test_get_no_esta_permitido(self):
        response = self.client.get(
            reverse("novedades:ocultar", args=[self.novedad.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.novedad.refresh_from_db()
        self.assertTrue(self.novedad.activa)


class NovedadListadoVisibleAhoraTests(TestCase):
    """El listado marca correctamente qué novedades son visibles ahora,
    reusando `NovedadQuerySet.visibles()` como fuente de verdad (no se
    duplica la condición acá para evitar que ambos criterios diverjan)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")
        self.hoy = now().date()

    def test_ids_visibles_coincide_con_novedadqueryset_visibles(self):
        vigente = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vigente",
            mensaje="Visible ahora.",
            fecha_publicacion=self.hoy,
            activa=True,
        )
        oculta = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Oculta",
            mensaje="Fue apagada.",
            fecha_publicacion=self.hoy,
            activa=False,
        )
        vencida = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vencida",
            mensaje="Ya venció.",
            fecha_publicacion=self.hoy - timedelta(days=5),
            visible_hasta=self.hoy - timedelta(days=1),
            activa=True,
        )

        response = self.client.get(reverse("novedades:listado"))

        esperado = set(
            Novedad.objects.for_gimnasio(self.gimnasio)
            .visibles()
            .values_list("pk", flat=True)
        )
        self.assertEqual(response.context["ids_visibles"], esperado)
        self.assertIn(vigente.pk, response.context["ids_visibles"])
        self.assertNotIn(oculta.pk, response.context["ids_visibles"])
        self.assertNotIn(vencida.pk, response.context["ids_visibles"])


class NovedadLeidaModelTests(TestCase):
    """Tests del modelo `NovedadLeida` (Feature B: read-receipts).

    Cubre: creación + __str__, unicidad (novedad, alumno), y cascade delete.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Juan",
            apellido="Pérez",
        )
        self.novedad = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Aviso importante",
            mensaje="El gimnasio abre de 7 a 22.",
        )

    def test_creacion_y_str(self):
        """NovedadLeida se crea correctamente y __str__ devuelve el formato esperado."""
        novedad_leida = NovedadLeida.objects.create(
            novedad=self.novedad,
            alumno=self.alumno,
        )

        self.assertEqual(
            str(novedad_leida), "Pérez, Juan leyó 'Aviso importante'"
        )

    def test_unicidad_novedad_alumno(self):
        """Crear dos NovedadLeida con el mismo par (novedad, alumno) lanza IntegrityError."""
        NovedadLeida.objects.create(
            novedad=self.novedad,
            alumno=self.alumno,
        )

        with self.assertRaises(IntegrityError):
            NovedadLeida.objects.create(
                novedad=self.novedad,
                alumno=self.alumno,
            )

    def test_cascade_delete_desde_novedad(self):
        """Borrar una Novedad borra en cascada sus NovedadLeida."""
        NovedadLeida.objects.create(
            novedad=self.novedad,
            alumno=self.alumno,
        )
        self.assertEqual(NovedadLeida.objects.count(), 1)

        self.novedad.delete()

        self.assertEqual(NovedadLeida.objects.count(), 0)


class NovedadMarcarLeidaViewTests(TestCase):
    """El alumno marca una novedad visible de su gimnasio como leída
    (Fase 5, Feature B). Ver `novedades.views.NovedadMarcarLeidaView`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self.user = User.objects.create_user("alumno-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez", perfil=self.perfil
        )
        self.novedad = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso", mensaje="Contenido."
        )

    def test_anonimo_es_redirigido_a_login(self):
        url = reverse("novedades:marcar_leida", args=[self.novedad.pk])

        response = self.client.post(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_staff_recibe_403(self):
        staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[self.novedad.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_alumno_sin_ficha_recibe_403(self):
        user = User.objects.create_user("sin-ficha", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="sin-ficha", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[self.novedad.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(NovedadLeida.objects.exists())

    def test_alumno_marca_novedad_como_leida(self):
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[self.novedad.pk])
        )

        self.assertRedirects(response, reverse("home"))
        self.assertTrue(
            NovedadLeida.objects.filter(
                novedad=self.novedad, alumno=self.alumno
            ).exists()
        )

    def test_marcar_dos_veces_es_idempotente(self):
        self.client.login(username="alumno-1", password="clave-123456")
        url = reverse("novedades:marcar_leida", args=[self.novedad.pk])

        primera = self.client.post(url)
        segunda = self.client.post(url)

        self.assertEqual(primera.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(
            NovedadLeida.objects.filter(
                novedad=self.novedad, alumno=self.alumno
            ).count(),
            1,
        )

    def test_novedad_de_otro_gimnasio_da_404(self):
        novedad_otro = Novedad.objects.create(
            gimnasio=self.otro_gimnasio, titulo="Otro aviso", mensaje="Contenido."
        )
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[novedad_otro.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(NovedadLeida.objects.exists())

    def test_novedad_oculta_da_404(self):
        oculta = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Oculta",
            mensaje="Contenido.",
            activa=False,
        )
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[oculta.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_novedad_vencida_da_404(self):
        vencida = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vencida",
            mensaje="Contenido.",
            fecha_publicacion=now().date() - timedelta(days=10),
            visible_hasta=now().date() - timedelta(days=1),
        )
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.post(
            reverse("novedades:marcar_leida", args=[vencida.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_get_no_esta_permitido(self):
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.get(
            reverse("novedades:marcar_leida", args=[self.novedad.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.assertFalse(NovedadLeida.objects.exists())
