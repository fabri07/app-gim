"""
Tests de la Fase 1 del panel de plataforma: monitor de solo lectura, redirect
del superusuario sin Perfil y acceso.

Lo que se cubre acá y no en `tests_precios.py` es todo lo que toca el ORM o
una vista: que el conteo de alumnos activos no se contamine entre gimnasios,
que el monitor cueste lo mismo con 2 gimnasios que con 12, y que un dueño de
gimnasio (staff) no pueda entrar al panel de la plataforma.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from plataforma import facturacion
from plataforma.precios import EstadoPago
from tenants.models import Gimnasio, Perfil


def _gimnasio(nombre, **extra):
    return Gimnasio.objects.create(
        nombre=nombre, slug=nombre.lower().replace(" ", "-"), **extra
    )


def _staff(gimnasio, username):
    usuario = User.objects.create_user(username, password="clave-123456")
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return usuario


class InicioEfectivoTests(TestCase):
    """La fecha de alta se lee en hora LOCAL, no en UTC.

    Con `TIME_ZONE = America/Argentina/Buenos_Aires` (UTC-3), un gimnasio dado
    de alta a las 22:00 tiene `creado` fechado al día SIGUIENTE en UTC. Leerlo
    con `.date()` a secas correría un día el fin de la prueba y el
    vencimiento: el gimnasio aparecería como por vencer (o vencido) un día
    antes de lo que le corresponde.
    """

    #: 01:00 UTC del 11/3 == 22:00 del 10/3 en Buenos Aires.
    INSTANTE = datetime(2026, 3, 11, 1, 0, tzinfo=dt_timezone.utc)

    def test_usa_la_fecha_local_del_alta(self):
        with patch("django.utils.timezone.now", return_value=self.INSTANTE):
            gimnasio = _gimnasio("Nocturno")
        gimnasio.refresh_from_db()

        # Guard: sin esto el test pasaría igual con `creado.date()`, porque no
        # se vería que las dos fechas son distintas.
        self.assertEqual(gimnasio.creado.date(), date(2026, 3, 11))
        self.assertEqual(facturacion.inicio_efectivo(gimnasio), date(2026, 3, 10))


class GimnasiosAnotadosTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena")
        self.otro = _gimnasio("Otro Gim")

    def _fila(self, gimnasio):
        return facturacion.gimnasios_anotados().get(pk=gimnasio.pk)

    def test_cuenta_solo_los_alumnos_activos_del_propio_gimnasio(self):
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="Activa")
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Beto", apellido="Activo")
        Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Caro",
            apellido="Inactiva",
            estado=Alumno.Estado.INACTIVO,
        )
        Alumno.objects.create(gimnasio=self.otro, nombre="Ajeno", apellido="Ajeno")

        self.assertEqual(self._fila(self.gimnasio).alumnos_activos, 2)
        self.assertEqual(self._fila(self.otro).alumnos_activos, 1)

    def test_un_gimnasio_sin_alumnos_cuenta_cero_y_no_none(self):
        """Sin el `Coalesce`, el subquery devuelve NULL y la fila rompe al
        calcular el precio (`None` no entra en ninguna comparación)."""
        self.assertEqual(self._fila(self.gimnasio).alumnos_activos, 0)

    def test_el_ultimo_uso_mira_solo_al_staff(self):
        staff = _staff(self.gimnasio, "duenio")
        alumno_usuario = User.objects.create_user("alumno", password="clave-123456")
        Perfil.objects.create(
            usuario=alumno_usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        User.objects.filter(pk=staff.pk).update(
            last_login=datetime(2026, 5, 1, 12, 0, tzinfo=dt_timezone.utc)
        )
        User.objects.filter(pk=alumno_usuario.pk).update(
            last_login=datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc)
        )

        ultimo = self._fila(self.gimnasio).ultimo_uso_staff

        self.assertEqual(ultimo.date(), date(2026, 5, 1))

    def test_el_conteo_de_alumnos_no_multiplica_al_ultimo_uso(self):
        """Dos anotaciones multivaluadas como JOINs se multiplican entre sí
        (el conteo de alumnos saldría × cantidad de perfiles). Por eso las dos
        van como `Subquery`."""
        _staff(self.gimnasio, "duenio")
        _staff(self.gimnasio, "entrenador")
        for i in range(3):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"Alu {i}", apellido="Activo"
            )

        self.assertEqual(self._fila(self.gimnasio).alumnos_activos, 3)


class CostoDelMonitorTests(TestCase):
    """El monitor lista TODOS los gimnasios de la plataforma: es exactamente
    el lugar donde un N+1 se paga en cada carga. El costo tiene que depender
    del catálogo (una query), no de la cantidad de gimnasios ni de alumnos.
    """

    HOY = date(2026, 6, 1)

    @staticmethod
    def _poblar(gimnasios, alumnos_por_gimnasio, prefijo):
        for g in range(gimnasios):
            gimnasio = _gimnasio(f"{prefijo} {g}")
            _staff(gimnasio, f"{prefijo}-staff-{g}")
            Alumno.objects.bulk_create(
                [
                    Alumno(gimnasio=gimnasio, nombre=f"A{a}", apellido=prefijo)
                    for a in range(alumnos_por_gimnasio)
                ]
            )

    def test_el_costo_no_crece_con_los_gimnasios_ni_con_los_alumnos(self):
        self._poblar(2, 3, "chico")
        with CaptureQueriesContext(connection) as chico:
            filas_chico = facturacion.filas_del_monitor(hoy=self.HOY)

        self._poblar(12, 40, "grande")
        with CaptureQueriesContext(connection) as grande:
            filas_grande = facturacion.filas_del_monitor(hoy=self.HOY)

        self.assertEqual(len(filas_chico), 2)
        self.assertEqual(len(filas_grande), 14)
        self.assertEqual(len(grande), len(chico))


class FilasDelMonitorTests(TestCase):
    HOY = date(2026, 6, 1)

    def _gimnasio_con_alta(self, nombre, dias_atras, alumnos=0, **extra):
        instante = datetime(
            self.HOY.year, self.HOY.month, self.HOY.day, 15, 0, tzinfo=dt_timezone.utc
        ) - timedelta(days=dias_atras)
        with patch("django.utils.timezone.now", return_value=instante):
            gimnasio = _gimnasio(nombre, **extra)
        Alumno.objects.bulk_create(
            [
                Alumno(gimnasio=gimnasio, nombre=f"A{i}", apellido=nombre)
                for i in range(alumnos)
            ]
        )
        return gimnasio

    def _fila_de(self, filas, nombre):
        return next(f for f in filas if f.gimnasio.nombre == nombre)

    def test_arma_precio_y_estado_por_gimnasio(self):
        self._gimnasio_con_alta("Nuevo", dias_atras=3, alumnos=2)
        self._gimnasio_con_alta("Atrasado", dias_atras=45, alumnos=120)

        filas = facturacion.filas_del_monitor(hoy=self.HOY)

        nuevo = self._fila_de(filas, "Nuevo")
        self.assertIs(nuevo.estado, EstadoPago.PRUEBA)
        self.assertEqual(nuevo.precio_usd, 10)
        self.assertEqual(nuevo.dias_de_atraso, 0)

        atrasado = self._fila_de(filas, "Atrasado")
        self.assertIs(atrasado.estado, EstadoPago.VENCIDA)
        self.assertEqual(atrasado.precio_usd, 15)
        self.assertEqual(atrasado.dias_de_atraso, 15)

    def test_la_cuenta_demo_queda_exenta(self):
        self._gimnasio_con_alta("Demo", dias_atras=200, alumnos=5, es_demo=True)

        fila = self._fila_de(facturacion.filas_del_monitor(hoy=self.HOY), "Demo")

        self.assertFalse(fila.factura)
        self.assertIs(fila.estado, EstadoPago.EXENTA)

    def test_kpis_cuentan_clientes_e_ingreso_solo_de_quienes_pagan(self):
        self._gimnasio_con_alta("Paga", dias_atras=45, alumnos=120)
        self._gimnasio_con_alta("Demo", dias_atras=200, alumnos=5, es_demo=True)

        kpis = facturacion.kpis(facturacion.filas_del_monitor(hoy=self.HOY))

        self.assertEqual(kpis["clientes"], 1)
        self.assertEqual(kpis["ingreso_mensual_usd"], 15)
        self.assertEqual(kpis["alumnos_activos"], 125)
        self.assertEqual(kpis["vencidos"], 1)
        self.assertEqual(kpis["por_vencer"], 0)

    def test_para_cobrar_ordena_por_vencimiento_y_deja_afuera_al_resto(self):
        self._gimnasio_con_alta("Atrasado", dias_atras=45)
        self._gimnasio_con_alta("Avisando", dias_atras=25)
        self._gimnasio_con_alta("Tranquilo", dias_atras=1)
        self._gimnasio_con_alta("Demo", dias_atras=200, es_demo=True)

        nombres = [
            f.gimnasio.nombre
            for f in facturacion.para_cobrar(
                facturacion.filas_del_monitor(hoy=self.HOY)
            )
        ]

        self.assertEqual(nombres, ["Atrasado", "Avisando"])


class AccesoAlPanelTests(TestCase):
    """El panel de plataforma es del dueño del producto, no de los gimnasios:
    un staff de gimnasio (que en su propia app es el rol más alto) tiene que
    recibir 403, igual que un alumno."""

    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena")
        self.url = reverse("plataforma:inicio")

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def test_staff_de_gimnasio_recibe_403(self):
        _staff(self.gimnasio, "duenio")
        self.client.login(username="duenio", password="clave-123456")

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_alumno_recibe_403(self):
        usuario = User.objects.create_user("alu", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alu", password="clave-123456")

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_superusuario_entra(self):
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")
        self.client.login(username="jefe", password="clave-123456")

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_el_detalle_de_un_gimnasio_es_solo_para_el_superusuario(self):
        _staff(self.gimnasio, "duenio")
        url = reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        self.client.login(username="duenio", password="clave-123456")
        self.assertEqual(self.client.get(url).status_code, 403)

        User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")
        self.client.login(username="jefe", password="clave-123456")
        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Vida Plena")

    def test_el_link_a_la_landing_no_va_boosteado(self):
        """La landing arma su fondo en `extra_style`, que vive en el <head>, y
        hx-boost solo reemplaza el <body>: boosteado, el gimnasio se ve con el
        fondo de la pantalla anterior. Es el mismo gotcha que ya apareció en
        Google Calendar, el PDF de rutina, el upload de logo y el login."""
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")
        self.client.login(username="jefe", password="clave-123456")

        html = self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        ).content.decode()

        destino = reverse("landing_gimnasio", args=[self.gimnasio.slug])
        self.assertIn(f'href="{destino}" hx-boost="false"', html)


class EntradaDelSuperusuarioTests(TestCase):
    """Un superusuario SIN Perfil no tiene gimnasio: el dashboard de staff no
    tiene nada que mostrarle y hoy le contesta 403 (`HomeView` exige Perfil).
    Su casa es el panel de plataforma. Con Perfil (es también staff de un
    gimnasio) el home sigue siendo el de siempre.
    """

    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena")

    def test_superusuario_sin_perfil_es_redirigido_al_panel(self):
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")
        self.client.login(username="jefe", password="clave-123456")

        respuesta = self.client.get(reverse("home"))

        self.assertRedirects(respuesta, reverse("plataforma:inicio"))

    def test_un_usuario_comun_sin_perfil_sigue_recibiendo_403(self):
        User.objects.create_user("suelto", password="clave-123456")
        self.client.login(username="suelto", password="clave-123456")

        self.assertEqual(self.client.get(reverse("home")).status_code, 403)

    def test_superusuario_con_perfil_ve_su_home_y_el_link_al_panel(self):
        usuario = _staff(self.gimnasio, "duenio-jefe")
        User.objects.filter(pk=usuario.pk).update(is_superuser=True, is_staff=True)
        self.client.login(username="duenio-jefe", password="clave-123456")

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "tenants/home.html")
        self.assertContains(respuesta, reverse("plataforma:inicio"))

    def test_el_staff_sin_superusuario_no_ve_el_link_al_panel(self):
        _staff(self.gimnasio, "duenio")
        self.client.login(username="duenio", password="clave-123456")

        respuesta = self.client.get(reverse("home"))

        self.assertNotContains(respuesta, reverse("plataforma:inicio"))

    def test_el_superusuario_sin_perfil_tiene_su_propia_nav_sin_alpine(self):
        """La nav de staff se apoya en un `x-data` que solo existe cuando el
        usuario tiene Perfil de staff: un `:class` de Alpine fuera de ese
        alcance deja la nav escondida en mobile para siempre."""
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")
        self.client.login(username="jefe", password="clave-123456")

        html = self.client.get(reverse("plataforma:inicio")).content.decode()

        self.assertIn('<nav class="nav-staff">', html)
        self.assertNotIn(":class", html)
        self.assertIn(reverse("admin:index"), html)
