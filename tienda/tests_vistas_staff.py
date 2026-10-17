import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import CategoriaProducto, Pedido, Producto, Variante


def jpeg(ancho=1600, alto=1200):
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), "red").save(salida, "JPEG")
    return SimpleUploadedFile("foto.jpg", salida.getvalue(), content_type="image/jpeg")


class _BaseStaff(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)

    def crear_producto(self, gimnasio=None, stock=3):
        producto = Producto.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        variante = Variante.objects.create(producto=producto, nombre="M", stock=stock)
        return producto, variante


def datos_producto(**extra):
    datos = {
        "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
        "orden": "0", "categoria": "",
        "variantes-TOTAL_FORMS": "2", "variantes-INITIAL_FORMS": "0",
        "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
        "variantes-0-nombre": "Chocolate", "variantes-0-stock_inicial": "4", "variantes-0-activa": "on",
        "variantes-1-nombre": "Vainilla", "variantes-1-stock_inicial": "2", "variantes-1-activa": "on",
    }
    datos.update(extra)
    return datos


class ProductoFormTests(_BaseStaff):
    def test_crear_con_variantes_y_foto_reescalada(self):
        datos = datos_producto()
        datos["foto"] = jpeg()
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        self.assertEqual(
            sorted(producto.variantes.values_list("nombre", "stock")),
            [("Chocolate", 4), ("Vainilla", 2)],
        )
        with producto.foto.open("rb") as f:
            self.assertLessEqual(max(Image.open(f).size), 1200)

    def test_sin_ninguna_variante_es_error_visible(self):
        datos = datos_producto(**{"variantes-0-nombre": "", "variantes-0-stock_inicial": "",
                                   "variantes-1-nombre": "", "variantes-1-stock_inicial": ""})
        datos.pop("variantes-0-activa")
        datos.pop("variantes-1-activa")
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "al menos una variante")

    def test_editar_el_nombre_no_pisa_el_stock_reservado(self):
        producto, variante = self.crear_producto(stock=3)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 2}, suplantando=False)
        datos = {
            "nombre": "Remera Dry", "descripcion": "", "precio": "15000", "publicado": "on",
            "orden": "0", "categoria": "",
            "variantes-TOTAL_FORMS": "1", "variantes-INITIAL_FORMS": "1",
            "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
            "variantes-0-id": str(variante.pk), "variantes-0-nombre": "M",
            "variantes-0-activa": "on",
        }
        r = self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 1)

    def test_errores_del_formset_se_ven(self):
        """`core.tests.ErroresDeFormularioVisiblesTests` sólo mira `form.<campo>`:
        los campos del formset de variantes no los cubre, así que se prueban acá."""
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-1-nombre": "Chocolate"}),
        )
        self.assertContains(r, "mismo nombre")
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-0-stock_inicial": "-3"}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_categoria_de_otro_gimnasio_no_se_puede_elegir(self):
        ajena = CategoriaProducto.objects.create(gimnasio=self.otro, nombre="Ajena")
        r = self.client.post(reverse("tienda:producto_crear"), datos_producto(categoria=str(ajena.pk)))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_no_edita_un_producto_ajeno(self):
        producto, _ = self.crear_producto(gimnasio=self.otro)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertEqual(r.status_code, 404)

    def test_editar_muestra_el_formulario_con_variantes(self):
        producto, variante = self.crear_producto()
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="M"')


class CategoriaTests(_BaseStaff):
    def test_nombre_repetido_es_error_visible_no_500(self):
        CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        r = self.client.post(reverse("tienda:categoria_crear"), {"nombre": "ropa", "orden": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ya hay una categoría")

    def test_editar_no_crea_otra(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.client.post(
            reverse("tienda:categoria_editar", args=[cat.pk]), {"nombre": "Indumentaria", "orden": "0"}
        )
        self.assertEqual(CategoriaProducto.objects.for_gimnasio(self.gimnasio).count(), 1)
        cat.refresh_from_db()
        self.assertEqual(cat.nombre, "Indumentaria")

    def test_lista_y_borrar(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.assertContains(self.client.get(reverse("tienda:categorias")), "Ropa")
        self.assertEqual(
            self.client.get(reverse("tienda:categoria_borrar", args=[cat.pk])).status_code, 200
        )
        self.client.post(reverse("tienda:categoria_borrar", args=[cat.pk]))
        self.assertFalse(CategoriaProducto.objects.filter(pk=cat.pk).exists())


class AjusteStockTests(_BaseStaff):
    def test_suma_y_resta(self):
        _, variante = self.crear_producto(stock=3)
        url = reverse("tienda:variante_ajustar", args=[variante.pk])
        self.client.post(url, {"delta": "5"})
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)
        r = self.client.post(url, {"delta": "-20"}, follow=True)
        self.assertContains(r, "No hay tantas unidades")
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)

    def test_variante_ajena_404(self):
        _, ajena = self.crear_producto(gimnasio=self.otro)
        r = self.client.post(reverse("tienda:variante_ajustar", args=[ajena.pk]), {"delta": "5"})
        self.assertEqual(r.status_code, 404)


class BorrarProductoTests(_BaseStaff):
    def test_sin_ventas_se_borra(self):
        producto, _ = self.crear_producto()
        self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertFalse(Producto.objects.filter(pk=producto.pk).exists())

    def test_con_ventas_no_se_borra_y_lo_explica(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 1}, suplantando=False)
        r = self.client.get(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertContains(r, "pedidos")
        r = self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertContains(r, "Destildá «Publicado»")


class FotoTests(_BaseStaff):
    def test_sirve_la_foto_cacheable_sin_cookie(self):
        producto, _ = self.crear_producto()
        producto.foto.save("f.jpg", jpeg(400, 400))
        self.client.logout()
        r = self.client.get(producto.foto_url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r["Cache-Control"])
        self.assertNotIn("sessionid", r.cookies)

    def test_logueado_tampoco_lleva_cookie_ni_registra_actividad(self):
        """El anónimo corta antes en el middleware y no prueba nada: con sesión
        es donde `URLS_SIN_BLOQUEO` evita la cookie en una respuesta que se
        cachea un año (molde: `plataforma/tests_actividad.py::ArchivosCacheablesTests`)."""
        from plataforma.models import ActividadDiaria

        producto, _ = self.crear_producto()
        producto.foto.save("f.jpg", jpeg(400, 400))
        r = self.client.get(producto.foto_url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.cookies.keys()), [])
        self.assertFalse(ActividadDiaria.objects.exists())

    def test_gimnasio_sin_tienda_404(self):
        producto, _ = self.crear_producto(gimnasio=Gimnasio.objects.create(nombre="C", slug="c"))
        producto.foto.save("f.jpg", jpeg(400, 400))
        self.assertEqual(self.client.get(producto.foto_url_cacheable).status_code, 404)


class AccesoStaffTests(_BaseStaff):
    def test_sin_add_on_muestra_cartel_200(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:productos"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no está habilitada")

    def test_alumno_no_entra(self):
        u = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=u, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("tienda:productos")).status_code, 403)


class StockBajoTests(_BaseStaff):
    def test_badge_de_stock_bajo_hasta_el_umbral(self):
        _, variante = self.crear_producto(stock=services.UMBRAL_POCAS_UNIDADES)
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")
        Variante.objects.filter(pk=variante.pk).update(stock=services.UMBRAL_POCAS_UNIDADES + 1)
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")

    def test_sin_stock_no_es_stock_bajo(self):
        self.crear_producto(stock=0)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, "Sin stock")
        self.assertNotContains(r, "data-stock-bajo")


class PedidosStaffTests(_BaseStaff):
    def setUp(self):
        super().setUp()
        self.producto, self.variante = self.crear_producto(stock=5)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 2}, suplantando=False,
        )

    def accion(self, accion, **extra):
        return self.client.post(
            reverse("tienda:pedido_accion", args=[self.pedido.pk]),
            {"accion": accion, **extra}, follow=True,
        )

    def test_listado_filtra_a_revisar(self):
        # Se busca el link al detalle y no «#<pk>»: `base.html` inyecta el
        # color del gimnasio (`#1d6f56`) y «#1» aparece en cualquier página.
        detalle = reverse("tienda:pedido_detalle", args=[self.pedido.pk])
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertNotContains(r, detalle)
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertContains(r, detalle)

    @patch("notificaciones.services.notificar_pedido_al_alumno")
    def test_marcar_pagado_avisa_al_alumno(self, aviso):
        with self.captureOnCommitCallbacks(execute=True):
            self.accion("pagado")
        self.assertEqual(aviso.call_args.args[1], "confirmado")

    def test_marcar_pagado_y_entregar(self):
        self.accion("pagado")
        self.accion("entregar")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.ENTREGADO)

    def test_rechazar_pide_otro_comprobante_y_sigue_reservado(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.accion("rechazar", motivo="No se lee")
        self.assertContains(r, "puede subir otro")
        self.pedido.refresh_from_db()
        self.assertEqual(
            (self.pedido.estado, self.pedido.motivo), (Pedido.Estado.PENDIENTE_PAGO, "No se lee")
        )
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 3)  # 5 - 2: sigue reservado

    def test_cancelar_desde_revision_si_devuelve_el_stock(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        self.accion("cancelar", motivo="No llegó la plata")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.CANCELADO)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 5)

    def test_accion_invalida_es_cartel_no_500(self):
        r = self.accion("entregar")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ya cambió de estado")

    def test_pedido_ajeno_404(self):
        otro_alumno = Alumno.objects.create(gimnasio=self.otro, nombre="B", apellido="P")
        _, var = self.crear_producto(gimnasio=self.otro)
        ajeno = services.crear_pedido(gimnasio=self.otro, alumno=otro_alumno,
                                      carrito={var.pk: 1}, suplantando=False)
        self.assertEqual(self.client.get(reverse("tienda:pedido_detalle", args=[ajeno.pk])).status_code, 404)
        r = self.client.post(reverse("tienda:pedido_accion", args=[ajeno.pk]), {"accion": "pagado"})
        self.assertEqual(r.status_code, 404)

    def test_listado_vence_perezosamente(self):
        from django.utils import timezone
        from datetime import timedelta

        Pedido.objects.filter(pk=self.pedido.pk).update(
            reservado_hasta=timezone.now() - timedelta(hours=1)
        )
        self.client.get(reverse("tienda:pedidos"))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.VENCIDO)


class TiendaApagadaTests(_BaseStaff):
    def test_el_staff_sigue_gestionando_los_pedidos_vivos(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pedido)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=entregar")
        self.assertContains(r, reverse("tienda:pedido_detalle", args=[pedido.pk]))
        self.client.post(reverse("tienda:pedido_accion", args=[pedido.pk]), {"accion": "entregar"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        # ...pero productos y configuración no.
        self.assertContains(self.client.get(reverse("tienda:productos")), "no está habilitada")


class ConfiguracionTests(_BaseStaff):
    def test_guardar(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "publicada": "on", "horas_reserva_transferencia": "48",
            "max_pedidos_activos": "3", "instrucciones_retiro": "Recepción",
        })
        self.assertRedirects(r, reverse("tienda:configuracion"))
        from tienda.models import obtener_configuracion
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.publicada, c.horas_reserva_transferencia), (True, 48))

    def test_plazo_fuera_de_rango_es_error_visible(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "horas_reserva_transferencia": "0", "max_pedidos_activos": "2",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")  # o la clase del partial


class PanelYBarraTests(_BaseStaff):
    def test_tarjeta_del_panel_con_pendientes(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Pedidos de la tienda")
        self.assertContains(r, reverse("tienda:pedidos") + "?filtro=revisar")

    def test_sin_add_on_ni_link_ni_tarjeta(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, reverse("tienda:productos"))
        self.assertNotContains(r, "Pedidos de la tienda")

    def test_con_add_on_link_en_la_barra(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, f'href="{reverse("tienda:productos")}"')

    def test_chip_adicional_solo_en_la_demo(self):
        """La portada dice «todas las funciones incluidas» hasta el Hito 3:
        en la demo, la tienda se marca como adicional."""
        self.assertNotContains(self.client.get(reverse("home")), "data-adicional")
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-adicional")
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.assertContains(self.client.get(reverse("home")), "data-adicional")
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-adicional")
