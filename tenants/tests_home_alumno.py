"""Inicio del alumno en 2 columnas (`templates/tenants/home.html`).

Las columnas son sólo CSS (`.columnas`): el orden del HTML es el del celular
y no puede cambiar. Estos tests fijan ese orden y que cada condición sigue
mostrando lo mismo que antes.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ingreso import services
from pagos.models import Cuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio
from tenants.tests_nav_alumno import _alumno, _gimnasio, _staff
from tienda.models import ConfiguracionTienda


def _html(respuesta):
    return respuesta.content.decode()


class InicioAlumnoDosColumnasTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.alumno, self.usuario = _alumno(self.gimnasio)
        self.client.force_login(self.usuario)

    def _prender_tienda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=True)
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"publicada": True}
        )

    def _con_codigo(self):
        services.generar_credencial_qr(self.alumno)

    def test_usa_el_contenedor_del_alumno_y_las_dos_columnas(self):
        html = _html(self.client.get(reverse("home")))

        self.assertIn("contenido--alumno", html)
        self.assertNotIn("contenido--ancho", html)
        self.assertEqual(html.count('class="columnas"'), 1)

    def test_el_boton_de_reservar_es_ancho_en_celular_y_auto_en_la_compu(self):
        html = _html(self.client.get(reverse("home")))

        self.assertIn('class="boton w-full lg:w-auto mt-4"', html)

    def test_la_tarjeta_de_rutina_es_el_ancla_del_menu(self):
        html = _html(self.client.get(reverse("home")))

        self.assertEqual(html.count('id="mi-rutina"'), 1)
        self.assertIn('class="tarjeta scroll-mt-20" id="mi-rutina"', html)
        self.assertLess(html.index('id="mi-rutina"'), html.index("<h2>Tu rutina</h2>"))

    def test_orden_del_html_codigo_rutina_cuota_tienda_novedades(self):
        self._prender_tienda()
        self._con_codigo()
        html = _html(self.client.get(reverse("home")))

        orden = [
            html.index('class="columnas"'),
            html.index("Tu código para entrar"),
            html.index("<h2>Tu rutina</h2>"),
            html.index("<h2>Tu cuota</h2>"),
            html.index("Tienda del gimnasio"),
            html.index("<h2>Últimas novedades</h2>"),
            html.index(reverse("politica_privacidad")),
        ]
        self.assertEqual(orden, sorted(orden))

    def test_las_tarjetas_se_reparten_entre_las_dos_columnas(self):
        self._prender_tienda()
        self._con_codigo()
        html = _html(self.client.get(reverse("home")))

        inicio = html.index('class="columnas"')
        corte = html.index("Tienda del gimnasio")
        # Lo de la izquierda queda antes de la tienda; lo de la derecha, después.
        self.assertLess(inicio, html.index("Tu código para entrar"))
        self.assertLess(html.index("<h2>Tu cuota</h2>"), corte)
        self.assertLess(corte, html.index("<h2>Últimas novedades</h2>"))
        # Exactamente dos columnas: el `<div>` de la segunda abre entre «Tu cuota»
        # y la tienda, y no queda ninguna tarjeta suelta fuera de las columnas.
        entre = html[html.index("<h2>Tu cuota</h2>"):corte]
        self.assertIn("\n  <div>\n", entre)

    def test_sin_tienda_no_hay_tarjeta_y_las_novedades_siguen(self):
        html = _html(self.client.get(reverse("home")))

        self.assertNotIn("Tienda del gimnasio", html)
        self.assertNotIn("Tus pedidos de la tienda", html)
        self.assertIn("<h2>Últimas novedades</h2>", html)
        self.assertIn("<h2>Tu cuota</h2>", html)

    def test_con_tienda_ofrece_ver_productos_y_mis_pedidos(self):
        self._prender_tienda()
        html = _html(self.client.get(reverse("home")))

        self.assertIn("Tienda del gimnasio", html)
        self.assertIn(reverse("tienda:catalogo"), html)
        self.assertIn(reverse("tienda:mis_pedidos"), html)

    def test_sin_vinculo_solo_muestra_el_cartel(self):
        sin_ficha = _staff(self.gimnasio, username="sin-ficha")
        sin_ficha.perfil.rol = "alumno"
        sin_ficha.perfil.save()
        self.client.force_login(sin_ficha)
        html = _html(self.client.get(reverse("home")))

        self.assertIn("Todavía no está vinculada tu cuenta", html)
        self.assertNotIn("<h2>Tu rutina</h2>", html)
        self.assertNotIn('id="mi-rutina"', html)
        self.assertNotIn("<h2>Tu cuota</h2>", html)
        self.assertNotIn("Tienda del gimnasio", html)
        self.assertIn("<h2>Últimas novedades</h2>", html)
        self.assertEqual(html.count('class="columnas"'), 1)

    def test_bloqueado_muestra_el_aviso_antes_que_la_rutina_en_pausa(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5,
            fecha_activacion_bloqueo=timezone.localdate() - timedelta(days=365),
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=40),
            estado=Cuota.Estado.PENDIENTE,
        )
        html = _html(self.client.get(reverse("home")))

        self.assertIn("Tu acceso está pausado", html)
        self.assertIn("Vas a volver a ver tu rutina", html)
        self.assertLess(
            html.index("Tu acceso está pausado"), html.index("<h2>Tu rutina</h2>")
        )
        self.assertLess(
            html.index("<h2>Tu rutina</h2>"), html.index("<h2>Tu cuota</h2>")
        )
        self.assertIn("<h2>Últimas novedades</h2>", html)

    def test_sin_bloqueo_no_hay_aviso_de_pausa(self):
        html = _html(self.client.get(reverse("home")))

        self.assertNotIn("Tu acceso está pausado", html)

    def test_el_staff_conserva_su_panel_ancho_sin_columnas(self):
        self.client.force_login(_staff(self.gimnasio))
        html = _html(self.client.get(reverse("home")))

        self.assertIn("contenido--ancho", html)
        self.assertNotIn("contenido--alumno", html)
        self.assertNotIn('class="columnas"', html)
