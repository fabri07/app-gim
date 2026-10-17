"""Modelos de la tienda: restricciones que tiene que hacer cumplir la base,
no la vista."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
)


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("25000")
        )
        self.variante = Variante.objects.create(producto=self.producto, stock=3)


class VarianteTests(_Base):
    def test_el_stock_no_puede_quedar_negativo_en_la_base(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variante.objects.filter(pk=self.variante.pk).update(stock=-1)

    def test_nombre_unico_por_producto(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variante.objects.create(producto=self.producto, stock=1)

    def test_str_sin_variantes_es_el_producto(self):
        self.assertEqual(str(self.variante), "Proteína")
        talle = Variante.objects.create(producto=self.producto, nombre="M", stock=1)
        self.assertEqual(str(talle), "Proteína · M")


class ItemPedidoTests(_Base):
    def test_la_cantidad_cero_la_rechaza_la_base(self):
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, total=Decimal("0"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ItemPedido.objects.create(
                pedido=pedido, variante=self.variante, nombre="x",
                precio_unitario=Decimal("1"), cantidad=0,
            )

    def test_la_variante_vendida_no_se_puede_borrar(self):
        from django.db.models import ProtectedError

        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(gimnasio=self.gimnasio, alumno=alumno, total=1)
        ItemPedido.objects.create(
            pedido=pedido, variante=self.variante, nombre="x",
            precio_unitario=Decimal("1"), cantidad=1,
        )
        with self.assertRaises(ProtectedError):
            self.producto.delete()


class PedidoTests(_Base):
    def test_clean_rechaza_un_alumno_de_otro_gimnasio(self):
        ajeno = Alumno.objects.create(gimnasio=self.otro, nombre="Bruno", apellido="P")
        pedido = Pedido(gimnasio=self.gimnasio, alumno=ajeno, total=1)
        with self.assertRaises(ValidationError):
            pedido.clean()

    def test_estados_que_retienen(self):
        from tienda.models import ESTADOS_QUE_RETIENEN

        self.assertEqual(
            set(ESTADOS_QUE_RETIENEN),
            {Pedido.Estado.PENDIENTE_PAGO, Pedido.Estado.EN_REVISION, Pedido.Estado.PAGADO},
        )


class ConfiguracionTests(_Base):
    def test_obtener_configuracion_crea_una_sola(self):
        a = obtener_configuracion(self.gimnasio)
        b = obtener_configuracion(self.gimnasio)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(a.horas_reserva_transferencia, 24)
        self.assertEqual(a.max_pedidos_activos, 2)
        self.assertFalse(a.publicada)

    def test_una_por_gimnasio(self):
        obtener_configuracion(self.gimnasio)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionTienda.objects.create(gimnasio=self.gimnasio)


class CategoriaTests(_Base):
    def test_borrar_la_categoria_deja_el_producto_sin_categoria(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.producto.categoria = cat
        self.producto.save()
        cat.delete()
        self.producto.refresh_from_db()
        self.assertIsNone(self.producto.categoria)


class AislamientoTests(_Base):
    """Un test de aislamiento por modelo tenant-owned (regla de `CLAUDE.md`)."""

    def test_producto(self):
        Producto.objects.create(gimnasio=self.otro, nombre="Ajeno", precio=1)
        self.assertEqual(list(Producto.objects.for_gimnasio(self.gimnasio)), [self.producto])

    def test_categoria(self):
        propia = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        CategoriaProducto.objects.create(gimnasio=self.otro, nombre="Ropa")
        self.assertEqual(list(CategoriaProducto.objects.for_gimnasio(self.gimnasio)), [propia])

    def test_configuracion(self):
        propia = obtener_configuracion(self.gimnasio)
        obtener_configuracion(self.otro)
        self.assertEqual(list(ConfiguracionTienda.objects.for_gimnasio(self.gimnasio)), [propia])

    def test_pedido(self):
        ana = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        bruno = Alumno.objects.create(gimnasio=self.otro, nombre="Bruno", apellido="P")
        propio = Pedido.objects.create(gimnasio=self.gimnasio, alumno=ana, total=1)
        Pedido.objects.create(gimnasio=self.otro, alumno=bruno, total=1)
        self.assertEqual(list(Pedido.objects.for_gimnasio(self.gimnasio)), [propio])


class GimnasioTiendaTests(TestCase):
    def test_puede_usar_tienda(self):
        real = Gimnasio.objects.create(nombre="R", slug="r")
        demo = Gimnasio.objects.create(nombre="D", slug="d", es_demo=True)
        self.assertFalse(real.puede_usar_tienda)
        self.assertTrue(demo.puede_usar_tienda)
        real.tienda_habilitada = True
        self.assertTrue(real.puede_usar_tienda)


class TiendaNoSeAutohabilitaTests(TestCase):
    """Hermano de `test_el_staff_no_puede_habilitarsela_desde_su_panel`
    (exportación): el add-on lo prende el dueño del producto, no el staff."""

    def test_el_form_del_gimnasio_no_expone_tienda_habilitada(self):
        from tenants.forms import GimnasioForm

        self.assertNotIn("tienda_habilitada", GimnasioForm.Meta.fields)
        self.assertNotIn("tienda_habilitada", GimnasioForm().fields)

    def test_un_post_a_mi_gimnasio_no_la_prende(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        from tenants.models import Perfil

        gimnasio = Gimnasio.objects.create(nombre="Cliente", slug="cliente")
        staff = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(usuario=staff, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="dueno", password="clave-123456")

        self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Cliente",
                "tipo_publico": Gimnasio.TipoPublico.MIXTO,
                "paleta": Gimnasio.Paleta.BOSQUE,
                "tipografia": Gimnasio.Tipografia.PLUS_JAKARTA,
                "fondo_tipo": Gimnasio.FondoTipo.COLOR,
                "tienda_habilitada": "on",
            },
        )

        gimnasio.refresh_from_db()
        self.assertFalse(gimnasio.tienda_habilitada)
