from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import Pedido, Producto, Variante, obtener_configuracion
from tienda.testing import PNG


class _BaseAlumno(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.client.force_login(usuario)
        self.producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"))
        self.variante = Variante.objects.create(producto=self.producto, nombre="M", stock=2)

    def agregar(self, cantidad=1, variante=None):
        return self.client.post(reverse("tienda:carrito"), {
            "accion": "agregar", "variante": (variante or self.variante).pk, "cantidad": cantidad,
        }, follow=True)


class CompraTests(_BaseAlumno):
    def test_compra_de_punta_a_punta(self):
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Remera")
        self.agregar(2)
        self.assertContains(self.client.get(reverse("tienda:carrito")), "30.000")
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        pedido = Pedido.objects.get(alumno=self.alumno)
        self.assertRedirects(r, reverse("tienda:mi_pedido", args=[pedido.pk]))
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 0)
        # El carrito quedó vacío.
        self.assertNotContains(self.client.get(reverse("tienda:carrito")), "30.000")

    def test_sin_stock_es_cartel_y_no_crea_pedido(self):
        self.agregar(2)
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertContains(r, "No queda stock suficiente")
        self.assertFalse(Pedido.objects.exists())

    def test_suplantando_no_compra(self):
        from tenants import suplantacion

        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION] = {"inicio": timezone.now().isoformat()}
        sesion.save()
        self.agregar(1)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertContains(r, "no se puede comprar")
        self.assertFalse(Pedido.objects.exists())

    def test_un_producto_archivado_en_el_carrito_no_traba_la_compra(self):
        otro = Variante.objects.create(producto=self.producto, nombre="L", stock=2)
        self.agregar(1)
        self.agregar(1, variante=otro)
        Variante.objects.filter(pk=otro.pk).update(activa=False)
        self.client.post(reverse("tienda:checkout"))
        pedido = Pedido.objects.get(alumno=self.alumno)
        self.assertEqual(list(pedido.items.values_list("variante_id", flat=True)), [self.variante.pk])

    def test_catalogo_oculta_sin_stock_como_agotado(self):
        Variante.objects.filter(pk=self.variante.pk).update(stock=0)
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Agotado")

    def test_catalogo_avisa_cuando_quedan_pocas(self):
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Quedan 2")  # stock 2
        Variante.objects.filter(pk=self.variante.pk).update(stock=services.UMBRAL_POCAS_UNIDADES + 1)
        self.assertNotContains(self.client.get(reverse("tienda:catalogo")), "Quedan")


class ComprobanteVistaTests(_BaseAlumno):
    def setUp(self):
        super().setUp()
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 1}, suplantando=False,
        )

    def test_subir_comprobante(self):
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", PNG, content_type="image/png")},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.EN_REVISION)

    def test_un_pdf_o_basura_es_error_visible(self):
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", b"no es imagen", content_type="image/png")},
            follow=True,
        )
        self.assertContains(r, "imagen")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_subir_tarde_dice_que_el_plazo_vencio_y_libera_el_stock(self):
        from django.core.files.storage import default_storage

        Pedido.objects.filter(pk=self.pedido.pk).update(
            reservado_hasta=timezone.now() - timedelta(days=1)
        )
        antes = set(default_storage.listdir("tienda/comprobantes/")[1]) if default_storage.exists("tienda/comprobantes/") else set()
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", PNG, content_type="image/png")},
            follow=True,
        )
        self.assertContains(r, "venció")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.VENCIDO)
        self.assertFalse(self.pedido.comprobante)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)  # liberado una sola vez
        despues = set(default_storage.listdir("tienda/comprobantes/")[1]) if default_storage.exists("tienda/comprobantes/") else set()
        self.assertEqual(antes, despues)

    def test_un_gif_renombrado_png_se_rechaza(self):
        from io import BytesIO

        from PIL import Image

        salida = BytesIO()
        Image.new("RGB", (1, 1)).save(salida, "GIF")
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", salida.getvalue(), content_type="image/png")},
            follow=True,
        )
        self.assertContains(r, "JPG o PNG")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_pedido_de_otro_alumno_404(self):
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        ajeno = services.crear_pedido(gimnasio=self.gimnasio, alumno=otro,
                                      carrito={self.variante.pk: 1}, suplantando=False)
        self.assertEqual(self.client.get(reverse("tienda:mi_pedido", args=[ajeno.pk])).status_code, 404)

    def test_detalle_muestra_datos_de_transferencia(self):
        from pagos.models import MedioCobro

        MedioCobro.objects.create(gimnasio=self.gimnasio, alias="gym.alias.mp", titular="Gym SA")
        self.assertContains(self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk])), "gym.alias.mp")

    def test_cancelar(self):
        self.client.post(reverse("tienda:mi_pedido_cancelar", args=[self.pedido.pk]))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.CANCELADO)

    def test_pedir_cancelacion_de_un_pagado(self):
        services.marcar_pagado(self.pedido)
        r = self.client.post(
            reverse("tienda:mi_pedido_pedir_cancelacion", args=[self.pedido.pk]), follow=True
        )
        self.assertContains(r, f"Solicitud registrada — pedido #{self.pedido.pk}")
        self.pedido.refresh_from_db()
        self.assertIsNotNone(self.pedido.cancelacion_pedida_en)

    def test_comprobante_rechazado_muestra_el_motivo_y_deja_subir_otro(self):
        services.subir_comprobante(
            self.pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"),
        )
        services.rechazar_comprobante(self.pedido, motivo="No se ve el monto")
        r = self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk]))
        self.assertContains(r, "No se ve el monto")
        self.assertContains(r, reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]))

    def test_rechazo_sin_motivo_igual_se_avisa(self):
        services.subir_comprobante(
            self.pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"),
        )
        services.rechazar_comprobante(self.pedido, motivo="")
        r = self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk]))
        self.assertContains(r, "rechazó tu comprobante")


class BotonDeArrepentimientoTests(_BaseAlumno):
    """Res. 424/2020: el «botón de arrepentimiento» tiene que estar a la
    vista, no escondido dentro de cada pedido."""

    def setUp(self):
        super().setUp()
        self.url = reverse("tienda:mis_pedidos") + "?arrepentimiento=1"

    def test_el_link_esta_en_el_catalogo_y_en_mis_pedidos(self):
        for pantalla in ("tienda:catalogo", "tienda:mis_pedidos"):
            r = self.client.get(reverse(pantalla))
            self.assertContains(r, "Botón de arrepentimiento")
            self.assertContains(r, self.url)

    def test_lista_solo_los_pedidos_cancelables(self):
        pagado = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pagado)
        pendiente = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                          carrito={self.variante.pk: 1}, suplantando=False)
        r = self.client.get(self.url)
        self.assertContains(r, reverse("tienda:mi_pedido_pedir_cancelacion", args=[pagado.pk]))
        self.assertNotContains(r, reverse("tienda:mi_pedido", args=[pendiente.pk]))


class TiendaApagadaAlumnoTests(_BaseAlumno):
    def test_sin_publicar_no_hay_catalogo_pero_si_mis_pedidos(self):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = False
        config.save()
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no está disponible")
        self.assertContains(
            self.client.get(reverse("tienda:mis_pedidos")), reverse("tienda:mi_pedido", args=[pedido.pk])
        )

    def test_card_del_portal(self):
        # `href="..."` exacto: «/tienda/» también está adentro de «/tienda/mis-pedidos/».
        catalogo = f'href="{reverse("tienda:catalogo")}"'
        self.assertContains(self.client.get(reverse("home")), catalogo)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, catalogo)
        self.assertNotContains(r, reverse("tienda:mis_pedidos"))

    def test_apagada_queda_el_link_con_un_entregado_dentro_del_plazo_de_arrepentimiento(self):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pedido)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.Estado.ENTREGADO, entregado_en=timezone.now() - timedelta(days=2)
        )
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        self.assertContains(self.client.get(reverse("home")), reverse("tienda:mis_pedidos"))
        Pedido.objects.filter(pk=pedido.pk).update(entregado_en=timezone.now() - timedelta(days=30))
        self.assertNotContains(self.client.get(reverse("home")), reverse("tienda:mis_pedidos"))

    def test_con_la_tienda_apagada_queda_el_link_a_mis_pedidos_si_hay_pedidos_vivos(self):
        services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                              carrito={self.variante.pk: 1}, suplantando=False)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, f'href="{reverse("tienda:catalogo")}"')
        self.assertContains(r, reverse("tienda:mis_pedidos"))


class VerbosTests(_BaseAlumno):
    def test_get_al_checkout_y_post_al_catalogo_son_405_no_500(self):
        self.assertEqual(self.client.get(reverse("tienda:checkout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("tienda:catalogo")).status_code, 405)


class VencimientoPerezosoSinPushTests(_BaseAlumno):
    def test_ni_el_catalogo_ni_el_listado_del_staff_mandan_push_ni_tocan_la_red(self):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        Pedido.objects.filter(pk=pedido.pk).update(reservado_hasta=timezone.now() - timedelta(hours=1))
        prohibido = AssertionError("el camino perezoso no puede mandar push")
        with patch("notificaciones.services.notificar_a_usuario", side_effect=prohibido), \
                patch("notificaciones.services.notificar_a_gimnasio", side_effect=prohibido):
            self.client.get(reverse("tienda:catalogo"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.VENCIDO)


class AvisosDeLasVistasTests(_BaseAlumno):
    """En un `TestCase` los `on_commit` no corren solos."""

    @patch("notificaciones.services.notificar_pedido_al_staff")
    def test_el_checkout_avisa_al_staff(self, aviso):
        self.agregar(1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tienda:checkout"))
        self.assertEqual(aviso.call_args.args[1], "nuevo")

    @patch("notificaciones.services.notificar_pedido_al_staff")
    def test_el_comprobante_avisa_al_staff(self, aviso):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("tienda:mi_pedido_comprobante", args=[pedido.pk]),
                {"comprobante": SimpleUploadedFile("c.png", PNG, content_type="image/png")},
            )
        self.assertEqual(aviso.call_args.args[1], "comprobante")


class PerfilSinAlumnoTests(TestCase):
    def test_cartel_200_no_403(self):
        gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        u = User.objects.create_user("solo", password="c-123456")
        Perfil.objects.create(usuario=u, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(u)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertEqual(r.status_code, 200)


class PublicoDeLasVistasTests(TestCase):
    #: Las únicas vistas sin mixin de rol, cada una con su motivo.
    EXCEPCIONES = {
        # Pública a propósito: la foto de un producto no es dato sensible y va
        # en `URLS_SIN_BLOQUEO` para responder `immutable` sin cookie.
        "FotoProductoView",
    }

    def test_toda_vista_declara_su_publico(self):
        import inspect

        from django.views import View

        from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
        from tienda import views

        clases = [
            c for _, c in inspect.getmembers(views, inspect.isclass)
            if issubclass(c, View) and c.__module__ == views.__name__
        ]
        self.assertTrue(clases)
        for clase in clases:
            with self.subTest(clase.__name__):
                publicos = [m for m in (StaffRequiredMixin, AlumnoRequiredMixin) if issubclass(clase, m)]
                if clase.__name__ in self.EXCEPCIONES:
                    self.assertEqual(publicos, [])
                else:
                    self.assertEqual(len(publicos), 1)
