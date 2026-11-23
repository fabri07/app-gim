"""Pieza 5, tarea 5: lado del alumno (avance, premios y cuadro de honor)."""

from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from reconocimientos import honor
from reconocimientos.models import Premio, PremioAplicacion, PremioOtorgado
from reconocimientos.ranking import participantes
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
        self.ancla = HOY - 10 * UN_DIA
        self.yo = self._alumno("Yo", apellido="Mío", con_rutina=True)
        self.client.login(username=self.yo.perfil.usuario.username, password=CLAVE)

    def _alumno(self, nombre="Ana", apellido="Pérez", gimnasio=None, terminos=VERSION_TERMINOS,
                con_rutina=False, **extra):
        gimnasio = gimnasio or self.gimnasio
        usuario = User.objects.create_user(f"u{next(_serie)}", password=CLAVE)
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO,
            terminos_version=terminos,
        )
        datos = dict(
            gimnasio=gimnasio, nombre=nombre, apellido=apellido, perfil=perfil,
            estado=Alumno.Estado.ACTIVO, fecha_nacimiento=date(1990, 1, 1),
            fecha_inicio_ciclo=self.ancla,
        )
        datos.update(extra)
        alumno = Alumno.objects.create(**datos)
        if con_rutina:
            self._rutina(alumno)
        return alumno

    def _rutina(self, alumno):
        rutina = RutinaAsignada.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre_snapshot="R",
            objetivo_snapshot="O", fecha_inicio=self.ancla,
        )
        for orden in range(1, 5):
            RutinaAsignadaItem.objects.create(
                rutina_asignada=rutina, ejercicio_nombre_snapshot="E",
                dia=orden, semana=1, orden=orden, series=3, repeticiones="10",
            )

    def _otorgado(self, alumno, **extra):
        datos = dict(
            gimnasio=alumno.gimnasio, alumno=alumno, nombre="Un mes 20%",
            tipo=Premio.Tipo.PORCENTAJE, valor=Decimal("20"), cuotas=2,
            periodo_inicio=HOY - 40 * UN_DIA, periodo_fin=HOY - 12 * UN_DIA,
            meta=4, asistidas=4, da_insignia=True, insignia_hasta=HOY + 5 * UN_DIA,
        )
        datos.update(extra)
        return PremioOtorgado.objects.create(**datos)

    def _entrar(self, alumno):
        self.client.logout()
        self.client.login(username=alumno.perfil.usuario.username, password=CLAVE)


class MiPerfilTests(_Base):
    url = reverse("alumnos:mi_perfil")

    def test_avance_del_ciclo(self):
        for i in range(2):
            Asistencia.objects.create(
                gimnasio=self.gimnasio, alumno=self.yo, fecha=self.ancla + i * UN_DIA,
                origen=Asistencia.Origen.MANUAL,
            )
        r = self.client.get(self.url)
        self.assertContains(r, "Tu asistencia")
        self.assertContains(r, "2 de 4 sesiones")
        self.assertContains(r, "faltan 2 sesiones")

    def test_sin_meta_no_rompe(self):
        yo = self._alumno("Sin", con_rutina=False)
        self._entrar(yo)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Cuando tengas una rutina")

    def test_sin_ancla_no_rompe(self):
        yo = self._alumno("Sin")
        # El alta le pone un ancla por defecto: se la sacamos con un UPDATE.
        Alumno.objects.filter(pk=yo.pk).update(fecha_inicio_ciclo=None)
        self._entrar(yo)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Todavía no hay un ciclo en marcha")

    def test_premios_en_sus_tres_estados(self):
        self._otorgado(self.yo, nombre="Premio sin usar", periodo_inicio=HOY - 90 * UN_DIA)
        usado = self._otorgado(
            self.yo, nombre="Premio usado", periodo_inicio=HOY - 60 * UN_DIA, cuotas=3
        )
        cuota = self._cuota(self.yo)
        PremioAplicacion.objects.create(otorgado=usado, cuota=cuota, descuento=Decimal("10"))
        self._otorgado(
            self.yo, nombre="Una remera", tipo=Premio.Tipo.OBSEQUIO, valor=None,
            cuotas=1, texto="Remera talle M", periodo_inicio=HOY - 120 * UN_DIA,
        )
        self._otorgado(
            self.yo, nombre="Una gorra", tipo=Premio.Tipo.OBSEQUIO, valor=None,
            cuotas=1, periodo_inicio=HOY - 150 * UN_DIA,
            entregado_en=timezone.now(),
        )
        self._otorgado(
            self.yo, nombre="Premio anulado", periodo_inicio=HOY - 180 * UN_DIA,
            estado=PremioOtorgado.Estado.ANULADO,
        )
        r = self.client.get(self.url)
        self.assertContains(r, "Se aplicará en tu próxima cuota")
        self.assertContains(r, "Aplicado en 1 de 3 cuotas")
        self.assertContains(r, "Retiralo en el gimnasio")
        self.assertContains(r, "Remera talle M")
        self.assertContains(r, "Entregado")
        self.assertNotContains(r, "Premio anulado")

    def _cuota(self, alumno):
        from pagos.models import Cuota
        return Cuota.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, monto=Decimal("1000"),
            periodo_inicio=self.ancla, periodo_fin=self.ancla + 27 * UN_DIA,
        )

    def test_insignia_vigente_y_vencida(self):
        self._otorgado(self.yo, insignia_hasta=HOY + UN_DIA)
        self.assertContains(self.client.get(self.url), "Tenés la insignia")
        PremioOtorgado.objects.update(insignia_hasta=HOY - UN_DIA)
        self.assertNotContains(self.client.get(self.url), "Tenés la insignia")

    def test_sin_premios_texto_amable(self):
        self.assertContains(self.client.get(self.url), "Todavía no ganaste premios")

    def test_un_menor_ve_su_avance_y_premios(self):
        menor = self._alumno("Chico", fecha_nacimiento=HOY - 10 * 365 * UN_DIA, con_rutina=True)
        self._otorgado(menor, nombre="Premio del chico")
        self._entrar(menor)
        r = self.client.get(self.url)
        self.assertContains(r, "Tu asistencia")
        self.assertContains(r, "Premio del chico")

    def test_no_ve_premios_de_otros(self):
        otro = self._alumno("Otro")
        self._otorgado(otro, nombre="Premio ajeno")
        self.assertNotContains(self.client.get(self.url), "Premio ajeno")

    def test_sin_bandera_404(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(reconocimientos_habilitado=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_suplantando_ve_lo_mismo_sin_escribir(self):
        self._otorgado(self.yo, nombre="Premio mío")
        staff = User.objects.create_user("staff1", password=CLAVE)
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.logout()
        self.client.login(username="staff1", password=CLAVE)
        self.client.post(reverse("suplantar", args=[self.yo.pk]))
        antes = PremioOtorgado.objects.count(), PremioAplicacion.objects.count()
        r = self.client.get(self.url)
        self.assertContains(r, "Premio mío")
        self.assertContains(r, "Tu asistencia")
        self.assertEqual(
            (PremioOtorgado.objects.count(), PremioAplicacion.objects.count()), antes
        )

    def test_queries_constantes(self):
        self.client.get(self.url)  # calienta
        for n in range(5):
            self._otorgado(self.yo, periodo_inicio=HOY - (100 + n) * UN_DIA)
        with CaptureQueriesContext(connection) as chico:
            self.client.get(self.url)
        for n in range(45):
            self._otorgado(self.yo, periodo_inicio=HOY - (200 + n) * UN_DIA)
        with CaptureQueriesContext(connection) as grande:
            self.client.get(self.url)
        self.assertEqual(len(chico), len(grande))


class CuadroDeHonorTests(_Base):
    url = reverse("reconocimientos:ranking")

    def test_nombre_publico_y_sin_foto(self):
        ana = self._alumno("Lucía", "Martínez")
        self._otorgado(ana)
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), ["Lucía M."])
        r = self.client.get(self.url)
        self.assertContains(r, "Cuadro de honor")
        self.assertContains(r, "Lucía M.")
        self.assertNotContains(r, "Martínez")
        self.assertNotContains(r, "<img")

    def test_sin_nadie_no_hay_seccion(self):
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), [])
        self.assertNotContains(self.client.get(self.url), "Cuadro de honor")

    def test_insignia_vencida_no_sale(self):
        self._otorgado(self._alumno("Vieja"), insignia_hasta=HOY - UN_DIA)
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), [])

    def test_hoy_inclusive(self):
        self._otorgado(self._alumno("Justo"), insignia_hasta=HOY)
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), ["Justo P."])

    def test_sin_insignia_o_anulado_no_sale(self):
        self._otorgado(self._alumno("A"), da_insignia=False)
        self._otorgado(self._alumno("B"), estado=PremioOtorgado.Estado.ANULADO)
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), [])

    def test_sin_duplicados_y_orden_estable(self):
        zeta = self._alumno("Zeta")
        self._otorgado(zeta)
        self._otorgado(zeta, periodo_inicio=HOY - 80 * UN_DIA)
        self._otorgado(self._alumno("Beto"))
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), ["Beto P.", "Zeta P."])

    def test_orden_sin_acentos_ni_mayusculas(self):
        for nombre in ("Zeta", "Álvaro", "ana", "Ana"):
            self._otorgado(self._alumno(nombre))
        self.assertEqual(
            honor.cuadro_de_honor(self.gimnasio),
            ["Álvaro P.", "ana P.", "Ana P.", "Zeta P."],
        )

    def test_privacidad_nadie_invisible_sale(self):
        invisibles = [
            self._alumno("Menor", fecha_nacimiento=HOY - 10 * 365 * UN_DIA),
            self._alumno("SinNac", fecha_nacimiento=None),
            self._alumno("Baja", estado=Alumno.Estado.INACTIVO),
            self._alumno("SinTerm", terminos=0),
            self._alumno("OptOut", aparece_en_ranking=False),
        ]
        for a in invisibles:
            self._otorgado(a)
        self._otorgado(self._alumno("Valido"))
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), ["Valido P."])
        r = self.client.get(self.url)
        for a in invisibles:
            self.assertNotContains(r, a.nombre)

    def test_otro_gimnasio_no_sale(self):
        self._otorgado(self._alumno("Ajeno", gimnasio=self.otro))
        self.assertEqual(honor.cuadro_de_honor(self.gimnasio), [])

    def test_menor_no_ve_la_pantalla_ni_el_cuadro(self):
        self._otorgado(self._alumno("Ana"))
        menor = self._alumno("Chico", fecha_nacimiento=HOY - 10 * 365 * UN_DIA)
        self._entrar(menor)
        r = self.client.get(self.url)
        self.assertContains(r, "mayores de 18 años")
        self.assertNotContains(r, "Cuadro de honor")

    def test_una_query(self):
        self._otorgado(self._alumno("Ana"))
        with CaptureQueriesContext(connection) as c:
            honor.cuadro_de_honor(self.gimnasio)
        self.assertEqual(len(c), 1)

    def test_queries_constantes_de_la_pantalla(self):
        self.client.get(self.url)  # calienta
        for n in range(5):
            self._otorgado(self._alumno(f"N{n}"))
        with CaptureQueriesContext(connection) as chico:
            self.client.get(self.url)
        for n in range(45):
            self._otorgado(self._alumno(f"M{n}"))
        with CaptureQueriesContext(connection) as grande:
            self.client.get(self.url)
        self.assertEqual(len(chico), len(grande))

    def test_sin_bandera_404(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(reconocimientos_habilitado=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)


class ConsistenciaConElRankingTests(_Base):
    def test_las_personas_visibles_son_las_del_ranking(self):
        variantes = [
            self._alumno("Menor", fecha_nacimiento=HOY - 10 * 365 * UN_DIA),
            self._alumno("SinNac", fecha_nacimiento=None),
            self._alumno("Baja", estado=Alumno.Estado.INACTIVO),
            self._alumno("SinTerm", terminos=0),
            self._alumno("TermViejos", terminos=VERSION_TERMINOS - 1),
            self._alumno("OptOut", aparece_en_ranking=False),
            self._alumno("Cumple18Hoy", fecha_nacimiento=date(HOY.year - 18, HOY.month, HOY.day)),
            self._alumno("Casi18", fecha_nacimiento=date(HOY.year - 18, HOY.month, HOY.day) + UN_DIA),
            self._alumno("Valido"),
        ]
        for a in variantes:
            self._rutina(a)
        del_ranking = set(participantes(self.gimnasio).values_list("alumno_id", flat=True))
        visibles = set(honor.alumnos_visibles(self.gimnasio).values_list("pk", flat=True))
        # `self.yo` (adulto válido con rutina) también está en los dos.
        self.assertEqual(visibles, del_ranking)
        self.assertIn(self.yo.pk, visibles)
        self.assertIn(variantes[-1].pk, visibles)
        self.assertNotIn(variantes[0].pk, visibles)
        self.assertNotIn(variantes[1].pk, visibles)
