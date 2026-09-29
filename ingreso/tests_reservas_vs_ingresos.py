"""Tests de la tarjeta «Reservas contra ingresos reales» (`ingreso/analitica.py`).

Lo que se fija:

- Las dos grillas comparten columnas y escala, y cuentan la MISMA ventana.
- La hora del ingreso es la LOCAL: uno de las 22:30 de Buenos Aires cae en
  las 22 de ese día, no en la 1 del día siguiente (UTC).
- Solo cuenta «vino»: permitido y de entrada.
- «Sin reserva» es la regla de la puerta (hasta 15 minutos antes del turno y
  mientras dura), no una versión propia.
- Costo fijo, sin importar cuántos ingresos haya.
- La tarjeta sale solo con la puerta en uso, y la lista solo con turnos.

Fechas lejanas a hoy a propósito (ver CLAUDE.md, «Fechas»).
"""

from datetime import date, time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import analitica
from ingreso.models import ConfiguracionIngreso, EventoIngreso, obtener_configuracion
from ingreso.tests_dashboard import _BaseDashboard, _local
from turnos.models import (
    ConfiguracionTurnos,
    DiaSemana,
    HorarioAtencion,
    Reserva,
)
from turnos.models import obtener_configuracion as config_turnos

LUNES = date(2026, 5, 4)


class _Base(_BaseDashboard):
    def setUp(self):
        super().setUp()
        ConfiguracionTurnos.objects.filter(pk=config_turnos(self.gimnasio).pk).update(
            duracion_minutos=60
        )

    def _reserva(self, alumno, fecha, hora):
        return Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, fecha=fecha, hora_inicio=hora
        )

    def _celda(self, grilla, hora, dia="Lunes"):
        fila = next(d for d in grilla["dias"] if d["nombre"] == dia)
        return next(c for c in fila["celdas"] if c["hora"] == hora)["valor"]


class ReservasContraIngresosTests(_Base):
    def test_las_dos_grillas_comparten_columnas_y_escala(self):
        self._reserva(self.alumno, LUNES, time(19))
        self._evento(alumno=self.alumno, momento=_local(2026, 5, 4, 8, 10))
        self._evento(alumno=self.alumno, momento=_local(2026, 5, 4, 8, 40))

        c = analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)

        self.assertEqual(c["reservas"]["horas"], [8, 19])
        self.assertEqual(c["ingresos"]["horas"], [8, 19])
        self.assertEqual(c["reservas"]["maximo"], 2)
        self.assertEqual(c["ingresos"]["maximo"], 2)
        self.assertEqual(self._celda(c["reservas"], 19), 1)
        self.assertEqual(self._celda(c["ingresos"], 8), 2)
        self.assertEqual(self._celda(c["ingresos"], 19), 0)
        self.assertEqual((c["total_reservas"], c["total_ingresos"]), (1, 2))

    def test_el_ingreso_de_la_noche_cae_en_su_dia_y_hora_local(self):
        # 22:30 en Buenos Aires = 01:30 UTC del martes.
        self._evento(alumno=self.alumno, momento=_local(2026, 5, 4, 22, 30))

        c = analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)

        self.assertEqual(c["ingresos"]["horas"], [22])
        self.assertEqual(self._celda(c["ingresos"], 22, "Lunes"), 1)
        self.assertEqual(self._celda(c["ingresos"], 22, "Martes"), 0)

    def test_solo_cuenta_los_que_entraron(self):
        self._evento(alumno=self.alumno, momento=_local(2026, 5, 4, 9))
        self._evento(
            alumno=self.alumno, momento=_local(2026, 5, 4, 10),
            sentido=EventoIngreso.Sentido.SALIDA,
        )
        self._evento(
            alumno=self.alumno, momento=_local(2026, 5, 4, 11),
            resultado=EventoIngreso.Resultado.DEUDA,
        )

        c = analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)

        self.assertEqual(c["total_ingresos"], 1)
        self.assertEqual(c["ingresos"]["horas"], [9])

    def test_las_dos_cuentan_la_misma_ventana(self):
        afuera = LUNES - timedelta(weeks=analitica.SEMANAS_COMPARACION)
        self._reserva(self.alumno, afuera, time(7))
        self._evento(alumno=self.alumno, momento=_local(afuera.year, afuera.month, afuera.day, 7))
        adentro = afuera + timedelta(days=1)
        self._reserva(self.alumno, adentro, time(9))
        self._evento(alumno=self.alumno, momento=_local(adentro.year, adentro.month, adentro.day, 9))

        c = analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)

        self.assertEqual((c["total_reservas"], c["total_ingresos"]), (1, 1))
        self.assertEqual(c["reservas"]["horas"], [9])

    def test_no_mezcla_otro_gimnasio(self):
        ajeno = Alumno.objects.create(gimnasio=self.otro, nombre="X", apellido="Y")
        Reserva.objects.create(gimnasio=self.otro, alumno=ajeno, fecha=LUNES, hora_inicio=time(19))
        self._evento(
            gimnasio=self.otro, dispositivo=self.puerta_ajena, alumno=ajeno,
            momento=_local(2026, 5, 4, 19),
        )

        c = analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)

        self.assertEqual((c["total_reservas"], c["total_ingresos"]), (0, 0))
        self.assertEqual(c["reservas"]["dias"], [])


class SinReservaTests(_Base):
    AHORA = _local(2026, 5, 4, 23, 0)

    def test_usa_la_regla_de_la_puerta(self):
        a_tiempo = self.alumno
        temprano = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="Temprano")
        sin_turno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Beto", apellido="Suelto")
        tarde = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Caro", apellido="Tarde")
        for alumno in (a_tiempo, temprano, tarde):
            self._reserva(alumno, LUNES, time(19))
        # 15 minutos antes: vale. 20 antes: para la puerta no tenía turno.
        self._evento(alumno=a_tiempo, momento=_local(2026, 5, 4, 18, 45))
        self._evento(alumno=temprano, momento=_local(2026, 5, 4, 18, 40))
        self._evento(alumno=sin_turno, momento=_local(2026, 5, 4, 10))
        self._evento(alumno=sin_turno, momento=_local(2026, 5, 4, 20))
        # Con el turno ya terminado (60 minutos), tampoco.
        self._evento(alumno=tarde, momento=_local(2026, 5, 4, 20, 0))

        sr = analitica.sin_reserva(self.gimnasio, ahora=self.AHORA)

        veces = {fila["nombre"]: fila["veces"] for fila in sr["alumnos"]}
        self.assertEqual(veces, {"Beto Suelto": 2, "Ana Temprano": 1, "Caro Tarde": 1})
        self.assertEqual(sr["alumnos"][0]["nombre"], "Beto Suelto")
        self.assertEqual((sr["total_sin_reserva"], sr["total_ingresos"]), (4, 5))

    def test_un_turno_de_la_noche_anterior_sigue_en_curso_pasada_la_medianoche(self):
        # El primer día de la ventana, con el turno de la noche ANTERIOR a la
        # ventana: las reservas se tienen que traer con un día de margen.
        primero = LUNES - timedelta(days=analitica.DIAS_SIN_RESERVA - 1)
        self._reserva(self.alumno, primero - timedelta(days=1), time(23, 30))
        self._evento(
            alumno=self.alumno,
            momento=_local(primero.year, primero.month, primero.day, 0, 10),
        )

        sr = analitica.sin_reserva(self.gimnasio, ahora=self.AHORA)

        self.assertEqual(sr["alumnos"], [])
        self.assertEqual(sr["total_ingresos"], 1)

    def test_fuera_de_la_semana_no_cuenta(self):
        viejo = LUNES - timedelta(days=analitica.DIAS_SIN_RESERVA)
        self._evento(alumno=self.alumno, momento=_local(viejo.year, viejo.month, viejo.day, 10))

        sr = analitica.sin_reserva(self.gimnasio, ahora=self.AHORA)

        self.assertEqual(sr["total_ingresos"], 0)

    def test_topea_la_lista(self):
        for i in range(analitica.TOPE_SIN_RESERVA + 3):
            alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre=f"N{i}", apellido="A")
            self._evento(alumno=alumno, momento=_local(2026, 5, 4, 10))

        sr = analitica.sin_reserva(self.gimnasio, ahora=self.AHORA)

        self.assertEqual(len(sr["alumnos"]), analitica.TOPE_SIN_RESERVA)
        self.assertEqual(sr["mas"], 3)

    def _queries(self, cantidad):
        EventoIngreso.objects.all().delete()
        Reserva.objects.all().delete()
        for i in range(cantidad):
            alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre=f"Q{cantidad}-{i}", apellido="A")
            self._reserva(alumno, LUNES, time(8 + i % 12))
            self._evento(alumno=alumno, momento=_local(2026, 5, 4, 8 + i % 12, 5))
            self._evento(alumno=alumno, momento=_local(2026, 5, 3, 9))
        with CaptureQueriesContext(connection) as ctx:
            analitica.sin_reserva(self.gimnasio, ahora=self.AHORA)
            analitica.reservas_contra_ingresos(self.gimnasio, hoy=LUNES)
        return len(ctx.captured_queries)

    def test_el_costo_no_crece_con_los_ingresos(self):
        self.assertEqual(self._queries(3), self._queries(30))


class TarjetaEnElPanelTests(_Base):
    def _horario(self):
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio, dia_semana=DiaSemana.LUNES,
            hora_desde=time(8), hora_hasta=time(22),
        )

    def test_con_puerta_y_turnos_muestra_grillas_y_lista(self):
        self._horario()
        self._evento(alumno=self.alumno)

        respuesta = self._panel()

        self.assertContains(respuesta, "Reservas contra ingresos reales")
        self.assertContains(respuesta, "Llegaron por la puerta")
        self.assertContains(respuesta, "Vinieron sin reserva")
        self.assertContains(respuesta, "Juan Perez")

    def test_sin_turnos_no_lista_a_todos_como_sin_reserva(self):
        self._evento(alumno=self.alumno)

        respuesta = self._panel()

        self.assertContains(respuesta, "Reservas contra ingresos reales")
        self.assertNotContains(respuesta, "Vinieron sin reserva")

    def test_sin_puerta_no_hay_tarjeta(self):
        Dispositivo = type(self.puerta)
        Dispositivo.objects.filter(gimnasio=self.gimnasio).update(activo=False)
        self._horario()

        respuesta = self._panel()

        self.assertIsNone(respuesta.context["puerta_vs_reservas"])
        self.assertNotContains(respuesta, "Reservas contra ingresos reales")

    def test_con_la_puerta_exigiendo_turno_linkea_a_los_rechazos(self):
        self._horario()
        self._evento(alumno=self.alumno)
        ConfiguracionIngreso.objects.filter(pk=obtener_configuracion(self.gimnasio).pk).update(
            exige_reserva=True
        )

        respuesta = self._panel()

        self.assertContains(respuesta, f'{reverse("ingreso:listado")}?resultado=sin_reserva')

    def test_la_grilla_historica_de_reservas_sigue_igual(self):
        """El refactor a `grilla_de_calor` no cambió la tarjeta de siempre."""
        self._reserva(self.alumno, LUNES, time(19))

        respuesta = self._panel()

        asistencia = respuesta.context["asistencia"]
        self.assertEqual(asistencia["horas"], [19])
        self.assertEqual(asistencia["maximo"], 1)
        self.assertContains(respuesta, 'title="Lunes 19h: 1 reserva(s)"')
