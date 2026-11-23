"""Tarjeta «Asistencia» de la ficha del alumno y sus dos acciones del staff."""

from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.forms import RegistrarAsistenciaForm
from asistencia.models import Asistencia
from asistencia.services import MAX_DIAS_ATRAS
from ingreso.models import Dispositivo, EventoIngreso
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS


class _Base(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a", reconocimientos_habilitado=True
        )
        self.otro = Gimnasio.objects.create(
            nombre="B", slug="b", reconocimientos_habilitado=True
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez",
            fecha_inicio_ciclo=self.hoy - timedelta(days=5),
        )
        self.alumno_otro = Alumno.objects.create(
            gimnasio=self.otro, nombre="Carla", apellido="Ruiz",
            fecha_inicio_ciclo=self.hoy - timedelta(days=5),
        )
        self.user = User.objects.create_user("staff-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

    def _ficha(self, alumno=None):
        return self.client.get(
            reverse("alumnos:detalle", args=[(alumno or self.alumno).pk])
        )

    def _registrar(self, fecha, alumno=None):
        return self.client.post(
            reverse("asistencia:registrar", args=[(alumno or self.alumno).pk]),
            {"fecha": fecha.isoformat() if fecha else ""},
        )

    def _rutina(self, alumno, inicio, n_dias=3):
        rutina = RutinaAsignada.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre_snapshot="R",
            objetivo_snapshot="O", fecha_inicio=inicio,
        )
        for orden in range(1, n_dias + 1):
            RutinaAsignadaItem.objects.create(
                rutina_asignada=rutina, ejercicio_nombre_snapshot="E",
                dia=orden, semana=1, orden=orden, series=3, repeticiones="10",
            )
        return rutina


class TarjetaTests(_Base):
    def test_con_plan_muestra_x_de_y_y_quien_marco(self):
        self._rutina(self.alumno, self.alumno.fecha_inicio_ciclo, n_dias=3)
        Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy,
            registrada_por=self.perfil,
        )
        html = self._ficha().content.decode()
        self.assertIn("Asistencia", html)
        self.assertIn("1 de 3 sesiones", html)
        self.assertIn("marcada por staff-1", html)
        self.assertIn(reverse("asistencia:registrar", args=[self.alumno.pk]), html)

    def test_dia_del_lector_dice_lector_y_no_tiene_quitar(self):
        disp = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="P", token_hash="h"
        )
        dia = self.hoy - timedelta(days=1)
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=disp, alumno=self.alumno,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            sentido=EventoIngreso.Sentido.ENTRADA,
            momento=timezone.make_aware(datetime(dia.year, dia.month, dia.day, 10)),
        )
        html = self._ficha().content.decode()
        self.assertIn("lector", html)
        self.assertNotIn("/quitar/", html)

    def test_dia_de_marca_tiene_boton_quitar(self):
        marca = Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy,
            registrada_por=self.perfil,
        )
        html = self._ficha().content.decode()
        self.assertIn(reverse("asistencia:quitar", args=[marca.pk]), html)

    def test_sin_ancla(self):
        self.alumno.fecha_inicio_ciclo = None
        self.alumno.save(update_fields=["fecha_inicio_ciclo"])
        html = self._ficha().content.decode()
        self.assertIn(
            "Sin fecha de inicio del ciclo: no se cuenta la asistencia", html
        )

    def test_sin_rutina_dice_sin_plan_y_no_falla(self):
        response = self._ficha()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Sin plan al inicio del ciclo: no hay meta", response.content.decode()
        )

    def test_sin_bandera_no_hay_tarjeta_y_urls_404(self):
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        marca = Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy
        )
        html = self._ficha().content.decode()
        self.assertNotIn("/asistencia/", html)
        self.assertNotIn("Sin plan al inicio del ciclo", html)
        self.assertEqual(self._registrar(self.hoy).status_code, 404)
        r = self.client.post(reverse("asistencia:quitar", args=[marca.pk]))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Asistencia.objects.filter(pk=marca.pk).exists())

    def test_queries_constantes_al_crecer_los_dias(self):
        def contar():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self._ficha().status_code, 200)
            return len(ctx)

        Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy,
            registrada_por=self.perfil,
        )
        contar()  # calienta cachés (contenttypes, etc.)
        antes = contar()
        for i in range(1, 5):
            Asistencia.objects.create(
                gimnasio=self.gimnasio, alumno=self.alumno,
                fecha=self.hoy - timedelta(days=i), registrada_por=self.perfil,
            )
        self.assertEqual(contar(), antes)


class FormTests(_Base):
    def _form(self, valor):
        return RegistrarAsistenciaForm({"fecha": valor})

    def test_valida(self):
        self.assertTrue(self._form(self.hoy.isoformat()).is_valid())
        limite = self.hoy - timedelta(days=MAX_DIAS_ATRAS)
        self.assertTrue(self._form(limite.isoformat()).is_valid())

    def test_obligatoria(self):
        self.assertIn("fecha", self._form("").errors)

    def test_no_futura(self):
        f = self._form((self.hoy + timedelta(days=1)).isoformat())
        self.assertIn("fecha", f.errors)

    def test_no_mas_de_35_dias(self):
        f = self._form((self.hoy - timedelta(days=MAX_DIAS_ATRAS + 1)).isoformat())
        self.assertIn("fecha", f.errors)


class RegistrarTests(_Base):
    def test_crea_manual_y_es_idempotente(self):
        dia = self.hoy - timedelta(days=2)
        self.assertEqual(self._registrar(dia).status_code, 302)
        self.assertEqual(self._registrar(dia).status_code, 302)
        marca = Asistencia.objects.get()
        self.assertEqual(marca.origen, Asistencia.Origen.MANUAL)
        self.assertEqual(marca.registrada_por, self.perfil)
        self.assertEqual(marca.fecha, dia)

    def test_error_re_renderiza_la_ficha_con_200_y_el_error(self):
        response = self._registrar(self.hoy + timedelta(days=1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "config-error")
        self.assertContains(response, "Gómez")
        self.assertFalse(Asistencia.objects.exists())

    def test_fecha_vacia_da_200_con_error(self):
        response = self._registrar(None)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "config-error")

    def test_alumno_inactivo_no_se_registra(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save(update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo"])
        response = self._registrar(self.hoy)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())

    def test_alumno_de_otro_gimnasio_404(self):
        self.assertEqual(
            self._registrar(self.hoy, alumno=self.alumno_otro).status_code, 404
        )
        self.assertFalse(Asistencia.objects.exists())

    def test_alumno_recibe_403(self):
        u = User.objects.create_user("alu", password="clave-123456")
        Perfil.objects.create(
            usuario=u, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO,
            terminos_version=VERSION_TERMINOS,
        )
        self.client.login(username="alu", password="clave-123456")
        self.assertEqual(self._registrar(self.hoy).status_code, 403)

    def test_get_no_permitido(self):
        r = self.client.get(reverse("asistencia:registrar", args=[self.alumno.pk]))
        self.assertEqual(r.status_code, 405)


class QuitarTests(_Base):
    def test_quita_la_marca(self):
        marca = Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy
        )
        r = self.client.post(reverse("asistencia:quitar", args=[marca.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())

    def test_marca_de_otro_gimnasio_404(self):
        marca = Asistencia.objects.create(
            gimnasio=self.otro, alumno=self.alumno_otro, fecha=self.hoy
        )
        r = self.client.post(reverse("asistencia:quitar", args=[marca.pk]))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Asistencia.objects.filter(pk=marca.pk).exists())

    def test_no_toca_ingresos_del_lector(self):
        disp = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="P", token_hash="h"
        )
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=disp, alumno=self.alumno,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=timezone.now(),
        )
        marca = Asistencia.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=self.hoy
        )
        self.client.post(reverse("asistencia:quitar", args=[marca.pk]))
        self.assertEqual(EventoIngreso.objects.count(), 1)
