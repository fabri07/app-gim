"""Pieza 5, tarea 4: pantalla «Premios del ciclo» (staff).

El link «Premios» de la nav (sólo con bandera) ya lo fija
`tests_premios_catalogo`; acá se prueba que la pantalla nueva cuelga de él.
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from asistencia.models import Asistencia
from novedades.models import Novedad
from pagos.models import Cuota
from reconocimientos.models import Premio, PremioAplicacion, PremioOtorgado
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS

HOY = date(2031, 6, 18)
UN_DIA = timedelta(days=1)
CLAVE = "clave-123456"
_serie = count(1)


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
        self.ancla = HOY - timedelta(days=30)
        self.staff = self._usuario(self.gimnasio, Perfil.Rol.STAFF)
        self.url = reverse("reconocimientos:premios_ciclo")
        self.client.login(username=self.staff.username, password=CLAVE)

    def _usuario(self, gimnasio, rol):
        usuario = User.objects.create_user(f"u{next(_serie)}", password=CLAVE)
        Perfil.objects.create(
            usuario=usuario, gimnasio=gimnasio, rol=rol,
            terminos_version=VERSION_TERMINOS,
        )
        return usuario

    def _alumno(self, nombre="Ana", gimnasio=None, perfecto=True, **extra):
        gimnasio = gimnasio or self.gimnasio
        datos = dict(
            gimnasio=gimnasio, nombre=nombre, apellido="Pérez",
            estado=Alumno.Estado.ACTIVO, fecha_nacimiento=date(1990, 1, 1),
            fecha_inicio_ciclo=self.ancla,
        )
        datos.update(extra)
        alumno = Alumno.objects.create(**datos)
        if perfecto:
            rutina = RutinaAsignada.objects.create(
                gimnasio=gimnasio, alumno=alumno, nombre_snapshot="R",
                objetivo_snapshot="O", fecha_inicio=self.ancla,
            )
            for orden in range(1, 5):
                RutinaAsignadaItem.objects.create(
                    rutina_asignada=rutina, ejercicio_nombre_snapshot="E",
                    dia=orden, semana=1, orden=orden, series=3, repeticiones="10",
                )
            for i in range(4):
                Asistencia.objects.create(
                    gimnasio=gimnasio, alumno=alumno, fecha=self.ancla + i * UN_DIA,
                    origen=Asistencia.Origen.MANUAL,
                )
        return alumno

    def _premio(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, nombre="Un mes 20%",
            tipo=Premio.Tipo.PORCENTAJE, valor=Decimal("20"), cuotas=1,
        )
        datos.update(extra)
        return Premio.objects.create(**datos)

    def _otorgado(self, alumno, **extra):
        datos = dict(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre="Un mes 20%",
            tipo=Premio.Tipo.PORCENTAJE, valor=Decimal("20"), cuotas=2,
            periodo_inicio=self.ancla, periodo_fin=self.ancla + 27 * UN_DIA,
            meta=4, asistidas=4,
        )
        datos.update(extra)
        return PremioOtorgado.objects.create(**datos)

    def _dar(self, alumno, premio, inicio=None, cambios=None):
        datos = {
            "accion": "dar", "alumno": alumno.pk, "premio": premio.pk,
            "inicio": (inicio or self.ancla).isoformat(),
        }
        datos.update(cambios or {})
        return self.client.post(self.url, datos)

    def _mensajes(self, respuesta):
        return [str(m) for m in get_messages(respuesta.wsgi_request)]


class AccesoTests(_Base):
    def test_staff_ve_la_pantalla(self):
        self._premio()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_alumno_403(self):
        alumno = self._usuario(self.gimnasio, Perfil.Rol.ALUMNO)
        self.client.login(username=alumno.username, password=CLAVE)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"accion": "dar"}).status_code, 403)

    def test_anonimo_va_al_login(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_sin_bandera_404_en_get_y_post(self):
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"accion": "dar"}).status_code, 404)

    def test_el_catalogo_enlaza_a_la_pantalla_del_ciclo(self):
        r = self.client.get(reverse("reconocimientos:premios"))
        self.assertContains(r, self.url)


class ListaTests(_Base):
    def test_muestra_candidatos_y_selector_con_el_por_defecto(self):
        alumno = self._alumno("Ana")
        self._premio(nombre="Común")
        defecto = self._premio(nombre="El elegido", por_defecto=True)
        r = self.client.get(self.url)
        self.assertContains(r, "Ana")
        self.assertContains(r, "Dar premio")
        self.assertContains(r, f'value="{defecto.pk}" selected')
        self.assertEqual(len(r.context["candidatos"]), 1)
        self.assertEqual(r.context["candidatos"][0].alumno_id, alumno.pk)

    def test_por_defecto_inactivo_no_se_preselecciona_ni_se_lista(self):
        self._alumno()
        self._premio(nombre="Activo")
        inactivo = self._premio(nombre="Viejo", por_defecto=True, activo=False)
        r = self.client.get(self.url)
        self.assertNotContains(r, f'value="{inactivo.pk}"')
        self.assertNotContains(r, " selected")

    def test_selector_no_lista_premios_de_otro_gimnasio(self):
        self._alumno()
        self._premio()
        ajeno = self._premio(gimnasio=self.otro, nombre="AJENO")
        self.assertNotContains(self.client.get(self.url), ajeno.nombre)

    def test_sin_premios_activos_cartel_que_lleva_al_catalogo(self):
        self._alumno()
        self._premio(activo=False)
        r = self.client.get(self.url)
        self.assertContains(r, reverse("reconocimientos:premio_nuevo"))
        self.assertNotContains(r, "Dar premio")

    def test_menor_y_edad_sin_cargar_se_ven_rotulados(self):
        self._alumno("Menor", fecha_nacimiento=HOY - timedelta(days=365 * 10))
        self._alumno("Sinfecha", fecha_nacimiento=None)
        self._premio()
        r = self.client.get(self.url)
        self.assertContains(r, "Menor de 18")
        self.assertContains(r, "Edad sin cargar")

    def test_menor_se_premia(self):
        menor = self._alumno("Menor", fecha_nacimiento=HOY - timedelta(days=365 * 10))
        self._dar(menor, self._premio())
        self.assertTrue(PremioOtorgado.objects.filter(alumno=menor).exists())

    def test_otorgados_recientes_con_estado_y_cuotas_aplicadas(self):
        alumno = self._alumno("Ana", perfecto=False)
        otorgado = self._otorgado(alumno, nombre="Descuento visible")
        cuota = Cuota.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, periodo_inicio=HOY,
            periodo_fin=HOY + 27 * UN_DIA, monto=Decimal("100"),
        )
        PremioAplicacion.objects.create(otorgado=otorgado, cuota=cuota, descuento=Decimal("20"))
        r = self.client.get(self.url)
        self.assertContains(r, "Descuento visible")
        self.assertContains(r, "1 de 2 cuotas")

    def test_otorgados_viejos_no_aparecen(self):
        alumno = self._alumno("Ana", perfecto=False)
        viejo = HOY - timedelta(days=200)
        self._otorgado(alumno, nombre="Muy viejo", periodo_inicio=viejo,
                       periodo_fin=viejo + 27 * UN_DIA)
        self.assertNotContains(self.client.get(self.url), "Muy viejo")

    def test_otorgados_de_otro_gimnasio_no_aparecen(self):
        ajeno = self._alumno("Ajena", gimnasio=self.otro, perfecto=False)
        self._otorgado(ajeno, nombre="Del otro")
        self.assertNotContains(self.client.get(self.url), "Del otro")

    def test_obsequio_tiene_boton_entregado(self):
        alumno = self._alumno(perfecto=False)
        self._otorgado(alumno, tipo=Premio.Tipo.OBSEQUIO, valor=None, cuotas=1, texto="Remera")
        self.assertContains(self.client.get(self.url), "Marcar entregado")


class DarPremioTests(_Base):
    def test_dar_crea_y_avisa(self):
        alumno = self._alumno("Ana")
        r = self._dar(alumno, self._premio())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PremioOtorgado.objects.filter(alumno=alumno).count(), 1)
        self.assertTrue(any("Premio dado a Ana" in m for m in self._mensajes(r)))
        self.assertTrue(Novedad.objects.filter(alumno=alumno).exists())

    def test_doble_clic_es_idempotente(self):
        alumno = self._alumno()
        premio = self._premio()
        self._dar(alumno, premio)
        r = self._dar(alumno, premio)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PremioOtorgado.objects.count(), 1)
        self.assertEqual(Novedad.objects.filter(alumno=alumno).count(), 1)

    def _forjado(self, r):
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PremioOtorgado.objects.count(), 0)
        self.assertEqual(Novedad.objects.count(), 0)
        self.assertTrue(self._mensajes(r))

    def test_premio_ajeno(self):
        self._forjado(self._dar(self._alumno(), self._premio(gimnasio=self.otro)))

    def test_premio_inactivo(self):
        self._forjado(self._dar(self._alumno(), self._premio(activo=False)))

    def test_alumno_no_elegible(self):
        self._forjado(self._dar(self._alumno(perfecto=False), self._premio()))

    def test_alumno_de_otro_gimnasio(self):
        ajeno = self._alumno(gimnasio=self.otro)
        self._forjado(self._dar(ajeno, self._premio()))

    def test_ciclo_fuera_de_ventana(self):
        viejo = self.ancla - 28 * 10 * UN_DIA
        self._forjado(self._dar(self._alumno(), self._premio(), inicio=viejo))

    def test_campos_invalidos(self):
        alumno = self._alumno()
        premio = self._premio()
        for extra in (
            {"inicio": "basura"}, {"inicio": ""}, {"alumno": "x"}, {"premio": "x"},
            {"alumno": ""}, {"premio": ""},
        ):
            with self.subTest(extra=extra):
                self._forjado(self._dar(alumno, premio, cambios=extra))

    def test_accion_desconocida_o_ausente(self):
        for datos in ({"accion": "borrar"}, {}):
            with self.subTest(datos=datos):
                r = self.client.post(self.url, datos)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(self._mensajes(r))
        self.assertEqual(PremioOtorgado.objects.count(), 0)


class AnularYEntregarTests(_Base):
    def setUp(self):
        super().setUp()
        self.alumno = self._alumno(perfecto=False)

    def test_anular(self):
        otorgado = self._otorgado(self.alumno)
        r = self.client.post(self.url, {"accion": "anular", "otorgado": otorgado.pk})
        self.assertEqual(r.status_code, 302)
        otorgado.refresh_from_db()
        self.assertEqual(otorgado.estado, PremioOtorgado.Estado.ANULADO)
        self.assertTrue(self._mensajes(r))

    def test_entregado(self):
        otorgado = self._otorgado(
            self.alumno, tipo=Premio.Tipo.OBSEQUIO, valor=None, cuotas=1, texto="Remera"
        )
        r = self.client.post(self.url, {"accion": "entregado", "otorgado": otorgado.pk})
        self.assertEqual(r.status_code, 302)
        otorgado.refresh_from_db()
        self.assertIsNotNone(otorgado.entregado_en)

    def test_de_otro_gimnasio_no_toca_nada(self):
        ajeno = self._alumno(gimnasio=self.otro, perfecto=False)
        otorgado = self._otorgado(ajeno, tipo=Premio.Tipo.OBSEQUIO, valor=None, cuotas=1, texto="x")
        for accion in ("anular", "entregado"):
            r = self.client.post(self.url, {"accion": accion, "otorgado": otorgado.pk})
            self.assertEqual(r.status_code, 302)
            self.assertTrue(self._mensajes(r))
        otorgado.refresh_from_db()
        self.assertEqual(otorgado.estado, PremioOtorgado.Estado.OTORGADO)
        self.assertIsNone(otorgado.entregado_en)

    def test_id_no_numerico_o_inexistente(self):
        for valor in ("x", "", "99999"):
            for accion in ("anular", "entregado"):
                r = self.client.post(self.url, {"accion": accion, "otorgado": valor})
                self.assertEqual(r.status_code, 302)
                self.assertTrue(self._mensajes(r))


class QueriesTests(_Base):
    def _medir(self):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        return len(ctx.captured_queries)

    def test_queries_constantes_con_5_y_con_60(self):
        self._premio()
        self._medir()  # calienta cachés de la primera request (sesión, etc.)
        medidas = []
        for cantidad in (5, 60):
            for i in range(cantidad):
                alumno = self._alumno(f"N{cantidad}-{i}")
                if i % 2:
                    self._otorgado(alumno, periodo_inicio=self.ancla - 28 * UN_DIA,
                                   periodo_fin=self.ancla - UN_DIA)
            medidas.append(self._medir())
        self.assertEqual(medidas[0], medidas[1])
