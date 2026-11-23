"""Servicio de asistencia: unión lector + marcas del staff, meta y resumen."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from asistencia.services import (
    MAX_DIAS_ATRAS,
    ResumenCiclo,
    dias_asistidos,
    dias_asistidos_por_alumno,
    meta_del_ciclo,
    resumen_del_ciclo,
    rutina_vigente_en,
)
from ingreso.models import Dispositivo, EventoIngreso
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio


def _local(dia, hora=12, minuto=0):
    """Un instante aware a esa hora LOCAL del día."""
    return timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto))


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.disp = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta", token_hash="h1"
        )
        self.disp_otro = Dispositivo.objects.create(
            gimnasio=self.otro, nombre="Puerta", token_hash="h2"
        )
        self.alumno = self._alumno("Ana")

    def _alumno(self, nombre, gimnasio=None, **extra):
        return Alumno.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, apellido="X", **extra
        )

    def _ingreso(self, alumno, momento, *, resultado=None, sentido=None):
        disp = self.disp if alumno.gimnasio_id == self.gimnasio.pk else self.disp_otro
        return EventoIngreso.objects.create(
            gimnasio=alumno.gimnasio,
            dispositivo=disp,
            alumno=alumno,
            resultado=resultado or EventoIngreso.Resultado.PERMITIDO,
            sentido=sentido or EventoIngreso.Sentido.ENTRADA,
            momento=momento,
        )

    def _marca(self, alumno, fecha, origen=Asistencia.Origen.MANUAL):
        return Asistencia.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, fecha=fecha, origen=origen
        )

    def _rutina(self, alumno, inicio, pares, **extra):
        rutina = RutinaAsignada.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre_snapshot="R",
            objetivo_snapshot="O", fecha_inicio=inicio, **extra,
        )
        for orden, (dia, semana) in enumerate(pares, start=1):
            RutinaAsignadaItem.objects.create(
                rutina_asignada=rutina, ejercicio_nombre_snapshot="E",
                dia=dia, semana=semana, orden=orden, series=3, repeticiones="10",
            )
        return rutina


class DiasAsistidosTests(_Base):
    D = date(2026, 11, 10)

    def test_ingreso_y_marca_el_mismo_dia_es_un_dia(self):
        self._ingreso(self.alumno, _local(self.D, 9))
        self._marca(self.alumno, self.D)
        self.assertEqual(dias_asistidos(self.alumno, self.D, self.D), {self.D})

    def test_marcas_turno_de_dias_distintos(self):
        sig = self.D + timedelta(days=1)
        self._marca(self.alumno, self.D, Asistencia.Origen.TURNO)
        self._marca(self.alumno, sig, Asistencia.Origen.TURNO)
        self.assertEqual(dias_asistidos(self.alumno, self.D, sig), {self.D, sig})

    def test_ingreso_de_noche_cuenta_en_el_dia_local(self):
        sig = self.D + timedelta(days=1)
        momento = _local(self.D, 22, 30)  # 22:30 locales = día siguiente en UTC
        self.assertEqual(momento.astimezone(dt_timezone.utc).date(), sig)
        self._ingreso(self.alumno, momento)
        self.assertEqual(dias_asistidos(self.alumno, self.D, self.D), {self.D})
        self.assertEqual(dias_asistidos(self.alumno, sig, sig), set())

    def test_ingreso_pasada_la_medianoche_local_cuenta_ese_dia(self):
        ant = self.D - timedelta(days=1)
        self._ingreso(self.alumno, _local(self.D, 0, 10))
        self.assertEqual(dias_asistidos(self.alumno, self.D, self.D), {self.D})
        self.assertEqual(dias_asistidos(self.alumno, ant, ant), set())

    def test_lo_que_no_cuenta(self):
        otra = self._alumno("Otra")
        ajeno = self._alumno("Ajeno", gimnasio=self.otro)
        self._ingreso(self.alumno, _local(self.D), resultado=EventoIngreso.Resultado.DEUDA)
        self._ingreso(self.alumno, _local(self.D, 13), sentido=EventoIngreso.Sentido.SALIDA)
        self._ingreso(otra, _local(self.D))
        self._ingreso(ajeno, _local(self.D))
        self._marca(otra, self.D)
        self._marca(ajeno, self.D)
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.disp, alumno=None,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=_local(self.D),
        )
        self.assertEqual(dias_asistidos(self.alumno, self.D, self.D), set())

    def test_datos_de_otro_gimnasio_no_se_leen(self):
        self._ingreso(self.alumno, _local(self.D))
        self._marca(self.alumno, self.D)
        res = dias_asistidos_por_alumno(self.otro, {self.alumno.pk: (self.D, self.D)})
        self.assertEqual(res, {self.alumno.pk: set()})

    def test_bordes_inclusivos_de_la_ventana(self):
        ini, fin = self.D, self.D + timedelta(days=3)
        for d in (ini - timedelta(days=1), ini, fin, fin + timedelta(days=1)):
            self._ingreso(self.alumno, _local(d, 23, 59))
        self._marca(self.alumno, ini - timedelta(days=2))
        self._marca(self.alumno, fin + timedelta(days=2))
        self.assertEqual(dias_asistidos(self.alumno, ini, fin), {ini, fin})

    def test_por_alumno_cada_uno_con_su_ventana(self):
        a, b, c = self.alumno, self._alumno("B"), self._alumno("C")
        d = self.D
        un = timedelta(days=1)
        for alumno in (a, b, c):
            for k in range(5):
                self._ingreso(alumno, _local(d + k * un))
        self._marca(b, d + 9 * un)
        res = dias_asistidos_por_alumno(self.gimnasio, {
            a.pk: (d, d + un),
            b.pk: (d + 3 * un, d + 9 * un),
            c.pk: (d + 20 * un, d + 21 * un),
        })
        self.assertEqual(res[a.pk], {d, d + un})
        self.assertEqual(res[b.pk], {d + 3 * un, d + 4 * un, d + 9 * un})
        self.assertEqual(res[c.pk], set())

    def test_dos_queries_con_3_y_con_30_alumnos(self):
        for cantidad in (3, 30):
            alumnos = [self._alumno(f"N{cantidad}-{i}") for i in range(cantidad)]
            for alumno in alumnos:
                self._ingreso(alumno, _local(self.D))
                self._marca(alumno, self.D + timedelta(days=1))
            ventanas = {a.pk: (self.D, self.D + timedelta(days=2)) for a in alumnos}
            with self.assertNumQueries(2):
                res = dias_asistidos_por_alumno(self.gimnasio, ventanas)
            self.assertEqual(len(res), cantidad)
            self.assertTrue(all(len(v) == 2 for v in res.values()))

    def test_max_dias_atras(self):
        self.assertEqual(MAX_DIAS_ATRAS, 35)


class MetaTests(_Base):
    INI = date(2026, 11, 1)

    def test_tres_dias_por_cuatro_semanas(self):
        pares = [(d, s) for s in range(1, 5) for d in (1, 2, 3)]
        self._rutina(self.alumno, self.INI, pares)
        self.assertEqual(meta_del_ciclo(self.alumno, self.INI), 12)

    def test_semanas_con_un_dia_menos_cuentan_pares_reales(self):
        pares = [(d, s) for s in range(1, 4) for d in (1, 2, 3)] + [(1, 4), (2, 4)]
        self._rutina(self.alumno, self.INI, pares)
        self.assertEqual(meta_del_ciclo(self.alumno, self.INI), 11)

    def test_sin_rutina_es_none(self):
        self.assertIsNone(meta_del_ciclo(self.alumno, self.INI))

    def test_rutina_vacia_es_none(self):
        self._rutina(self.alumno, self.INI, [])
        self.assertIsNone(meta_del_ciclo(self.alumno, self.INI))

    def test_rutina_que_arranca_despues_del_inicio_es_none(self):
        self._rutina(self.alumno, self.INI + timedelta(days=1), [(1, 1)])
        self.assertIsNone(meta_del_ciclo(self.alumno, self.INI))
        self.assertIsNone(rutina_vigente_en(self.alumno, self.INI))

    def test_dos_rutinas_el_mismo_dia_gana_el_mayor_id(self):
        self._rutina(self.alumno, self.INI, [(1, 1)])
        segunda = self._rutina(self.alumno, self.INI, [(1, 1), (2, 1)])
        self.assertEqual(rutina_vigente_en(self.alumno, self.INI), segunda)
        self.assertEqual(meta_del_ciclo(self.alumno, self.INI), 2)

    def test_rutina_inactiva_no_cuenta(self):
        self._rutina(self.alumno, self.INI, [(1, 1)], activa=False)
        self.assertIsNone(rutina_vigente_en(self.alumno, self.INI))

    def test_plan_cambiado_a_mitad_de_ciclo_no_mueve_la_meta(self):
        self._rutina(self.alumno, self.INI, [(1, 1), (2, 1)])
        self._rutina(
            self.alumno, self.INI + timedelta(days=10),
            [(1, 1), (2, 1), (3, 1), (4, 1)],
        )
        self.assertEqual(meta_del_ciclo(self.alumno, self.INI), 2)


class ResumenTests(_Base):
    INI = date(2026, 11, 1)  # ciclo 1/11 a 28/11
    HOY = date(2026, 11, 10)

    def setUp(self):
        super().setUp()
        self.alumno.fecha_inicio_ciclo = self.INI
        self.alumno.save(update_fields=["fecha_inicio_ciclo"])

    def test_sin_ancla_none(self):
        self.alumno.fecha_inicio_ciclo = None
        self.alumno.save(update_fields=["fecha_inicio_ciclo"])
        self.assertIsNone(resumen_del_ciclo(self.alumno, hoy=self.HOY))

    def test_inactivo_none(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save(update_fields=["estado"])
        self.assertIsNone(resumen_del_ciclo(self.alumno, hoy=self.HOY))

    def test_ancla_futura_none(self):
        self.assertIsNone(resumen_del_ciclo(self.alumno, hoy=date(2026, 10, 20)))

    def test_ciclo_normal(self):
        pares = [(1, s) for s in range(1, 5)] + [(2, s) for s in range(1, 5)]
        self._rutina(self.alumno, self.INI, pares)
        self._ingreso(self.alumno, _local(date(2026, 11, 3)))
        self._marca(self.alumno, date(2026, 11, 5))
        self._marca(self.alumno, date(2026, 11, 3))
        r = resumen_del_ciclo(self.alumno, hoy=self.HOY)
        self.assertIsInstance(r, ResumenCiclo)
        self.assertEqual((r.inicio, r.fin), (date(2026, 11, 1), date(2026, 11, 28)))
        self.assertEqual(r.meta, 8)
        self.assertEqual(set(r.dias), {date(2026, 11, 3), date(2026, 11, 5)})
        self.assertEqual(r.asistidos, 2)
        self.assertEqual(r.faltan, 6)
        self.assertEqual(set(r.dias_lector), {date(2026, 11, 3)})

    def test_solo_cuenta_hasta_hoy(self):
        self._marca(self.alumno, date(2026, 11, 20))
        self._ingreso(self.alumno, _local(date(2026, 11, 21)))
        r = resumen_del_ciclo(self.alumno, hoy=self.HOY)
        self.assertEqual(r.asistidos, 0)
        self.assertEqual(set(r.dias_lector), set())

    def test_faltan_nunca_negativo_y_none_sin_meta(self):
        self._rutina(self.alumno, self.INI, [(1, 1)])
        for k in range(3):
            self._marca(self.alumno, self.INI + timedelta(days=k))
        r = resumen_del_ciclo(self.alumno, hoy=self.HOY)
        self.assertEqual((r.meta, r.asistidos, r.faltan), (1, 3, 0))

        otro = self._alumno("Sin plan", fecha_inicio_ciclo=self.INI)
        r2 = resumen_del_ciclo(otro, hoy=self.HOY)
        self.assertIsNone(r2.meta)
        self.assertIsNone(r2.faltan)

    def test_dias_lector_solo_dias_con_ingreso(self):
        self._marca(self.alumno, date(2026, 11, 4))
        self._ingreso(self.alumno, _local(date(2026, 11, 6)))
        r = resumen_del_ciclo(self.alumno, hoy=self.HOY)
        self.assertEqual(set(r.dias_lector), {date(2026, 11, 6)})
        self.assertEqual(r.asistidos, 2)

    def test_hoy_por_defecto_es_la_fecha_local(self):
        # 23:30 locales del 10/11 = 11/11 en UTC: tiene que usar el día local.
        with patch("django.utils.timezone.now", return_value=_local(self.HOY, 23, 30)):
            r = resumen_del_ciclo(self.alumno)
        self.assertEqual(r.inicio, self.INI)
