"""Pantallas del descuento (pieza 3 de reconocimientos, T4).

La pantalla «Corregir monto» (monto + descuento + motivo) y el neto en todos
los lugares donde se ve el monto de una cuota. Sin descuento el HTML no cambia.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth.models import User
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from pagos.admin import CuotaAdmin
from pagos.models import CobroOnlineCuota, Cuota
from pagos.testing import crear_cuota
from pagos.tests_cobro import _Base
from pagos.tests_pagar_mp import MP
from pagos.tests_pagar_mp import _Base as _BasePortal
from tenants.models import Gimnasio, Perfil

C = Cuota.Estado


class FormularioTests(_Base):
    def setUp(self):
        super().setUp()
        self._login_staff()
        self.url = reverse("pagos:monto", args=[self.cuota.pk])  # monto 28000

    def post(self, **datos):
        base = {"monto": "28000", "descuento": "0", "descuento_motivo": ""}
        base.update(datos)
        return self.client.post(self.url, base)

    def assertSinCambios(self):
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.descuento, Decimal("0"))
        self.assertEqual(self.cuota.monto, Decimal("28000"))

    def test_get_muestra_los_tres_campos(self):
        r = self.client.get(self.url)
        for campo in ("monto", "descuento", "descuento_motivo"):
            self.assertIn(campo, r.context["form"].fields)
        self.assertContains(r, 'name="descuento"')
        self.assertContains(r, 'name="descuento_motivo"')

    def test_guarda_descuento_y_motivo_y_cancela_el_link_vivo(self):
        vivo = self._intento()
        r = self.post(descuento="2800", descuento_motivo="Premio del mes")
        self.assertRedirects(r, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.descuento, Decimal("2800"))
        self.assertEqual(self.cuota.descuento_motivo, "Premio del mes")
        self.assertEqual(self.cuota.monto_a_cobrar, Decimal("25200"))
        self.assertEqual(vivo.estado, CobroOnlineCuota.Estado.CANCELADO)

    def test_descuento_cero_quita_el_descuento(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("100"), descuento_motivo="x"
        )
        self.post(descuento="0", descuento_motivo="lo que sea")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.descuento, Decimal("0"))
        self.assertEqual(self.cuota.descuento_motivo, "")

    def test_tope_dice_el_maximo_en_pesos(self):
        r = self.post(descuento="25300", descuento_motivo="x")  # tope 25200
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["descuento"])
        self.assertContains(r, "25200")
        self.assertContains(r, 'class="config-error"')
        self.assertSinCambios()

    def test_en_el_tope_exacto_pasa(self):
        r = self.post(descuento="25200", descuento_motivo="x")
        self.assertEqual(r.status_code, 302)

    def test_negativo(self):
        r = self.post(descuento="-1", descuento_motivo="x")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["descuento"])
        self.assertSinCambios()

    def test_motivo_largo(self):
        r = self.post(descuento="100", descuento_motivo="x" * 81)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["descuento_motivo"])
        self.assertSinCambios()

    def test_motivo_vacio_con_descuento(self):
        r = self.post(descuento="100", descuento_motivo="  ")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["descuento_motivo"])
        self.assertContains(r, "motivo")
        self.assertSinCambios()

    def test_bajar_el_monto_por_debajo_del_tope_del_descuento_vigente(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("20000"), descuento_motivo="x"
        )
        r = self.post(monto="1000", descuento="20000", descuento_motivo="x")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["descuento"])
        self.assertContains(r, "900")

    def test_sin_descuento_el_flujo_de_siempre(self):
        r = self.post(monto="30000")
        self.assertRedirects(r, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("30000"))

    def test_staff_de_otro_gimnasio_da_404(self):
        otro = User.objects.create_user("staff-b", password="clave-123456")
        Perfil.objects.create(usuario=otro, gimnasio=self.otro_gimnasio, rol=Perfil.Rol.STAFF)
        self.client.logout()
        self.client.login(username="staff-b", password="clave-123456")
        self.assertEqual(self.post(descuento="100", descuento_motivo="x").status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertSinCambios()


class MontoEnLasPantallasTests(_Base):
    """Staff: listado de pagos, detalle del alumno y home."""

    def setUp(self):
        super().setUp()
        self._login_staff()

    def paginas(self):
        return [
            reverse("pagos:listado"),
            reverse("alumnos:detalle", args=[self.alumno.pk]),
            reverse("home"),
        ]

    def test_sin_descuento_el_html_es_el_de_hoy(self):
        for url in self.paginas():
            html = self.client.get(url).content.decode()
            self.assertIn("<td>28000,00</td>", html, url)
            self.assertNotIn("<s>", html, url)

    def test_con_descuento_muestra_lista_tachada_neto_y_motivo(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("2800"), descuento_motivo="Premio del mes"
        )
        for url in self.paginas():
            html = self.client.get(url).content.decode()
            self.assertIn("<s>28000,00</s>", html, url)
            self.assertIn("25200,00", html, url)
            self.assertIn("Premio del mes", html, url)

    def test_el_listado_no_suma_queries_por_fila(self):
        url = reverse("pagos:listado")

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx)

        def agregar(desde, con_descuento):
            for i in range(desde, desde + 3):
                extra = {"descuento": Decimal("100"), "descuento_motivo": "m"} if con_descuento else {}
                crear_cuota(
                    gimnasio=self.gimnasio, alumno=self.alumno,
                    inicio=self.hoy - timedelta(days=3 + 28 * i), monto=Decimal("1000"),
                    **extra,
                )

        agregar(1, False)
        queries()
        antes = queries()
        agregar(4, True)
        self.assertEqual(queries(), antes)


@override_settings(**MP)
class PortalDelAlumnoTests(_BasePortal):
    def test_sin_descuento_los_textos_de_hoy(self):
        html = self.html_home()
        self.assertIn("Pagar $ 28.000 con Mercado Pago", html)
        self.assertIn("$ 28.000</span>", html)
        self.assertNotIn("<s>", html)
        r = self.client.get(reverse("pagos:comprobante_subir", args=[self.cuota.pk]))
        self.assertContains(r, "Monto: <strong>$ 28000,00</strong>")
        self.assertNotContains(r, "<s>")

    def test_con_descuento_el_boton_y_el_comprobante_van_por_el_neto(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("2800"), descuento_motivo="Premio del mes"
        )
        html = self.html_home()
        self.assertIn("Pagar $ 25.200 con Mercado Pago", html)
        self.assertIn("<s>$ 28.000</s>", html)
        self.assertIn("Premio del mes", html)
        r = self.client.get(reverse("pagos:comprobante_subir", args=[self.cuota.pk]))
        self.assertContains(r, "25200,00")
        self.assertContains(r, "<s>$ 28000,00</s>")
        self.assertContains(r, "Premio del mes")

    def test_neto_cero_no_ofrece_el_boton(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=0)
        self.assertNotIn(self.form(), self.html_home())

    def test_alumno_de_otro_gimnasio_no_ve_la_cuota_ajena(self):
        otro = User.objects.create_user("alu-b", password="c-123456")
        gym = Gimnasio.objects.create(nombre="Gym B", slug="b")
        perfil = Perfil.objects.create(usuario=otro, gimnasio=gym, rol=Perfil.Rol.ALUMNO)
        Alumno.objects.create(gimnasio=gym, nombre="Beto", apellido="B", perfil=perfil)
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("2800"), descuento_motivo="Premio del mes"
        )
        self.client.force_login(otro)
        self.assertNotIn("Premio del mes", self.html_home())
        r = self.client.get(reverse("pagos:comprobante_subir", args=[self.cuota.pk]))
        self.assertEqual(r.status_code, 404)


class AdminTests(_Base):
    def setUp(self):
        super().setUp()
        self.admin = CuotaAdmin(Cuota, admin_site)
        self.request = RequestFactory().get("/admin/")

    def test_editable_en_una_impaga(self):
        ro = self.admin.get_readonly_fields(self.request, self.cuota)
        self.assertNotIn("descuento", ro)
        self.assertNotIn("descuento_motivo", ro)

    def test_solo_lectura_en_pagada_y_anulada(self):
        for estado in (C.PAGADO, C.ANULADO):
            Cuota.objects.filter(pk=self.cuota.pk).update(estado=estado)
            self.cuota.refresh_from_db()
            ro = self.admin.get_readonly_fields(self.request, self.cuota)
            self.assertIn("descuento", ro)
            self.assertIn("descuento_motivo", ro)

    def test_alta_nueva_es_editable(self):
        ro = self.admin.get_readonly_fields(self.request, None)
        self.assertNotIn("descuento", ro)
