"""Pieza 5, tarea 3: candidatos al premio, otorgarlo y aplicarlo a las cuotas.

Reloj congelado en una fecha lejana (`HOY`): `timezone.localdate()` devuelve
siempre ese día, así los bordes de la ventana no dependen de cuándo corre la
suite.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from itertools import count
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from ingreso.models import Dispositivo, EventoIngreso
from novedades.models import Novedad
from pagos.descuentos import aplicar_descuento as aplicar_descuento_real
from pagos.models import CobroOnlineCuota, Cuota, generar_pagos_pendientes
from reconocimientos import premios
from reconocimientos.models import Premio, PremioAplicacion, PremioOtorgado
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS

HOY = date(2031, 6, 18)
UN_DIA = timedelta(days=1)
#: Pares (día, semana) de una rutina de 4 sesiones: meta = 4.
CUATRO = [(1, 1), (2, 1), (3, 1), (4, 1)]

_serie = count(1)


def _ciclo(ancla, k=0):
    inicio = ancla + timedelta(days=28 * k)
    return inicio, inicio + timedelta(days=27)


class _Base(TestCase):
    def setUp(self):
        reloj = patch("django.utils.timezone.localdate", return_value=HOY)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.gimnasio = Gimnasio.objects.create(
            nombre="G", slug="g", reconocimientos_habilitado=True
        )
        self.otro = Gimnasio.objects.create(
            nombre="O", slug="o", reconocimientos_habilitado=True
        )
        # Ciclo cerrado hace 3 días: [HOY-30, HOY-3].
        self.ancla = HOY - timedelta(days=30)
        self.alumno = self._alumno("Ana", ancla=self.ancla)

    # -- armado ---------------------------------------------------------

    def _alumno(self, nombre, ancla=None, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, apellido="Pérez",
            estado=Alumno.Estado.ACTIVO, fecha_nacimiento=date(1990, 1, 1),
            fecha_inicio_ciclo=ancla or self.ancla,
        )
        datos.update(extra)
        return Alumno.objects.create(**datos)

    def _rutina(self, alumno, inicio, pares=CUATRO, **extra):
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

    def _marcas(self, alumno, inicio, cuantas, origen=Asistencia.Origen.MANUAL):
        for i in range(cuantas):
            Asistencia.objects.create(
                gimnasio=alumno.gimnasio, alumno=alumno, fecha=inicio + i * UN_DIA,
                origen=origen,
            )

    def _perfecto(self, alumno=None, inicio=None, asistidas=4):
        """Rutina de 4 sesiones vigente desde `inicio` y `asistidas` días."""
        alumno = alumno or self.alumno
        inicio = inicio or alumno.fecha_inicio_ciclo
        self._rutina(alumno, inicio)
        self._marcas(alumno, inicio, asistidas)
        return alumno

    def _cuota(self, alumno, inicio, estado=Cuota.Estado.PENDIENTE,
               monto=Decimal("10000"), descuento=Decimal("0"), motivo=""):
        return Cuota.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, periodo_inicio=inicio,
            periodo_fin=inicio + timedelta(days=27), monto=monto, estado=estado,
            descuento=descuento, descuento_motivo=motivo,
        )

    def _premio(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, nombre="Un mes 20%",
            tipo=Premio.Tipo.PORCENTAJE, valor=Decimal("20"), cuotas=1,
        )
        datos.update(extra)
        return Premio.objects.create(**datos)

    def _perfil_staff(self):
        usuario = User.objects.create_user(f"s{next(_serie)}", password="clave-123456")
        return Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF,
            terminos_version=VERSION_TERMINOS,
        )

    def _otorgado(self, alumno=None, inicio=None, **extra):
        alumno = alumno or self.alumno
        inicio = inicio or self.ancla
        datos = dict(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre="Un mes 20%",
            tipo=Premio.Tipo.PORCENTAJE, valor=Decimal("20"), cuotas=1,
            periodo_inicio=inicio, periodo_fin=inicio + timedelta(days=27),
            meta=4, asistidas=4,
        )
        datos.update(extra)
        return PremioOtorgado.objects.create(**datos)

    def _ids(self, lista):
        return [(c.alumno_id, c.inicio) for c in lista]


# ---------------------------------------------------------------------------
# candidatos
# ---------------------------------------------------------------------------


class CandidatosTests(_Base):
    def test_califica_con_asistidas_igual_a_meta(self):
        self._perfecto(asistidas=4)
        lista = premios.candidatos(self.gimnasio)
        self.assertEqual(len(lista), 1)
        c = lista[0]
        self.assertEqual(c.alumno_id, self.alumno.pk)
        self.assertEqual((c.nombre, c.apellido), ("Ana", "Pérez"))
        self.assertEqual((c.inicio, c.fin), _ciclo(self.ancla))
        self.assertEqual((c.meta, c.asistidas), (4, 4))
        self.assertFalse(c.es_menor)
        self.assertFalse(c.edad_sin_cargar)

    def test_califica_con_mas_que_la_meta(self):
        self._perfecto(asistidas=6)
        (c,) = premios.candidatos(self.gimnasio)
        self.assertEqual((c.meta, c.asistidas), (4, 6))

    def test_no_califica_con_meta_menos_uno(self):
        self._perfecto(asistidas=3)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_lector_y_marca_el_mismo_dia_cuentan_uno(self):
        self._rutina(self.alumno, self.ancla)
        self._marcas(self.alumno, self.ancla, 3)
        disp = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta", token_hash="h"
        )
        for dia in (self.ancla, self.ancla + 10 * UN_DIA):  # el 1.º repite marca
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=disp, alumno=self.alumno,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                sentido=EventoIngreso.Sentido.ENTRADA,
                momento=timezone.make_aware(datetime(dia.year, dia.month, dia.day, 9)),
            )
        (c,) = premios.candidatos(self.gimnasio)
        self.assertEqual(c.asistidas, 4)

    def test_sin_rutina_no_hay_meta_y_no_califica(self):
        self._marcas(self.alumno, self.ancla, 20)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_rutina_vacia_no_califica(self):
        self._rutina(self.alumno, self.ancla, pares=[])
        self._marcas(self.alumno, self.ancla, 20)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_meta_es_la_de_la_rutina_vigente_al_inicio(self):
        self._rutina(self.alumno, self.ancla)  # 4 sesiones
        # Con una cuota emitida, la rutina nueva no mueve el ancla (señal de
        # `alumnos`): así el ciclo queda donde está.
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.PAGADO)
        # Un plan más exigente a mitad de ciclo no mueve la meta.
        self._rutina(self.alumno, self.ancla + 5 * UN_DIA, pares=CUATRO + [(1, 2), (2, 2)])
        # Una rutina inactiva más nueva tampoco.
        self._rutina(self.alumno, self.ancla, pares=[(1, 1)], activa=False)
        self._marcas(self.alumno, self.ancla, 4)
        (c,) = premios.candidatos(self.gimnasio)
        self.assertEqual(c.meta, 4)

    def test_dos_rutinas_el_mismo_dia_gana_el_mayor_id(self):
        self._rutina(self.alumno, self.ancla, pares=[(1, 1)])
        self._rutina(self.alumno, self.ancla)  # 4 sesiones, id mayor
        self._marcas(self.alumno, self.ancla, 3)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_inactivo_no_califica(self):
        self._perfecto()
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save()
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_sin_ancla_no_califica(self):
        self._perfecto()
        Alumno.objects.filter(pk=self.alumno.pk).update(fecha_inicio_ciclo=None)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_menor_califica_y_viene_marcado(self):
        menor = self._alumno("Teo", fecha_nacimiento=HOY - timedelta(days=365 * 15))
        self._perfecto(menor)
        (c,) = premios.candidatos(self.gimnasio, alumno_ids=[menor.pk])
        self.assertTrue(c.es_menor)
        self.assertFalse(c.edad_sin_cargar)

    def test_cumple_18_justo_hoy_no_es_menor(self):
        adulto = self._alumno("Bea", fecha_nacimiento=HOY.replace(year=HOY.year - 18))
        self._perfecto(adulto)
        (c,) = premios.candidatos(self.gimnasio, alumno_ids=[adulto.pk])
        self.assertFalse(c.es_menor)
        self.assertFalse(c.edad_sin_cargar)

    def test_sin_fecha_de_nacimiento_se_trata_como_menor(self):
        # No se puede verificar la edad: para la privacidad, cuenta como menor.
        sin_fecha = self._alumno("Sin", fecha_nacimiento=None)
        self._perfecto(sin_fecha)
        (c,) = premios.candidatos(self.gimnasio, alumno_ids=[sin_fecha.pk])
        self.assertTrue(c.es_menor)
        self.assertTrue(c.edad_sin_cargar)

    def test_deuda_del_propio_ciclo_excluye(self):
        self._perfecto()
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.VENCIDO)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_deuda_de_un_ciclo_anterior_excluye(self):
        self._perfecto()
        self._cuota(self.alumno, self.ancla - 28 * UN_DIA, estado=Cuota.Estado.PENDIENTE)
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.PAGADO)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_borde_de_deuda_inicio_igual_al_fin_del_ciclo(self):
        # Una cuota desalineada (ancla corregida) que arranca el último día del
        # ciclo es deuda de ese ciclo; una que arranca al día siguiente, no.
        self._perfecto()
        fin = self.ancla + 27 * UN_DIA
        self._cuota(self.alumno, fin + UN_DIA)
        self.assertEqual(len(premios.candidatos(self.gimnasio)), 1)
        self._cuota(self.alumno, fin)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def test_cuota_pagada_o_anulada_no_es_deuda(self):
        self._perfecto()
        self._cuota(self.alumno, self.ancla - 28 * UN_DIA, estado=Cuota.Estado.ANULADO)
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.PAGADO)
        self.assertEqual(len(premios.candidatos(self.gimnasio)), 1)

    def test_la_preemitida_del_ciclo_siguiente_no_excluye(self):
        self._perfecto()
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.PAGADO)
        self._cuota(self.alumno, self.ancla + 28 * UN_DIA, estado=Cuota.Estado.PENDIENTE)
        self.assertEqual(len(premios.candidatos(self.gimnasio)), 1)

    def test_deuda_de_otro_alumno_no_lo_afecta(self):
        self._perfecto()
        otro = self._alumno("Otro")
        self._cuota(otro, self.ancla, estado=Cuota.Estado.VENCIDO)
        self.assertEqual(self._ids(premios.candidatos(self.gimnasio)),
                         [(self.alumno.pk, self.ancla)])

    def test_ciclo_que_termina_hoy_no_es_candidato(self):
        ancla = HOY - 27 * UN_DIA  # ciclo [HOY-27, HOY]: no cerró
        alumno = self._perfecto(self._alumno("Hoy", ancla=ancla))
        self.assertEqual(premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk]), [])

    def test_ciclo_que_termino_ayer_si(self):
        ancla = HOY - 28 * UN_DIA  # [HOY-28, HOY-1]
        alumno = self._perfecto(self._alumno("Ayer", ancla=ancla))
        self.assertEqual(self._ids(premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk])),
                         [(alumno.pk, ancla)])

    def test_borde_de_ventana_hoy_menos_35_si(self):
        ancla = HOY - 62 * UN_DIA  # ciclo 0 = [HOY-62, HOY-35]
        alumno = self._perfecto(self._alumno("Borde", ancla=ancla))
        inicios = [c.inicio for c in premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk])]
        self.assertIn(ancla, inicios)

    def test_borde_de_ventana_hoy_menos_36_no(self):
        ancla = HOY - 63 * UN_DIA  # ciclo 0 = [HOY-63, HOY-36]
        alumno = self._perfecto(self._alumno("Fuera", ancla=ancla))
        inicios = [c.inicio for c in premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk])]
        self.assertNotIn(ancla, inicios)

    def test_dos_ciclos_candidatos(self):
        ancla = HOY - 58 * UN_DIA  # [HOY-58, HOY-31] y [HOY-30, HOY-3]
        alumno = self._alumno("Dos", ancla=ancla)
        self._rutina(alumno, ancla)
        self._marcas(alumno, ancla, 4)
        self._marcas(alumno, ancla + 28 * UN_DIA, 5)
        lista = premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk])
        self.assertEqual(
            [(c.inicio, c.asistidas) for c in lista],
            [(ancla, 4), (ancla + 28 * UN_DIA, 5)],
        )

    def test_dos_ciclos_cada_uno_con_su_asistencia(self):
        ancla = HOY - 58 * UN_DIA
        alumno = self._alumno("Uno", ancla=ancla)
        self._rutina(alumno, ancla)
        self._marcas(alumno, ancla, 2)  # primero: 2 de 4
        self._marcas(alumno, ancla + 28 * UN_DIA, 4)
        lista = premios.candidatos(self.gimnasio, alumno_ids=[alumno.pk])
        self.assertEqual([c.inicio for c in lista], [ancla + 28 * UN_DIA])

    def test_otorgado_vigente_lo_saca_y_anulado_no(self):
        self._perfecto()
        otorgado = self._otorgado()
        self.assertEqual(premios.candidatos(self.gimnasio), [])
        PremioOtorgado.objects.filter(pk=otorgado.pk).update(
            estado=PremioOtorgado.Estado.ANULADO
        )
        self.assertEqual(len(premios.candidatos(self.gimnasio)), 1)

    def test_otro_gimnasio_no_aparece(self):
        ajeno = self._perfecto(self._alumno("Ajena", gimnasio=self.otro))
        self._perfecto()
        self.assertEqual(self._ids(premios.candidatos(self.gimnasio)),
                         [(self.alumno.pk, self.ancla)])
        # Aunque se pida por id, un alumno ajeno no aparece.
        self.assertEqual(premios.candidatos(self.gimnasio, alumno_ids=[ajeno.pk]), [])

    def test_alumno_ids_filtra(self):
        self._perfecto()
        otra = self._perfecto(self._alumno("Otra"))
        self.assertEqual(self._ids(premios.candidatos(self.gimnasio, alumno_ids=[otra.pk])),
                         [(otra.pk, self.ancla)])

    def test_ancla_futura_no_rompe(self):
        self._alumno("Futuro", ancla=HOY + 10 * UN_DIA)
        self.assertEqual(premios.candidatos(self.gimnasio), [])

    def _sembrar(self, cantidad):
        """`cantidad` alumnos candidatos, con dos rutinas cada uno, marcas,
        ingresos del lector, cuotas y, uno de cada tres, un premio ya dado."""
        disp, _ = Dispositivo.objects.get_or_create(
            gimnasio=self.gimnasio, nombre="Puerta", defaults={"token_hash": "q"}
        )
        ancla = HOY - 58 * UN_DIA
        for i in range(cantidad):
            alumno = self._alumno(f"N{cantidad}-{i}", ancla=ancla)
            # Las cuotas antes que las rutinas: con cuotas, una rutina nueva no
            # mueve el ancla (señal de `alumnos`).
            self._cuota(alumno, ancla, estado=Cuota.Estado.PAGADO)
            self._cuota(alumno, ancla + 56 * UN_DIA)
            self._rutina(alumno, ancla)
            self._rutina(alumno, ancla + 40 * UN_DIA, pares=[(1, 1)])
            self._marcas(alumno, ancla, 4, origen=Asistencia.Origen.TURNO)
            self._marcas(alumno, ancla + 28 * UN_DIA, 4)
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=disp, alumno=alumno,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                sentido=EventoIngreso.Sentido.ENTRADA,
                momento=timezone.make_aware(datetime(ancla.year, ancla.month, ancla.day, 9)),
            )
            if i % 3 == 0:
                self._otorgado(alumno, inicio=ancla)

    def test_queries_constantes_con_3_y_con_40_alumnos(self):
        medidas = []
        for cantidad in (3, 40):
            self._sembrar(cantidad)
            with CaptureQueriesContext(connection) as ctx:
                lista = premios.candidatos(self.gimnasio)
            medidas.append(len(ctx.captured_queries))
            self.assertGreaterEqual(len(lista), cantidad)
        self.assertEqual(medidas[0], medidas[1])
        self.assertLessEqual(medidas[0], 10)


# ---------------------------------------------------------------------------
# otorgar
# ---------------------------------------------------------------------------


class OtorgarTests(_Base):
    def setUp(self):
        super().setUp()
        self._perfecto()
        self.premio = self._premio()
        self.staff = self._perfil_staff()

    def _otorgar(self, premio=None, inicio=None, alumno=None):
        return premios.otorgar(
            self.gimnasio, (alumno or self.alumno).pk, inicio or self.ancla,
            (premio or self.premio).pk, self.staff,
        )

    def test_otorga_con_los_datos_congelados(self):
        premio = self._premio(
            nombre="Trimestre", tipo=Premio.Tipo.FIJO, valor=Decimal("1500"),
            cuotas=3, da_insignia=True,
        )
        otorgado, motivo = self._otorgar(premio)
        self.assertIsNotNone(otorgado, motivo)
        otorgado.refresh_from_db()
        self.assertEqual(otorgado.premio, premio)
        self.assertEqual(otorgado.gimnasio, self.gimnasio)
        self.assertEqual(
            (otorgado.nombre, otorgado.tipo, otorgado.valor, otorgado.cuotas,
             otorgado.da_insignia),
            ("Trimestre", Premio.Tipo.FIJO, Decimal("1500"), 3, True),
        )
        inicio, fin = _ciclo(self.ancla)
        self.assertEqual((otorgado.periodo_inicio, otorgado.periodo_fin), (inicio, fin))
        self.assertEqual((otorgado.meta, otorgado.asistidas), (4, 4))
        self.assertEqual(otorgado.estado, PremioOtorgado.Estado.OTORGADO)
        self.assertEqual(otorgado.otorgado_por, self.staff)
        self.assertEqual(otorgado.insignia_hasta, fin + timedelta(days=28))

    def test_sin_insignia_no_hay_fecha(self):
        premio = self._premio(nombre="Sin", da_insignia=False)
        otorgado, _ = self._otorgar(premio)
        self.assertIsNone(otorgado.insignia_hasta)

    def test_editar_el_premio_despues_no_cambia_el_otorgado(self):
        otorgado, _ = self._otorgar()
        Premio.objects.filter(pk=self.premio.pk).update(
            nombre="Otro", valor=Decimal("50"), cuotas=5, activo=False
        )
        otorgado.refresh_from_db()
        self.assertEqual(
            (otorgado.nombre, otorgado.valor, otorgado.cuotas),
            ("Un mes 20%", Decimal("20"), 1),
        )

    def test_crea_una_novedad_personal(self):
        self._otorgar()
        (novedad,) = Novedad.objects.all()
        self.assertEqual(novedad.gimnasio, self.gimnasio)
        self.assertEqual(novedad.alumno, self.alumno)
        self.assertEqual(novedad.titulo, "¡Asistencia perfecta!")
        self.assertIn("Un mes 20%", novedad.mensaje)
        self.assertIn("20%", novedad.mensaje)
        self.assertEqual(novedad.fecha_publicacion, HOY)
        self.assertEqual(novedad.visible_hasta, HOY + timedelta(days=60))
        self.assertTrue(novedad.activa)

    def test_mensaje_del_fijo_y_del_obsequio(self):
        fijo = self._premio(nombre="Fijo", tipo=Premio.Tipo.FIJO,
                            valor=Decimal("1500"), cuotas=3)
        self._otorgar(fijo)
        self.assertIn("$ 1.500", Novedad.objects.get().mensaje)
        self.assertIn("3 cuotas", Novedad.objects.get().mensaje)
        Novedad.objects.all().delete()
        PremioOtorgado.objects.update(estado=PremioOtorgado.Estado.ANULADO)
        regalo = self._premio(nombre="Remera", tipo=Premio.Tipo.OBSEQUIO,
                              valor=None, texto="Remera del gimnasio")
        self._otorgar(regalo)
        self.assertIn("Remera del gimnasio", Novedad.objects.get().mensaje)

    def test_la_novedad_no_avisa_a_todo_el_gimnasio(self):
        with patch("notificaciones.services.notificar_a_gimnasio") as masivo, \
                patch("notificaciones.services.notificar_a_usuario"):
            with self.captureOnCommitCallbacks(execute=True):
                self._otorgar()
        masivo.assert_not_called()

    def test_aplica_el_descuento_en_el_acto(self):
        siguiente = self._cuota(self.alumno, self.ancla + 28 * UN_DIA)
        otorgado, _ = self._otorgar()
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, Decimal("2000.00"))
        self.assertEqual(PremioAplicacion.objects.get().otorgado, otorgado)

    def test_obsequio_no_toca_cuotas(self):
        siguiente = self._cuota(self.alumno, self.ancla + 28 * UN_DIA)
        regalo = self._premio(nombre="Remera", tipo=Premio.Tipo.OBSEQUIO,
                              valor=None, texto="Remera")
        otorgado, _ = self._otorgar(regalo)
        self.assertIsNotNone(otorgado)
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, 0)
        self.assertFalse(PremioAplicacion.objects.exists())

    def _no_escribio_nada(self):
        self.assertFalse(PremioOtorgado.objects.exists())
        self.assertFalse(Novedad.objects.exists())
        self.assertFalse(PremioAplicacion.objects.exists())

    def test_alumno_que_dejo_de_calificar(self):
        Asistencia.objects.filter(alumno=self.alumno).first().delete()
        otorgado, motivo = self._otorgar()
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_NO_CALIFICA)
        self._no_escribio_nada()

    def test_alumno_dado_de_baja_entre_listado_y_post(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save()
        otorgado, motivo = self._otorgar()
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_NO_CALIFICA)
        self._no_escribio_nada()

    def test_alumno_con_deuda_nueva(self):
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.VENCIDO)
        otorgado, _ = self._otorgar()
        self.assertIsNone(otorgado)
        self._no_escribio_nada()

    def test_premio_de_otro_gimnasio(self):
        ajeno = self._premio(gimnasio=self.otro)
        otorgado, motivo = self._otorgar(ajeno)
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_PREMIO_INVALIDO)
        self._no_escribio_nada()

    def test_premio_inactivo(self):
        Premio.objects.filter(pk=self.premio.pk).update(activo=False, por_defecto=True)
        otorgado, motivo = self._otorgar()
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_PREMIO_INVALIDO)
        self._no_escribio_nada()

    def test_premio_inexistente(self):
        otorgado, motivo = premios.otorgar(
            self.gimnasio, self.alumno.pk, self.ancla, 999999, self.staff
        )
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_PREMIO_INVALIDO)

    def test_ciclo_fuera_de_ventana(self):
        # Un ciclo inventado (no arranca en el ancla) y uno viejo.
        for inicio in (self.ancla + UN_DIA, self.ancla - 28 * UN_DIA):
            otorgado, motivo = self._otorgar(inicio=inicio)
            self.assertIsNone(otorgado)
            self.assertEqual(motivo, premios.MOTIVO_NO_CALIFICA)
        self._no_escribio_nada()

    def test_ciclo_que_ya_salio_de_la_ventana(self):
        with patch("django.utils.timezone.localdate", return_value=HOY + 33 * UN_DIA):
            otorgado, motivo = self._otorgar()  # fin = HOY-3 = (HOY+33) - 36
        self.assertIsNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_NO_CALIFICA)
        self._no_escribio_nada()

    def test_alumno_de_otro_gimnasio(self):
        ajeno = self._perfecto(self._alumno("Ajena", gimnasio=self.otro))
        otorgado, motivo = self._otorgar(alumno=ajeno)
        self.assertIsNone(otorgado)
        self._no_escribio_nada()

    def test_doble_clic_un_otorgado_y_una_novedad(self):
        siguiente = self._cuota(self.alumno, self.ancla + 28 * UN_DIA)
        primero, _ = self._otorgar()
        segundo, motivo = self._otorgar()
        self.assertEqual(segundo, primero)
        self.assertEqual(motivo, premios.MOTIVO_YA_OTORGADO)
        self.assertEqual(PremioOtorgado.objects.count(), 1)
        self.assertEqual(Novedad.objects.count(), 1)
        self.assertEqual(PremioAplicacion.objects.count(), 1)
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, Decimal("2000.00"))

    def test_dos_pestanas_a_la_vez_choca_el_indice_y_devuelve_el_existente(self):
        # La otra pestaña ganó entre la revalidación y el INSERT: el chequeo
        # previo no lo ve y `candidatos` todavía lo lista.
        (candidato,) = premios.candidatos(self.gimnasio)
        existente = self._otorgado()
        with patch.object(premios, "_otorgado_vigente", return_value=None), \
                patch.object(premios, "candidatos", return_value=[candidato]):
            otorgado, motivo = self._otorgar()
        self.assertEqual(otorgado, existente)
        self.assertEqual(motivo, premios.MOTIVO_YA_OTORGADO)
        self.assertEqual(PremioOtorgado.objects.count(), 1)
        self.assertFalse(Novedad.objects.exists())

    def test_si_la_aplicacion_revienta_el_premio_y_la_novedad_quedan(self):
        siguiente = self._cuota(self.alumno, self.ancla + 28 * UN_DIA)
        with patch.object(premios, "aplicar_pendientes", side_effect=RuntimeError("boom")):
            with self.assertLogs("reconocimientos.premios", level="ERROR") as logs:
                otorgado, motivo = self._otorgar()
        self.assertIsNotNone(otorgado)
        self.assertEqual(motivo, premios.MOTIVO_OTORGADO)
        self.assertTrue(PremioOtorgado.objects.filter(pk=otorgado.pk).exists())
        self.assertEqual(Novedad.objects.count(), 1)
        self.assertIn("boom", "\n".join(logs.output))
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, 0)
        # El cron (idempotente) lo completa después.
        self.assertEqual(premios.aplicar_pendientes(), 1)
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, Decimal("2000.00"))

    def test_la_aplicacion_corre_fuera_de_la_transaccion_de_otorgar(self):
        # El `TestCase` ya envuelve el test en atomics: se compara la cantidad
        # de savepoints abiertos al aplicar contra la de afuera de `otorgar`.
        afuera = len(connection.savepoint_ids)
        adentro = []

        def espia(*args, **kwargs):
            adentro.append(len(connection.savepoint_ids))
            return 0

        with patch.object(premios, "aplicar_pendientes", side_effect=espia):
            self._otorgar()
        self.assertEqual(adentro, [afuera])

    def test_doble_clic_reintenta_la_aplicacion_sin_otra_novedad(self):
        # Decisión: el camino «ya otorgado» reintenta `aplicar_pendientes`
        # (idempotente) para completar una aplicación que había fallado.
        siguiente = self._cuota(self.alumno, self.ancla + 28 * UN_DIA)
        with patch.object(premios, "aplicar_pendientes", side_effect=RuntimeError("boom")):
            with self.assertLogs("reconocimientos.premios", level="ERROR"):
                primero, _ = self._otorgar()
        segundo, motivo = self._otorgar()
        self.assertEqual((segundo, motivo), (primero, premios.MOTIVO_YA_OTORGADO))
        self.assertEqual(Novedad.objects.count(), 1)
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, Decimal("2000.00"))

    def test_anulado_se_puede_volver_a_dar(self):
        primero, _ = self._otorgar()
        self.assertTrue(premios.anular(self.gimnasio, primero.pk))
        segundo, _ = self._otorgar()
        self.assertIsNotNone(segundo)
        self.assertNotEqual(segundo.pk, primero.pk)


# ---------------------------------------------------------------------------
# aplicar_pendientes
# ---------------------------------------------------------------------------


class AplicarPendientesTests(_Base):
    def setUp(self):
        super().setUp()
        self.fin = self.ancla + 27 * UN_DIA
        self.sig = self.ancla + 28 * UN_DIA  # inicio del ciclo siguiente

    def _ciclo_n(self, n):
        """Inicio del n-ésimo ciclo después del premiado (1 = el siguiente)."""
        return self.ancla + 28 * n * UN_DIA

    def _descuentos(self, alumno=None):
        return list(
            Cuota.objects.filter(alumno=alumno or self.alumno)
            .order_by("periodo_inicio")
            .values_list("periodo_inicio", "descuento")
        )

    def test_una_cuota(self):
        otorgado = self._otorgado()
        cuota = self._cuota(self.alumno, self.sig)
        self.assertEqual(premios.aplicar_pendientes(), 1)
        cuota.refresh_from_db()
        self.assertEqual(cuota.descuento, Decimal("2000.00"))
        self.assertEqual(cuota.descuento_motivo, "Premio: Un mes 20%")
        aplicacion = PremioAplicacion.objects.get()
        self.assertEqual((aplicacion.otorgado, aplicacion.cuota), (otorgado, cuota))
        self.assertEqual(aplicacion.descuento, Decimal("2000.00"))
        self.assertEqual(cuota.monto_a_cobrar, Decimal("8000.00"))

    def test_no_toca_la_cuota_del_ciclo_premiado_ni_anteriores(self):
        self._otorgado()
        propia = self._cuota(self.alumno, self.ancla)
        vieja = self._cuota(self.alumno, self.ancla - 28 * UN_DIA)
        self.assertEqual(premios.aplicar_pendientes(), 0)
        for cuota in (propia, vieja):
            cuota.refresh_from_db()
            self.assertEqual(cuota.descuento, 0)

    def test_tres_cuotas_en_orden(self):
        self._otorgado(cuotas=3)
        for n in (4, 2, 1, 3):  # creadas desordenadas
            self._cuota(self.alumno, self._ciclo_n(n))
        self.assertEqual(premios.aplicar_pendientes(), 3)
        self.assertEqual(
            self._descuentos(),
            [(self._ciclo_n(1), Decimal("2000.00")), (self._ciclo_n(2), Decimal("2000.00")),
             (self._ciclo_n(3), Decimal("2000.00")), (self._ciclo_n(4), Decimal("0.00"))],
        )

    def test_trece_cuotas_apareciendo_de_a_una(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(precio_cuota=Decimal("10000"))
        otorgado = self._otorgado(cuotas=13)
        # Las cuotas hasta el ciclo premiado ya están pagas.
        self._cuota(self.alumno, self.ancla, estado=Cuota.Estado.PAGADO)
        total = 0
        for n in range(1, 16):
            dia = self._ciclo_n(n) + 2 * UN_DIA
            generar_pagos_pendientes(dia)
            total += premios.aplicar_pendientes()
            # El alumno paga lo que se le emitió.
            Cuota.objects.filter(
                alumno=self.alumno, periodo_inicio__lte=self._ciclo_n(n)
            ).update(estado=Cuota.Estado.PAGADO)
        self.assertEqual(total, 13)
        con_descuento = Cuota.objects.filter(alumno=self.alumno, descuento__gt=0)
        self.assertEqual(con_descuento.count(), 13)
        self.assertEqual(
            sorted(con_descuento.values_list("periodo_inicio", flat=True)),
            [self._ciclo_n(n) for n in range(1, 14)],
        )
        self.assertEqual(PremioAplicacion.objects.filter(otorgado=otorgado).count(), 13)
        self.assertEqual(premios.aplicar_pendientes(), 0)

    def test_idempotente(self):
        self._otorgado(cuotas=2)
        self._cuota(self.alumno, self._ciclo_n(1))
        self.assertEqual(premios.aplicar_pendientes(), 1)
        self.assertEqual(premios.aplicar_pendientes(), 0)
        self.assertEqual(PremioAplicacion.objects.count(), 1)
        self._cuota(self.alumno, self._ciclo_n(2))
        self.assertEqual(premios.aplicar_pendientes(), 1)
        self.assertEqual(premios.aplicar_pendientes(), 0)
        self._cuota(self.alumno, self._ciclo_n(3))
        self.assertEqual(premios.aplicar_pendientes(), 0)  # ya usó sus 2
        self.assertEqual(PremioAplicacion.objects.count(), 2)

    def test_salta_pagada_anulada_descuento_manual_y_monto_cero(self):
        self._otorgado(cuotas=3)
        self._cuota(self.alumno, self._ciclo_n(1))
        self._cuota(self.alumno, self._ciclo_n(2), estado=Cuota.Estado.PAGADO)
        self._cuota(self.alumno, self._ciclo_n(3), estado=Cuota.Estado.ANULADO)
        self._cuota(self.alumno, self._ciclo_n(4), descuento=Decimal("500"), motivo="Beca")
        self._cuota(self.alumno, self._ciclo_n(5), monto=Decimal("0"))
        self._cuota(self.alumno, self._ciclo_n(6), estado=Cuota.Estado.VENCIDO)
        self._cuota(self.alumno, self._ciclo_n(7))
        self._cuota(self.alumno, self._ciclo_n(8))
        self.assertEqual(premios.aplicar_pendientes(), 3)
        self.assertEqual(
            self._descuentos(),
            [(self._ciclo_n(1), Decimal("2000.00")), (self._ciclo_n(2), Decimal("0.00")),
             (self._ciclo_n(3), Decimal("0.00")), (self._ciclo_n(4), Decimal("500.00")),
             (self._ciclo_n(5), Decimal("0.00")), (self._ciclo_n(6), Decimal("2000.00")),
             (self._ciclo_n(7), Decimal("2000.00")), (self._ciclo_n(8), Decimal("0.00"))],
        )
        self.assertEqual(
            Cuota.objects.get(periodo_inicio=self._ciclo_n(4)).descuento_motivo, "Beca"
        )

    def test_fijo_se_recorta_al_tope(self):
        self._otorgado(tipo=Premio.Tipo.FIJO, valor=Decimal("5000"), cuotas=2)
        chica = self._cuota(self.alumno, self._ciclo_n(1), monto=Decimal("1000.05"))
        grande = self._cuota(self.alumno, self._ciclo_n(2), monto=Decimal("20000"))
        self.assertEqual(premios.aplicar_pendientes(), 2)
        chica.refresh_from_db()
        grande.refresh_from_db()
        self.assertEqual(chica.descuento, Decimal("900.04"))  # 90% hacia abajo
        self.assertEqual(grande.descuento, Decimal("5000.00"))
        self.assertEqual(
            PremioAplicacion.objects.get(cuota=chica).descuento, Decimal("900.04")
        )

    def test_fijo_en_cuota_de_un_centavo_se_saltea(self):
        # El 90% de un centavo redondeado hacia abajo es 0: no hay descuento posible.
        self._otorgado(tipo=Premio.Tipo.FIJO, valor=Decimal("5000"))
        self._cuota(self.alumno, self._ciclo_n(1), monto=Decimal("0.01"))
        siguiente = self._cuota(self.alumno, self._ciclo_n(2))
        self.assertEqual(premios.aplicar_pendientes(), 1)
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.descuento, Decimal("5000.00"))

    def test_porcentaje_redondea_hacia_abajo(self):
        self._otorgado(valor=Decimal("15"))
        cuota = self._cuota(self.alumno, self._ciclo_n(1), monto=Decimal("333.33"))
        premios.aplicar_pendientes()
        cuota.refresh_from_db()
        self.assertEqual(cuota.descuento, Decimal("49.99"))  # 49,9995

    def test_motivo_truncado_a_80(self):
        nombre = "N" * 80
        self._otorgado(nombre=nombre)
        cuota = self._cuota(self.alumno, self._ciclo_n(1))
        premios.aplicar_pendientes()
        cuota.refresh_from_db()
        self.assertEqual(len(cuota.descuento_motivo), 80)
        self.assertTrue(cuota.descuento_motivo.startswith("Premio: NNN"))

    def test_link_de_mp_vivo_se_cancela_y_el_neto_es_correcto(self):
        self._otorgado()
        cuota = self._cuota(self.alumno, self._ciclo_n(1))
        link = CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=cuota, monto=cuota.monto
        )
        premios.aplicar_pendientes()
        link.refresh_from_db()
        self.assertEqual(link.estado, CobroOnlineCuota.Estado.CANCELADO)
        neto = Cuota.objects.annotate(neto=Cuota.NETO).get(pk=cuota.pk).neto
        self.assertEqual(neto, Decimal("8000.00"))

    def test_dos_otorgados_del_mismo_alumno_toman_cuotas_distintas_en_orden(self):
        viejo = self._otorgado(inicio=self.ancla - 28 * UN_DIA, cuotas=2, nombre="Viejo")
        nuevo = self._otorgado(nombre="Nuevo", valor=Decimal("10"))
        for n in (1, 2, 3, 4):
            self._cuota(self.alumno, self._ciclo_n(n))
        self.assertEqual(premios.aplicar_pendientes(), 3)
        por_cuota = dict(
            PremioAplicacion.objects.values_list("cuota__periodo_inicio", "otorgado_id")
        )
        self.assertEqual(
            por_cuota,
            {self._ciclo_n(1): viejo.pk, self._ciclo_n(2): viejo.pk,
             self._ciclo_n(3): nuevo.pk},
        )

    def test_el_mas_nuevo_no_toma_cuotas_anteriores_a_su_ciclo(self):
        # El viejo (ciclo -1) puede ir a la cuota del ciclo premiado del nuevo;
        # el nuevo sólo a las posteriores a SU fin.
        viejo = self._otorgado(inicio=self.ancla - 28 * UN_DIA, nombre="Viejo")
        nuevo = self._otorgado(nombre="Nuevo")
        self._cuota(self.alumno, self.ancla)
        self._cuota(self.alumno, self._ciclo_n(1))
        self.assertEqual(premios.aplicar_pendientes(), 2)
        por_cuota = dict(
            PremioAplicacion.objects.values_list("cuota__periodo_inicio", "otorgado_id")
        )
        self.assertEqual(por_cuota, {self.ancla: viejo.pk, self._ciclo_n(1): nuevo.pk})

    def test_anulado_no_aplica(self):
        otorgado = self._otorgado(cuotas=2)
        self._cuota(self.alumno, self._ciclo_n(1))
        premios.aplicar_pendientes()
        self.assertTrue(premios.anular(self.gimnasio, otorgado.pk))
        self._cuota(self.alumno, self._ciclo_n(2))
        self.assertEqual(premios.aplicar_pendientes(), 0)
        # Lo ya aplicado queda.
        self.assertEqual(
            self._descuentos(),
            [(self._ciclo_n(1), Decimal("2000.00")), (self._ciclo_n(2), Decimal("0.00"))],
        )

    def test_obsequio_no_aplica(self):
        self._otorgado(tipo=Premio.Tipo.OBSEQUIO, valor=None, texto="Remera")
        self._cuota(self.alumno, self._ciclo_n(1))
        self.assertEqual(premios.aplicar_pendientes(), 0)

    def test_bandera_apagada_igual_aplica(self):
        self._otorgado()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(reconocimientos_habilitado=False)
        cuota = self._cuota(self.alumno, self._ciclo_n(1))
        self.assertEqual(premios.aplicar_pendientes(), 1)
        cuota.refresh_from_db()
        self.assertEqual(cuota.descuento, Decimal("2000.00"))

    def test_alumno_dado_de_baja_no_recibe(self):
        self._otorgado()
        cuota = self._cuota(self.alumno, self._ciclo_n(1))
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save()
        self.assertEqual(premios.aplicar_pendientes(), 0)
        cuota.refresh_from_db()
        self.assertEqual(cuota.descuento, 0)

    def test_alumno_ids_acota(self):
        self._otorgado()
        otra = self._alumno("Otra")
        self._otorgado(otra)
        self._cuota(self.alumno, self._ciclo_n(1))
        cuota_otra = self._cuota(otra, self._ciclo_n(1))
        self.assertEqual(premios.aplicar_pendientes(alumno_ids=[self.alumno.pk]), 1)
        cuota_otra.refresh_from_db()
        self.assertEqual(cuota_otra.descuento, 0)

    def test_no_pisa_un_descuento_manual_cargado_en_el_medio(self):
        # El staff carga un descuento a mano entre la lectura de las cuotas y
        # el lock: `solo_si_sin_descuento` lo respeta y el premio sigue a la
        # siguiente cuota.
        self._otorgado()
        primera = self._cuota(self.alumno, self._ciclo_n(1))
        segunda = self._cuota(self.alumno, self._ciclo_n(2))
        llamadas = []

        def staff_se_adelanta(cuota_pk, *args, **kwargs):
            if not llamadas:
                Cuota.objects.filter(pk=cuota_pk).update(
                    descuento=Decimal("500"), descuento_motivo="Beca"
                )
            llamadas.append(cuota_pk)
            return aplicar_descuento_real(cuota_pk, *args, **kwargs)

        with patch.object(premios, "aplicar_descuento", side_effect=staff_se_adelanta):
            self.assertEqual(premios.aplicar_pendientes(), 1)
        primera.refresh_from_db()
        segunda.refresh_from_db()
        self.assertEqual((primera.descuento, primera.descuento_motivo),
                         (Decimal("500.00"), "Beca"))
        self.assertEqual(segunda.descuento, Decimal("2000.00"))
        self.assertEqual(PremioAplicacion.objects.get().cuota, segunda)

    def test_si_otra_corrida_ya_tomo_la_cuota_se_deshace_el_descuento(self):
        # Otra corrida registró la aplicación sobre esta cuota en el medio: el
        # INSERT choca con la unicidad y el descuento recién escrito se deshace.
        otro = self._otorgado(inicio=self.ancla - 28 * UN_DIA, nombre="Otro")
        PremioOtorgado.objects.filter(pk=otro.pk).update(estado=PremioOtorgado.Estado.ANULADO)
        self._otorgado()
        cuota = self._cuota(self.alumno, self._ciclo_n(1))

        def otra_corrida(cuota_pk, *args, **kwargs):
            PremioAplicacion.objects.create(otorgado=otro, cuota_id=cuota_pk,
                                            descuento=Decimal("1"))
            return aplicar_descuento_real(cuota_pk, *args, **kwargs)

        with patch.object(premios, "aplicar_descuento", side_effect=otra_corrida):
            self.assertEqual(premios.aplicar_pendientes(), 0)
        cuota.refresh_from_db()
        self.assertEqual((cuota.descuento, cuota.descuento_motivo), (Decimal("0.00"), ""))

    def test_no_se_pasa_de_sus_cuotas_si_otra_corrida_ya_aplico(self):
        # Otra corrida aplicó la única cuota del premio entre la lectura y el
        # lock del otorgado: esta corrida no le da una segunda.
        otorgado = self._otorgado()
        primera = self._cuota(self.alumno, self._ciclo_n(1))
        segunda = self._cuota(self.alumno, self._ciclo_n(2))
        original = premios._aplicar_una

        def otra_corrida_gana(otorgado_id, *args, **kwargs):
            if not PremioAplicacion.objects.exists():
                Cuota.objects.filter(pk=primera.pk).update(descuento=Decimal("2000"))
                PremioAplicacion.objects.create(
                    otorgado_id=otorgado_id, cuota=primera, descuento=Decimal("2000")
                )
            return original(otorgado_id, *args, **kwargs)

        with patch.object(premios, "_aplicar_una", side_effect=otra_corrida_gana):
            self.assertEqual(premios.aplicar_pendientes(), 0)
        segunda.refresh_from_db()
        self.assertEqual(segunda.descuento, 0)
        self.assertEqual(PremioAplicacion.objects.filter(otorgado=otorgado).count(), 1)

    def test_una_asignacion_que_revienta_no_frena_las_demas(self):
        otra = self._alumno("Otra")
        self._otorgado()
        self._otorgado(otra)
        mia = self._cuota(self.alumno, self._ciclo_n(1))
        suya = self._cuota(otra, self._ciclo_n(1))
        original = premios._aplicar_una
        llamadas = []

        def revienta_la_primera(*args, **kwargs):
            llamadas.append(args)
            if len(llamadas) == 1:
                raise RuntimeError("boom")
            return original(*args, **kwargs)

        with patch.object(premios, "_aplicar_una", side_effect=revienta_la_primera):
            with self.assertLogs("reconocimientos.premios", level="ERROR") as logs:
                self.assertEqual(premios.aplicar_pendientes(), 1)
        self.assertIn("boom", "\n".join(logs.output))
        mia.refresh_from_db()
        suya.refresh_from_db()
        self.assertEqual(
            sorted([mia.descuento, suya.descuento]), [Decimal("0.00"), Decimal("2000.00")]
        )
        self.assertEqual(PremioAplicacion.objects.count(), 1)
        # La siguiente corrida completa la que falló.
        self.assertEqual(premios.aplicar_pendientes(), 1)

    def test_lecturas_constantes_con_3_y_con_40_otorgados(self):
        medidas = []
        for cantidad in (3, 40):
            for i in range(cantidad):
                alumno = self._alumno(f"N{cantidad}-{i}")
                self._otorgado(alumno, cuotas=3)
                # Cuotas que no corresponden: pagadas o del ciclo premiado.
                self._cuota(alumno, self._ciclo_n(1), estado=Cuota.Estado.PAGADO)
                self._cuota(alumno, self.ancla)
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(premios.aplicar_pendientes(), 0)
            medidas.append(len(ctx.captured_queries))
        self.assertEqual(medidas, [2, 2])


# ---------------------------------------------------------------------------
# anular / marcar entregado
# ---------------------------------------------------------------------------


class AnularYEntregarTests(_Base):
    def test_anular(self):
        otorgado = self._otorgado()
        self.assertFalse(premios.anular(self.otro, otorgado.pk))
        self.assertTrue(premios.anular(self.gimnasio, otorgado.pk))
        otorgado.refresh_from_db()
        self.assertEqual(otorgado.estado, PremioOtorgado.Estado.ANULADO)
        self.assertFalse(premios.anular(self.gimnasio, otorgado.pk))

    def test_marcar_entregado(self):
        otorgado = self._otorgado(tipo=Premio.Tipo.OBSEQUIO, valor=None, texto="Remera")
        self.assertFalse(premios.marcar_entregado(self.otro, otorgado.pk))
        self.assertTrue(premios.marcar_entregado(self.gimnasio, otorgado.pk))
        otorgado.refresh_from_db()
        self.assertIsNotNone(otorgado.entregado_en)
        primera = otorgado.entregado_en
        self.assertFalse(premios.marcar_entregado(self.gimnasio, otorgado.pk))
        otorgado.refresh_from_db()
        self.assertEqual(otorgado.entregado_en, primera)

    def test_no_se_entrega_un_anulado_ni_un_descuento(self):
        anulado = self._otorgado(tipo=Premio.Tipo.OBSEQUIO, valor=None, texto="R",
                                 estado=PremioOtorgado.Estado.ANULADO)
        descuento = self._otorgado(inicio=self.ancla - 28 * UN_DIA)
        self.assertFalse(premios.marcar_entregado(self.gimnasio, anulado.pk))
        self.assertFalse(premios.marcar_entregado(self.gimnasio, descuento.pk))


# ---------------------------------------------------------------------------
# el cron `generar_pagos`
# ---------------------------------------------------------------------------


class ComandoGenerarPagosTests(_Base):
    def _correr(self):
        salida = StringIO()
        call_command("generar_pagos", stdout=salida)
        return salida.getvalue()

    def test_misma_salida_de_siempre_si_no_aplica_nada(self):
        self.assertEqual(
            self._correr(),
            f"Cuotas emitidas al {HOY:%d/%m/%Y}: 1. Cuotas marcadas como vencidas: 0.\n",
        )

    def test_aplica_el_premio_a_la_cuota_recien_emitida(self):
        # Ancla de modo que hoy arranque el ciclo siguiente al premiado.
        ancla = HOY - 28 * UN_DIA
        alumno = self._alumno("Bea", ancla=ancla)
        self._otorgado(alumno, inicio=ancla)
        salida = self._correr()
        cuota = Cuota.objects.get(alumno=alumno, periodo_inicio=HOY)
        self.assertEqual(cuota.descuento, Decimal("0.00"))  # monto 0: se saltea
        self.assertNotIn("premio", salida.lower())
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(precio_cuota=Decimal("10000"))
        Cuota.objects.filter(pk=cuota.pk).update(monto=Decimal("10000"))
        salida = self._correr()
        cuota.refresh_from_db()
        self.assertEqual(cuota.descuento, Decimal("2000.00"))
        self.assertIn("Descuentos de premios aplicados: 1.", salida)
        self.assertTrue(salida.startswith(f"Cuotas emitidas al {HOY:%d/%m/%Y}: "))

    def test_no_falla_si_premios_revienta_y_deja_el_log(self):
        with patch("reconocimientos.premios.aplicar_pendientes",
                   side_effect=RuntimeError("boom")):
            with self.assertLogs("pagos.management.commands.generar_pagos",
                                 level="ERROR") as logs:
                salida = self._correr()
        self.assertEqual(
            salida,
            f"Cuotas emitidas al {HOY:%d/%m/%Y}: 1. Cuotas marcadas como vencidas: 0.\n",
        )
        self.assertTrue(Cuota.objects.filter(alumno=self.alumno).exists())
        self.assertIn("boom", "\n".join(logs.output))
