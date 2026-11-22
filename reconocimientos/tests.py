"""Pieza 2 de reconocimientos: cálculo del ranking de kilos."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from itertools import count
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from reconocimientos.ranking import (
    MINIMO_PARTICIPANTES,
    TOP_EJERCICIOS,
    TOP_POR_EJERCICIO,
    calcular_ranking,
    participantes,
)
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS

_serie = count(1)


def _gimnasio(slug="vida-plena", habilitado=True):
    return Gimnasio.objects.create(
        nombre=slug, slug=slug, reconocimientos_habilitado=habilitado
    )


def _alumno(gimnasio, nombre="Ana", apellido="Pérez", terminos=VERSION_TERMINOS):
    n = next(_serie)
    usuario = User.objects.create_user(f"u{n}", password="clave-123456")
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO,
        terminos_version=terminos,
    )
    return Alumno.objects.create(
        gimnasio=gimnasio, nombre=nombre, apellido=apellido, perfil=perfil,
        estado=Alumno.Estado.ACTIVO,
    )


def _rutina(alumno, items, fecha_inicio=None, **extra):
    """`items`: lista de (ejercicio, kilos) o (ejercicio, kilos, semana)."""
    rutina = RutinaAsignada.objects.create(
        gimnasio=alumno.gimnasio, alumno=alumno, nombre_snapshot="Plan",
        objetivo_snapshot="Fuerza",
        fecha_inicio=fecha_inicio or timezone.localdate(), **extra,
    )
    for orden, item in enumerate(items, start=1):
        ejercicio, kilos, *resto = item
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina, ejercicio_nombre_snapshot=ejercicio,
            kilos=kilos, semana=resto[0] if resto else 1, dia=1, orden=orden,
            series=3, repeticiones="10",
        )
    return rutina


def _con_kilos(gimnasio, ejercicio, kilos_por_alumno):
    """Un alumno por cada valor de kilos, todos con el mismo ejercicio."""
    alumnos = []
    for kilos in kilos_por_alumno:
        alumno = _alumno(gimnasio)
        _rutina(alumno, [(ejercicio, str(kilos))])
        alumnos.append(alumno)
    return alumnos


class RankingTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()

    def _bloques(self, **kw):
        return calcular_ranking(self.gimnasio, **kw)

    def test_constantes(self):
        self.assertEqual(
            (TOP_EJERCICIOS, TOP_POR_EJERCICIO, MINIMO_PARTICIPANTES), (10, 10, 3)
        )

    def test_ranking_basico(self):
        _con_kilos(self.gimnasio, "Sentadilla", [60, 80, 70])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.ejercicio, "Sentadilla")
        self.assertEqual(bloque.participantes, 3)
        self.assertEqual(
            [f.kilos for f in bloque.filas], [Decimal(80), Decimal(70), Decimal(60)]
        )
        self.assertEqual([f.puesto for f in bloque.filas], [1, 2, 3])
        self.assertIsNone(bloque.tu_fila)

    def test_bandera_apagada_devuelve_vacio(self):
        gym = _gimnasio("apagado", habilitado=False)
        _con_kilos(gym, "Sentadilla", [60, 80, 70])
        self.assertEqual(calcular_ranking(gym), [])

    def test_toma_el_maximo_de_las_cuatro_semanas(self):
        a = _alumno(self.gimnasio)
        _rutina(a, [("Sentadilla", "50", 1), ("Sentadilla", "70", 4)])
        _con_kilos(self.gimnasio, "Sentadilla", [60, 65])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.filas[0].kilos, Decimal(70))

    def test_empate_comparte_puesto(self):
        _con_kilos(self.gimnasio, "Sentadilla", [80, 80, 60])
        (bloque,) = self._bloques()
        self.assertEqual([f.puesto for f in bloque.filas], [1, 1, 3])

    def test_agrupa_por_nombre_normalizado(self):
        for nombre, kilos in [
            ("Sentadilla", "60"), ("sentadilla ", "70"), ("SENTADILLA", "80"),
            ("Sentadilla", "50"),
        ]:
            _rutina(_alumno(self.gimnasio), [(nombre, kilos)])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.participantes, 4)
        self.assertEqual(bloque.ejercicio, "Sentadilla")

    def test_ignora_kilos_no_parseables(self):
        _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        _con_kilos(self.gimnasio, "Sentadilla", ["corporal", "60s", ""])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.participantes, 3)

    def test_minimo_de_participantes(self):
        _con_kilos(self.gimnasio, "Sentadilla", [60, 70])
        self.assertEqual(self._bloques(), [])
        _con_kilos(self.gimnasio, "Sentadilla", [80])
        self.assertEqual(len(self._bloques()), 1)

    def test_top_10_ejercicios_por_participantes(self):
        # 12 ejercicios con 3 a 14 participantes (ninguno repite cantidad).
        for i in range(12):
            _con_kilos(self.gimnasio, f"Ejercicio {i:02d}", [50] * (3 + i))
        nombres = [b.ejercicio for b in self._bloques()]
        self.assertEqual(len(nombres), 10)
        self.assertEqual(nombres[0], "Ejercicio 11")
        self.assertNotIn("Ejercicio 00", nombres)
        self.assertNotIn("Ejercicio 01", nombres)

    def test_top_10_desempata_por_nombre(self):
        for i in range(12):
            _con_kilos(self.gimnasio, f"Ejercicio {i:02d}", [50, 60, 70])
        nombres = [b.ejercicio for b in self._bloques()]
        self.assertEqual(nombres, [f"Ejercicio {i:02d}" for i in range(10)])

    def test_baja_del_ranking_inactivo_y_sin_terminos_no_aparecen(self):
        _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        fuera = _con_kilos(self.gimnasio, "Sentadilla", [90, 91, 92, 93])
        fuera[0].aparece_en_ranking = False
        fuera[0].save(update_fields=["aparece_en_ranking"])
        fuera[1].estado = Alumno.Estado.INACTIVO
        fuera[1].save(update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo"])
        fuera[2].perfil.terminos_version = 0
        fuera[2].perfil.save(update_fields=["terminos_version"])
        fuera[3].perfil = None
        fuera[3].save(update_fields=["perfil"])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.participantes, 3)
        self.assertEqual(max(f.kilos for f in bloque.filas), Decimal(80))

    def test_alumno_dado_de_baja_sale_y_al_reactivarlo_vuelve(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        a = alumnos[0]
        a.estado = Alumno.Estado.INACTIVO
        a.save(update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo"])
        self.assertEqual(self._bloques(), [])
        a.estado = Alumno.Estado.ACTIVO
        a.save(update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo"])
        self.assertEqual(self._bloques()[0].participantes, 3)

    def test_plan_abandonado_sale(self):
        hoy = timezone.localdate()
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        rutina = alumnos[0].rutinas_asignadas.get()
        rutina.fecha_inicio = hoy - timedelta(days=43)
        rutina.save(update_fields=["fecha_inicio"])
        self.assertEqual(self._bloques(), [])
        rutina.fecha_inicio = hoy - timedelta(days=42)
        rutina.save(update_fields=["fecha_inicio"])
        self.assertEqual(self._bloques()[0].participantes, 3)
        # Un plan nuevo cargado hoy lo devuelve al ranking.
        rutina.fecha_inicio = hoy - timedelta(days=43)
        rutina.save(update_fields=["fecha_inicio"])
        _rutina(alumnos[0], [("Sentadilla", "75")])
        self.assertEqual(self._bloques()[0].participantes, 3)

    def test_participantes_es_la_unica_definicion(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        otro = _alumno(self.gimnasio)
        _rutina(otro, [("Sentadilla", "corporal")])
        baja = _alumno(self.gimnasio)
        baja.aparece_en_ranking = False
        baja.save(update_fields=["aparece_en_ranking"])
        _rutina(baja, [("Sentadilla", "99")])
        ids = set(participantes(self.gimnasio).values_list("alumno_id", flat=True))
        self.assertIn(otro.id, ids)  # vigente aunque sin kilos válidos
        self.assertNotIn(baja.id, ids)
        self.assertTrue({a.id for a in alumnos} <= ids)
        (bloque,) = self._bloques()
        self.assertEqual(bloque.participantes, 3)

    def test_rutina_futura_o_archivada_no_cuenta(self):
        hoy = timezone.localdate()
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        _rutina(alumnos[0], [("Sentadilla", "200")], fecha_inicio=hoy + timedelta(days=3))
        _rutina(alumnos[1], [("Sentadilla", "300")], activa=False)
        (bloque,) = self._bloques()
        self.assertEqual(max(f.kilos for f in bloque.filas), Decimal(80))
        self.assertEqual(bloque.participantes, 3)

    def test_misma_fecha_inicio_gana_el_mayor_id(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        _rutina(alumnos[0], [("Sentadilla", "40")])
        (bloque,) = self._bloques()
        self.assertEqual(sorted(f.kilos for f in bloque.filas), [40, 70, 80])

    def test_solo_la_rutina_vigente(self):
        hoy = timezone.localdate()
        a = _alumno(self.gimnasio)
        _rutina(a, [("Sentadilla", "100")], fecha_inicio=hoy - timedelta(days=20))
        _rutina(a, [("Sentadilla", "60")], fecha_inicio=hoy - timedelta(days=2))
        _con_kilos(self.gimnasio, "Sentadilla", [50, 55])
        (bloque,) = self._bloques()
        self.assertEqual(max(f.kilos for f in bloque.filas), Decimal(60))

    def test_aislamiento_entre_gimnasios(self):
        _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
        otro = _gimnasio("otro")
        _con_kilos(otro, "Sentadilla", [500, 400, 300])
        _con_kilos(otro, "Peso muerto", [1, 2, 3])
        (bloque,) = self._bloques()
        self.assertEqual(bloque.participantes, 3)
        self.assertEqual(max(f.kilos for f in bloque.filas), Decimal(80))

    def test_nombre_con_inicial(self):
        _rutina(_alumno(self.gimnasio, "Lucía", "Martínez"), [("Sentadilla", "60")])
        _rutina(_alumno(self.gimnasio, "Pedro", ""), [("Sentadilla", "70")])
        _con_kilos(self.gimnasio, "Sentadilla", [80])
        (bloque,) = self._bloques()
        nombres = {f.nombre for f in bloque.filas}
        self.assertIn("Lucía M.", nombres)
        self.assertIn("Pedro", nombres)
        self.assertNotIn("Martínez", " ".join(nombres))

    def test_tu_fila_fuera_del_top(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", list(range(100, 88, -1)))
        yo = alumnos[-1]
        (bloque,) = self._bloques(alumno=yo)
        self.assertEqual(len(bloque.filas), TOP_POR_EJERCICIO)
        self.assertEqual(bloque.tu_fila.puesto, 12)
        self.assertTrue(bloque.tu_fila.es_propia)
        self.assertFalse(any(f.es_propia for f in bloque.filas))

    def test_tu_fila_dentro_del_top(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", [100, 90, 80])
        (bloque,) = self._bloques(alumno=alumnos[1])
        self.assertIsNone(bloque.tu_fila)
        propias = [f for f in bloque.filas if f.es_propia]
        self.assertEqual([f.puesto for f in propias], [2])

    def test_alumno_dado_de_baja_no_tiene_tu_fila(self):
        alumnos = _con_kilos(self.gimnasio, "Sentadilla", list(range(100, 88, -1)))
        yo = alumnos[-1]
        yo.estado = Alumno.Estado.INACTIVO
        yo.save(update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo"])
        (bloque,) = self._bloques(alumno=yo)
        self.assertIsNone(bloque.tu_fila)

    def test_cantidad_de_queries_constante(self):
        for i in range(5):
            _con_kilos(self.gimnasio, f"Ej {i % 3}", [50 + i])
        with self.assertNumQueries(2):
            calcular_ranking(self.gimnasio)
        gym = _gimnasio("grande")
        for e in range(12):
            _con_kilos(gym, f"Ej {e}", [50 + n for n in range(40)])
        with self.assertNumQueries(2):
            calcular_ranking(gym)

    def test_items_repetidos_no_cambian_el_resultado_ni_las_queries(self):
        a = _alumno(self.gimnasio)
        _rutina(a, [("Sentadilla", "70")] * 30)
        _con_kilos(self.gimnasio, "Sentadilla", [60, 65])
        with self.assertNumQueries(2):
            (bloque,) = calcular_ranking(self.gimnasio)
        self.assertEqual(bloque.filas[0].kilos, Decimal(70))

    def test_dia_local_de_la_vigencia(self):
        # 01:00 UTC del 10-mar es todavía el 9-mar a las 22:00 en UTC-3.
        ahora = datetime(2026, 3, 10, 1, 0, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", return_value=ahora):
            self.assertEqual(timezone.localdate(), date(2026, 3, 9))
            alumnos = _con_kilos(self.gimnasio, "Sentadilla", [60, 70, 80])
            rutina = alumnos[0].rutinas_asignadas.get()
            rutina.fecha_inicio = date(2026, 3, 10)  # «mañana» en hora local
            rutina.save(update_fields=["fecha_inicio"])
            self.assertEqual(self._bloques(), [])
            rutina.fecha_inicio = date(2026, 3, 9)
            rutina.save(update_fields=["fecha_inicio"])
            self.assertEqual(len(self._bloques()), 1)


# --- Pantalla «Ranking» del alumno -----------------------------------------

from django.urls import reverse  # noqa: E402


def _staff(gimnasio):
    usuario = User.objects.create_user(f"s{next(_serie)}", password="clave-123456")
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return usuario


class RankingVistaTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.url = reverse("reconocimientos:ranking")
        self.yo = _alumno(self.gimnasio, "Yo", "Mismo")
        _rutina(self.yo, [("Sentadilla", "60")])
        self.otros = []
        for nombre, apellido, kilos in [
            ("Lucía", "Martínez", "80"), ("Pedro", "Gómez", "70,5"),
        ]:
            alumno = _alumno(self.gimnasio, nombre, apellido)
            _rutina(alumno, [("Sentadilla", kilos)])
            self.otros.append(alumno)
        self.client.force_login(self.yo.perfil.usuario)

    def test_url(self):
        self.assertEqual(self.url, "/ranking/")

    def test_muestra_el_ranking_sin_apellido_completo(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Sentadilla")
        self.assertContains(respuesta, "Lucía M.")
        self.assertNotContains(respuesta, "Martínez")
        self.assertNotContains(respuesta, "Gómez")
        self.assertContains(respuesta, "según la rutina que le asignó el gimnasio")
        self.assertContains(respuesta, reverse("alumnos:mi_perfil"))

    def test_kilos_sin_ceros_de_mas_y_con_coma(self):
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, "80 kg")
        self.assertContains(respuesta, "70,5 kg")
        self.assertNotContains(respuesta, "80,0")
        self.assertNotContains(respuesta, "70.5")

    def test_bandera_apagada_404(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(reconocimientos_habilitado=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonimo_va_al_login(self):
        self.client.logout()
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def test_staff_403(self):
        self.client.force_login(_staff(self.gimnasio))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_alumno_sin_ficha_estado_vacio(self):
        usuario = User.objects.create_user("sinficha", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO,
            terminos_version=VERSION_TERMINOS,
        )
        self.client.force_login(usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Sentadilla")

    def test_sin_suficientes_participantes_mensaje(self):
        gym = _gimnasio("chico")
        alumno = _alumno(gym)
        _rutina(alumno, [("Sentadilla", "50")])
        self.client.force_login(alumno.perfil.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(
            respuesta,
            "Todavía no hay suficientes alumnos con kilos cargados para armar el ranking",
        )

    def test_aislamiento_entre_gimnasios(self):
        gym = _gimnasio("otro")
        _con_kilos(gym, "Sentadilla", [10, 20, 30])
        extra = _alumno(gym, "Zacarías", "Quiroga")
        _rutina(extra, [("Sentadilla", "99")])
        respuesta = self.client.get(self.url)
        self.assertNotContains(respuesta, "Zacarías")
        self.assertNotContains(respuesta, "99 kg")

    def test_fila_propia_marcada(self):
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'aria-current="true"', count=1)
        self.assertContains(respuesta, "Vos")

    def test_tu_puesto_fuera_del_top(self):
        gym = _gimnasio("grande")
        _con_kilos(gym, "Sentadilla", [100 + n for n in range(TOP_POR_EJERCICIO)])
        yo = _alumno(gym, "Rezagado", "Z")
        _rutina(yo, [("Sentadilla", "5")])
        self.client.force_login(yo.perfil.usuario)
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, "Tu puesto")
        self.assertContains(respuesta, "11")
        self.assertContains(respuesta, "5 kg")

    def test_suplantando_get_200(self):
        staff = _staff(self.gimnasio)
        self.client.force_login(staff)
        respuesta = self.client.post(reverse("suplantar", args=[self.yo.pk]))
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_nav_con_bandera(self):
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'href="/ranking/" aria-current="page"')
        respuesta = self.client.get(reverse("alumnos:mi_perfil"))
        self.assertContains(respuesta, 'href="/ranking/"')
        self.assertNotContains(respuesta, 'href="/ranking/" aria-current')

    def test_nav_sin_bandera(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(reconocimientos_habilitado=False)
        respuesta = self.client.get(reverse("turnos:mis_turnos"))
        self.assertNotContains(respuesta, 'href="/ranking/"')

    def test_queries_constantes(self):
        self.client.get(self.url)  # calienta
        with CaptureQueriesContext(connection) as chico:
            self.client.get(self.url)
        for e in range(6):
            _con_kilos(self.gimnasio, f"Ej {e}", [50 + n for n in range(15)])
        with CaptureQueriesContext(connection) as grande:
            self.client.get(self.url)
        self.assertEqual(len(chico), len(grande))
