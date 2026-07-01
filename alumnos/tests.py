"""
Tests de `Alumno`: creación básica, valores por defecto y aislamiento de
tenant (mismo criterio que `tenants/tests.py::TenantIsolationTests`).

La segunda mitad del archivo (Fase 2) cubre las vistas de gestión: acceso
por rol (anónimo -> login, alumno -> 403), scoping de tenant en las vistas
(alumno de otro gimnasio -> 404, no 403 ni leak), el toggle activar/inactivar
(POST-only) y que la ficha muestre pagos/rutina propios sin filtrar de otro
alumno.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class AlumnoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        self.assertEqual(str(alumno), "Pérez, Juan")

    def test_estado_por_defecto_activo_y_fecha_activacion_none(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.assertEqual(alumno.estado, Alumno.Estado.ACTIVO)
        self.assertIsNone(alumno.fecha_activacion)


class TenantIsolationTests(TestCase):
    """Confirma que dos gimnasios no comparten alumnos."""

    def test_for_gimnasio_devuelve_solo_los_alumnos_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        alumno_a = Alumno.objects.create(
            gimnasio=gimnasio_a, nombre="Alumno", apellido="A"
        )
        alumno_b = Alumno.objects.create(
            gimnasio=gimnasio_b, nombre="Alumno", apellido="B"
        )

        resultado = Alumno.objects.for_gimnasio(gimnasio_a)

        self.assertIn(alumno_a, resultado)
        self.assertNotIn(alumno_b, resultado)


class AlumnoViewsTests(TestCase):
    """Vistas de gestión de alumnos (Fase 2): acceso por rol, aislamiento
    de tenant, el toggle activar/inactivar y el contenido de la ficha."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF
        )

        self.usuario_alumno = User.objects.create_user(
            username="usuario_alumno", password="clave12345"
        )
        Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Juan", apellido="Pérez"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Ana", apellido="García"
        )

    def _urls_get(self):
        return [
            reverse("alumnos:listado"),
            reverse("alumnos:crear"),
            reverse("alumnos:detalle", args=[self.alumno_a.pk]),
            reverse("alumnos:editar", args=[self.alumno_a.pk]),
        ]

    # 1. Anónimo -> redirect a login en toda vista, incluido el toggle.
    def test_anonimo_redirige_a_login_en_todas_las_vistas(self):
        for url in self._urls_get():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

        response = self.client.get(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    # 2. Perfil con rol ALUMNO -> 403 en toda vista.
    def test_perfil_alumno_recibe_403_en_todas_las_vistas(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        for url in self._urls_get():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 403)

    # 3. Staff puede listar/crear/editar alumnos de su propio gimnasio.
    def test_staff_puede_listar_crear_y_editar_alumnos_de_su_gimnasio(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("alumnos:listado"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pérez")

        datos = {
            "nombre": "Nuevo",
            "apellido": "Alumno",
            "email": "",
            "telefono": "",
            "fecha_nacimiento": "",
            "estado": Alumno.Estado.ACTIVO,
            "observaciones": "",
        }
        response = self.client.post(reverse("alumnos:crear"), datos)
        self.assertEqual(response.status_code, 302)
        nuevo = Alumno.objects.get(apellido="Alumno", gimnasio=self.gimnasio_a)

        datos["apellido"] = "Alumno Editado"
        response = self.client.post(reverse("alumnos:editar", args=[nuevo.pk]), datos)
        self.assertEqual(response.status_code, 302)
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.apellido, "Alumno Editado")

    # 4. Aislamiento de tenant: alumno de otro gimnasio -> 404, no 403 ni leak.
    def test_aislamiento_de_tenant_devuelve_404_no_403(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse("alumnos:editar", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

    # 5. El toggle flipea estado y rechaza GET.
    def test_activar_inactivar_flipea_estado_y_rechaza_get(self):
        self.client.login(username="staff_a", password="clave12345")
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.ACTIVO)

        response = self.client.get(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 405)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 302)
        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.INACTIVO)

        self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.ACTIVO)

    # 6. La ficha muestra los pagos y la rutina propios, no los de otro alumno.
    def test_ficha_muestra_pagos_y_rutina_propios_sin_filtrar_de_otro_alumno(self):
        from pagos.models import PagoMensual
        from rutinas.models import RutinaAsignada

        pago_propio = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=1,
            anio=2026,
            monto=1000,
            estado=PagoMensual.Estado.PAGADO,
        )
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Otro", apellido="Alumno"
        )
        pago_ajeno = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=otro_alumno,
            mes=2,
            anio=2026,
            monto=2000,
            estado=PagoMensual.Estado.PENDIENTE,
        )
        rutina_propia = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            nombre_snapshot="Full body",
            objetivo_snapshot="Hipertrofia",
            fecha_inicio=datetime.date(2026, 1, 1),
            activa=True,
        )

        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(pago_propio, response.context["pagos"])
        self.assertNotIn(pago_ajeno, response.context["pagos"])
        self.assertEqual(response.context["rutina_actual"], rutina_propia)
        self.assertContains(response, "Full body")

    def test_ficha_sin_rutina_asignada_muestra_mensaje_plano(self):
        self.client.login(username="staff_b", password="clave12345")
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_b.pk]))
        self.assertContains(response, "Sin rutina asignada todavía")
