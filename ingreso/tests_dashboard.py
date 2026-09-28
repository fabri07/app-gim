"""Tests de la Tarea 14: la tarjeta «Control de ingreso» del panel del staff.

Lo que se fija acá:

- Cuenta solo lo del propio gimnasio (eventos y lectores).
- «Hoy» es el día LOCAL, cortado en la medianoche de Buenos Aires: un ingreso
  a las 22:30 es de hoy, no de mañana.
- Costo fijo: dos queries, crezcan lo que crezcan eventos, alumnos y lectores.
- Sin ningún lector activo (ni movimiento hoy) no hay números: la tarjeta
  invita a configurar la puerta. Es la única entrada a la sección, porque el
  nav de staff no tiene ítem para ella.
- Un lector activo que no habla con la app hace rato se marca en ámbar: es la
  señal de que la puerta puede estar caída.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import presentacion, tokens
from ingreso.models import Dispositivo, EventoIngreso
from ingreso.tests_vistas import _BaseVistas

BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")
PERMITIDO = EventoIngreso.Resultado.PERMITIDO
RECHAZADO = EventoIngreso.Resultado.CREDENCIAL_INACTIVA


def _local(*partes):
    return datetime(*partes, tzinfo=BUENOS_AIRES)


class _BaseDashboard(_BaseVistas):
    def setUp(self):
        super().setUp()
        # La puerta del gimnasio A habló con la app hace un rato: sin eso la
        # tarjeta se tiñe de ámbar y los tests que no son de eso lo notarían.
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            ultimo_contacto=timezone.now()
        )

    def _panel(self):
        self._login()
        return self.client.get(reverse("home"))

    def _lector(self, gimnasio=None, **extra):
        datos = {
            "gimnasio": gimnasio or self.gimnasio,
            "nombre": f"Lector {Dispositivo.objects.count() + 1}",
            "token_hash": tokens.hash_de(tokens.generar()),
            "ultimo_contacto": timezone.now(),
        }
        datos.update(extra)
        return Dispositivo.objects.create(**datos)


class TarjetaControlDeIngresoTests(_BaseDashboard):
    def test_cuenta_los_ingresos_y_rechazos_de_hoy_del_propio_gimnasio(self):
        self._evento(alumno=self.alumno)
        self._evento(alumno=self.alumno)
        # Una salida no es un ingreso.
        self._evento(alumno=self.alumno, sentido=EventoIngreso.Sentido.SALIDA)
        self._evento(resultado=RECHAZADO)
        # Lo del otro gimnasio no suma, ni en ingresos ni en rechazos.
        for _ in range(3):
            self._evento(gimnasio=self.otro, dispositivo=self.puerta_ajena)
            self._evento(
                gimnasio=self.otro, dispositivo=self.puerta_ajena,
                resultado=RECHAZADO,
            )

        respuesta = self._panel()

        resumen = respuesta.context["control_ingreso"]
        self.assertEqual(resumen["ingresos_hoy"], 2)
        self.assertEqual(resumen["rechazos_hoy"], 1)
        self.assertEqual(resumen["lectores_activos"], 1)
        self.assertContains(respuesta, "Control de ingreso")
        self.assertContains(respuesta, "2 ingresos hoy")

    def test_el_rechazo_lleva_al_listado_filtrado_por_rechazados_de_hoy(self):
        self._evento(resultado=RECHAZADO)
        hoy = timezone.localdate().isoformat()

        respuesta = self._panel()

        self.assertContains(
            respuesta,
            f'{reverse("ingreso:listado")}?resultado=rechazados'
            f"&amp;desde={hoy}&amp;hasta={hoy}",
        )
        self.assertContains(respuesta, "1 rechazo<")

    def test_la_doble_lectura_no_cuenta_como_rechazo(self):
        """`DUPLICADO` es el antipassback frenando la segunda lectura de
        alguien que YA entró: la ficha y el listado lo muestran neutro, y el
        panel no puede pintarlo como «1 rechazo» en ámbar."""
        self._evento(alumno=self.alumno)
        self._evento(
            alumno=self.alumno, resultado=EventoIngreso.Resultado.DUPLICADO
        )

        respuesta = self._panel()

        self.assertEqual(respuesta.context["control_ingreso"]["rechazos_hoy"], 0)
        self.assertContains(respuesta, "sin rechazos")

    def test_el_numero_del_panel_es_el_del_listado_al_que_lleva(self):
        """Una sola definición de rechazo (`models.RESULTADOS_RECHAZO`) para
        la tarjeta y para el filtro `rechazados`: un evento de cada resultado,
        y los dos números tienen que coincidir."""
        for resultado in EventoIngreso.Resultado.values:
            self._evento(alumno=self.alumno, resultado=resultado)
        hoy = timezone.localdate().isoformat()

        panel = self._panel()
        listado = self.client.get(
            reverse("ingreso:listado"),
            {"resultado": "rechazados", "desde": hoy, "hasta": hoy},
        )

        rechazos = panel.context["control_ingreso"]["rechazos_hoy"]
        self.assertEqual(rechazos, len(EventoIngreso.Resultado.values) - 2)
        self.assertEqual(rechazos, listado.context["paginator"].count)

    def test_los_lectores_del_otro_gimnasio_no_cuentan(self):
        self._lector(gimnasio=self.otro)
        self._lector(gimnasio=self.otro, ultimo_contacto=None)

        resumen = self._panel().context["control_ingreso"]

        self.assertEqual(resumen["lectores_activos"], 1)
        self.assertEqual(resumen["lectores_sin_contacto"], 0)

    def test_sin_lectores_invita_a_configurar_la_puerta_sin_mostrar_numeros(self):
        Dispositivo.objects.filter(pk=self.puerta.pk).update(activo=False)

        respuesta = self._panel()

        self.assertFalse(respuesta.context["control_ingreso"]["usa_la_puerta"])
        self.assertContains(respuesta, "Configurar la puerta")
        self.assertContains(respuesta, reverse("ingreso:dispositivos"))
        self.assertNotContains(respuesta, "ingresos hoy")
        self.assertNotContains(respuesta, "0 ingreso")

    def test_un_gimnasio_sin_ningun_lector_cargado_tambien_ve_la_invitacion(self):
        EventoIngreso.objects.all().delete()
        Dispositivo.objects.filter(gimnasio=self.gimnasio).delete()

        respuesta = self._panel()

        self.assertContains(respuesta, "Configurar la puerta")
        self.assertNotContains(respuesta, "ingresos hoy")

    def test_con_lectores_no_muestra_la_invitacion(self):
        respuesta = self._panel()

        self.assertTrue(respuesta.context["control_ingreso"]["usa_la_puerta"])
        self.assertContains(respuesta, "0 ingresos hoy")
        self.assertNotContains(respuesta, "Configurar la puerta")

    def test_con_movimiento_hoy_muestra_los_numeros_aunque_el_lector_se_haya_desactivado(self):
        """Desactivar el lector a media tarde no borra lo que pasó a la
        mañana: esos ingresos ocurrieron y el dueño los tiene que ver."""
        self._evento(alumno=self.alumno)
        Dispositivo.objects.filter(pk=self.puerta.pk).update(activo=False)

        respuesta = self._panel()

        self.assertContains(respuesta, "1 ingreso hoy")
        self.assertNotContains(respuesta, "Configurar la puerta")

    def test_el_alumno_no_ve_la_tarjeta(self):
        self._evento(alumno=self.alumno)
        self._login("alumno-a")

        respuesta = self.client.get(reverse("home"))

        self.assertNotIn("control_ingreso", respuesta.context)
        self.assertNotContains(respuesta, "Control de ingreso")


class LectoresSinContactoTests(_BaseDashboard):
    def test_un_lector_activo_callado_hace_rato_tine_la_tarjeta_de_ambar(self):
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            ultimo_contacto=timezone.now()
            - presentacion.UMBRAL_LECTOR_SIN_CONTACTO
            - timedelta(minutes=1)
        )

        respuesta = self._panel()

        self.assertEqual(respuesta.context["control_ingreso"]["lectores_sin_contacto"], 1)
        self.assertContains(respuesta, "tarjeta aviso-urgente")
        self.assertContains(respuesta, "no se comunica con la app")

    def test_uno_que_nunca_se_conecto_tambien_cuenta(self):
        self._lector(ultimo_contacto=None)

        resumen = self._panel().context["control_ingreso"]

        self.assertEqual(resumen["lectores_activos"], 2)
        self.assertEqual(resumen["lectores_sin_contacto"], 1)

    def test_un_lector_desactivado_no_cuenta_aunque_este_callado(self):
        self._lector(activo=False, ultimo_contacto=None)

        respuesta = self._panel()

        self.assertEqual(respuesta.context["control_ingreso"]["lectores_sin_contacto"], 0)
        self.assertNotContains(respuesta, "tarjeta aviso-urgente")

    def test_uno_que_hablo_dentro_del_umbral_no_cuenta(self):
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            ultimo_contacto=timezone.now()
            - presentacion.UMBRAL_LECTOR_SIN_CONTACTO
            + timedelta(minutes=5)
        )

        respuesta = self._panel()

        self.assertEqual(respuesta.context["control_ingreso"]["lectores_sin_contacto"], 0)
        self.assertNotContains(respuesta, "no se comunica con la app")


class HoyEsElDiaLocalTests(_BaseDashboard):
    """El reloj se congela a las 22:30 de Buenos Aires de un día LEJANO: en UTC
    ya es el día siguiente (01:30). Con el corte en UTC —`now().date()`, o la
    medianoche UTC en vez de la local— los números de la noche pasan al día
    que no es y el de la noche anterior se cuela."""

    AHORA = datetime(2031, 3, 11, 1, 30, tzinfo=dt_timezone.utc)  # 10/03 22:30 local

    def test_los_ingresos_de_la_noche_son_de_hoy_y_los_de_anoche_no(self):
        # Hoy (10/03 local): mediodía y 22:00.
        self._evento(alumno=self.alumno, momento=_local(2031, 3, 10, 12, 0))
        self._evento(alumno=self.alumno, momento=_local(2031, 3, 10, 22, 0))
        # Anoche (09/03 23:30 local == 10/03 02:30 UTC): no es de hoy.
        self._evento(resultado=RECHAZADO, momento=_local(2031, 3, 9, 23, 30))

        with patch("django.utils.timezone.now", return_value=self.AHORA):
            respuesta = self._panel()

        resumen = respuesta.context["control_ingreso"]
        self.assertEqual(resumen["ingresos_hoy"], 2)
        self.assertEqual(resumen["rechazos_hoy"], 0)
        self.assertContains(respuesta, "desde=2031-03-10")


class CostoFijoTests(_BaseDashboard):
    """El panel corre en cada carga del staff: la tarjeta suma DOS queries,
    tengan el gimnasio 1 o 40 ingresos, 1 o 10 alumnos, 1 o 6 lectores."""

    def _poblar(self, alumnos, lectores, eventos):
        lista_lectores = [self.puerta] + [self._lector() for _ in range(lectores - 1)]
        lista_alumnos = [self.alumno] + [
            Alumno.objects.create(gimnasio=self.gimnasio, nombre=f"A{i}", apellido="X")
            for i in range(alumnos - 1)
        ]
        for i in range(eventos):
            self._evento(
                alumno=lista_alumnos[i % len(lista_alumnos)],
                dispositivo=lista_lectores[i % len(lista_lectores)],
                resultado=PERMITIDO if i % 3 else RECHAZADO,
            )

    def test_el_resumen_son_dos_queries(self):
        self._poblar(alumnos=4, lectores=3, eventos=12)

        with CaptureQueriesContext(connection) as consultas:
            resumen = presentacion.resumen_para_el_panel(self.gimnasio)

        self.assertEqual(len(consultas), 2)
        self.assertEqual(resumen["ingresos_hoy"], 8)
        self.assertEqual(resumen["rechazos_hoy"], 4)

    def test_el_panel_no_crece_con_eventos_alumnos_ni_lectores(self):
        self._poblar(alumnos=1, lectores=1, eventos=1)
        self._login()
        self.client.get(reverse("home"))  # calienta sesión y caches
        with CaptureQueriesContext(connection) as chico:
            respuesta = self.client.get(reverse("home"))
        self.assertEqual(respuesta.context["control_ingreso"]["rechazos_hoy"], 1)

        self._poblar(alumnos=10, lectores=6, eventos=40)
        with CaptureQueriesContext(connection) as grande:
            respuesta = self.client.get(reverse("home"))
        resumen = respuesta.context["control_ingreso"]
        self.assertEqual(resumen["lectores_activos"], 6)
        self.assertGreater(resumen["ingresos_hoy"], 20)

        self.assertEqual(len(chico), len(grande))
