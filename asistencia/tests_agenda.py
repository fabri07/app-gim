"""Marcar «asistió» desde la agenda de turnos (staff)."""

from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from asistencia.services import MAX_DIAS_ATRAS
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS
from turnos.models import ConfiguracionTurnos, HorarioAtencion, Reserva
from turnos.services import lunes_de_semana


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a", reconocimientos_habilitado=True
        )
        self.otro = Gimnasio.objects.create(
            nombre="B", slug="b", reconocimientos_habilitado=True
        )
        for gym in (self.gimnasio, self.otro):
            ConfiguracionTurnos.objects.create(
                gimnasio=gym, duracion_minutos=60, vacantes_default=50
            )
            for dia in range(7):
                HorarioAtencion.objects.create(
                    gimnasio=gym,
                    dia_semana=dia,
                    hora_desde=time(8, 0),
                    hora_hasta=time(12, 0),
                )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.alumno_otro = Alumno.objects.create(
            gimnasio=self.otro, nombre="Carla", apellido="Ruiz"
        )
        self.user = User.objects.create_user("staff-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")
        self.hoy = timezone.localdate()

    def _reserva(self, fecha, alumno=None, hora=time(9, 0)):
        alumno = alumno or self.alumno
        return Reserva.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, fecha=fecha, hora_inicio=hora
        )

    def _post(self, reserva, accion="marcar", **extra):
        return self.client.post(
            reverse("asistencia:marcar_reserva", args=[reserva.pk]),
            {"accion": accion, **extra},
        )

    def _mensajes(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class AgendaConBanderaTests(_Base):
    def _semana_de(self, fecha):
        return (fecha - timedelta(days=fecha.weekday()) - lunes_de_semana(0)).days // 7

    def test_muestra_asistio_en_hoy_y_pasadas_pero_no_en_futuras(self):
        pasada = self.hoy - timedelta(days=7)
        futura = self.hoy + timedelta(days=7)
        for fecha in (self.hoy, pasada, futura):
            self._reserva(fecha)

        for fecha, esperado in ((self.hoy, True), (pasada, True), (futura, False)):
            response = self.client.get(
                reverse("turnos:agenda"), {"semana": self._semana_de(fecha)}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual("Asistió" in response.content.decode(), esperado, fecha)

    def test_no_muestra_asistio_pasados_los_35_dias(self):
        vieja = self.hoy - timedelta(days=MAX_DIAS_ATRAS + 1)
        self._reserva(vieja)
        response = self.client.get(
            reverse("turnos:agenda"), {"semana": self._semana_de(vieja)}
        )

        self.assertNotContains(response, "Asistió")

    def test_muestra_quitar_si_ya_hay_marca(self):
        self._reserva(self.hoy)
        Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy
        )

        response = self.client.get(reverse("turnos:agenda"))

        self.assertContains(response, "Quitar")
        self.assertNotContains(response, "Asistió")

    def test_franjas_de_hoy_y_pasadas_abiertas_futuras_no(self):
        self._reserva(self.hoy)
        response = self.client.get(reverse("turnos:agenda"))
        self.assertContains(response, "<details open>")

        futura = self.hoy + timedelta(days=7)
        Reserva.objects.all().delete()
        self._reserva(futura)
        response = self.client.get(reverse("turnos:agenda"), {"semana": 1})
        self.assertContains(response, "<details>")
        self.assertNotContains(response, "<details open>")

    def _queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("turnos:agenda"))
        return len(ctx)

    def test_queries_constantes_al_crecer_las_reservas(self):
        self._reserva(self.hoy)
        self._queries()  # calienta cachés (la primera vuelta cuesta de más)
        antes = self._queries()
        for i in range(10):
            alumno = Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"N{i}", apellido="Z"
            )
            self._reserva(self.hoy, alumno=alumno)
            Asistencia.objects.create(
                gimnasio=self.gimnasio, alumno=alumno, fecha=self.hoy
            )
        self.assertEqual(self._queries(), antes)


class AgendaSinBanderaTests(_Base):
    def test_html_identico_al_de_antes_y_url_404(self):
        self._reserva(self.hoy)
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()

        response = self.client.get(reverse("turnos:agenda"))
        html = response.content.decode()

        self.assertNotIn("Asistió", html)
        self.assertNotIn("<details open>", html)
        self.assertNotIn("asistencia/reservas", html)
        self.assertNotIn("<form", html.split('class="grilla-turnos"')[1])
        reserva = Reserva.objects.get()
        self.assertEqual(self._post(reserva).status_code, 404)
        self.assertFalse(Asistencia.objects.exists())


class MarcarAsistenciaReservaTests(_Base):
    def test_marcar_crea_la_asistencia_y_es_idempotente(self):
        reserva = self._reserva(self.hoy, hora=time(10, 0))

        r1 = self._post(reserva)
        r2 = self._post(reserva)

        self.assertEqual(r1.status_code, 302)
        self.assertEqual(r2.status_code, 302)
        asistencia = Asistencia.objects.get()
        self.assertEqual(asistencia.alumno, self.alumno)
        self.assertEqual(asistencia.fecha, self.hoy)
        self.assertEqual(asistencia.origen, Asistencia.Origen.TURNO)
        self.assertEqual(asistencia.hora, time(10, 0))
        self.assertEqual(asistencia.registrada_por, self.perfil)

    def test_quitar_borra_la_marca(self):
        reserva = self._reserva(self.hoy)
        self._post(reserva)

        self._post(reserva, accion="quitar")

        self.assertFalse(Asistencia.objects.exists())

    def test_reserva_futura_da_mensaje_y_no_crea(self):
        reserva = self._reserva(self.hoy + timedelta(days=1))

        response = self._post(reserva)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())
        self.assertEqual(len(self._mensajes(response)), 1)

    def test_reserva_de_mas_de_35_dias_da_mensaje_y_no_crea(self):
        reserva = self._reserva(self.hoy - timedelta(days=MAX_DIAS_ATRAS + 1))

        response = self._post(reserva)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())
        self.assertEqual(len(self._mensajes(response)), 1)

    def test_el_dia_35_si_se_puede(self):
        reserva = self._reserva(self.hoy - timedelta(days=MAX_DIAS_ATRAS))
        self._post(reserva)
        self.assertEqual(Asistencia.objects.count(), 1)

    def test_alumno_inactivo_da_mensaje_y_no_crea(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save(update_fields=["estado"])
        reserva = self._reserva(self.hoy)

        response = self._post(reserva)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())
        self.assertEqual(len(self._mensajes(response)), 1)

    def test_reserva_de_otro_gimnasio_404(self):
        reserva = self._reserva(self.hoy, alumno=self.alumno_otro)
        self.assertEqual(self._post(reserva).status_code, 404)
        self.assertFalse(Asistencia.objects.exists())

    def test_alumno_recibe_403(self):
        reserva = self._reserva(self.hoy)
        u = User.objects.create_user("alu", password="clave-123456")
        # Con terminos_version al día: si no, el cartel de términos lo frena
        # (redirect) antes de que la vista dé el 403.
        Perfil.objects.create(
            usuario=u,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
            terminos_version=VERSION_TERMINOS,
        )
        self.client.login(username="alu", password="clave-123456")
        self.assertEqual(self._post(reserva).status_code, 403)

    def test_anonimo_va_al_login(self):
        reserva = self._reserva(self.hoy)
        self.client.logout()
        response = self._post(reserva)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_cancelar_la_reserva_no_borra_la_asistencia(self):
        reserva = self._reserva(self.hoy)
        self._post(reserva)

        reserva.delete()

        self.assertEqual(Asistencia.objects.count(), 1)

    def test_redirect_conserva_la_semana(self):
        reserva = self._reserva(self.hoy)
        response = self._post(reserva, semana="-2")
        self.assertEqual(response["Location"], reverse("turnos:agenda") + "?semana=-2")

    def test_semana_invalida_se_ignora(self):
        reserva = self._reserva(self.hoy)
        response = self._post(reserva, semana="abc")
        self.assertEqual(response["Location"], reverse("turnos:agenda"))

    def test_doble_post_simultaneo_no_da_500(self):
        reserva = self._reserva(self.hoy)
        with patch(
            "asistencia.views.Asistencia.objects.get_or_create",
            side_effect=IntegrityError,
        ):
            response = self._post(reserva)
        self.assertEqual(response.status_code, 302)
