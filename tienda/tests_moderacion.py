"""Moderación de la tienda, Tarea 1: los estados, `q_vendible` y la migración
que deja aprobado todo lo que ya estaba a la venta."""

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from tenants.models import Gimnasio
from tienda.models import FotoProducto, Producto, Variante, q_vendible
from tienda.testing import crear_foto, crear_producto


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")


class EstadosTests(_Base):
    def test_un_producto_nuevo_nace_pendiente(self):
        producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("10")
        )
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, "pendiente")
        self.assertIs(producto.cambio_pendiente, False)
        self.assertEqual(producto.moderacion_motivo, "")
        self.assertIsNone(producto.moderacion_desde)

    def test_una_foto_nueva_nace_sin_aprobar(self):
        producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)
        foto = FotoProducto.objects.create(producto=producto, imagen="tienda/productos/a.png")
        foto.refresh_from_db()
        self.assertIs(foto.aprobada, False)

    def test_los_helpers_de_test_nacen_aprobados(self):
        producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)
        self.assertEqual(producto.moderacion, Producto.Moderacion.APROBADO)
        foto = crear_foto(producto=producto, imagen="tienda/productos/a.png")
        self.assertIs(foto.aprobada, True)
        otro = crear_producto(
            gimnasio=self.gimnasio, nombre="Q", precio=1, moderacion=Producto.Moderacion.BAJADO
        )
        self.assertEqual(otro.moderacion, "bajado")

    def test_q_vendible_exige_publicado_y_aprobado(self):
        A, P = Producto.Moderacion.APROBADO, Producto.Moderacion.PENDIENTE
        productos = {}
        for nombre, publicado, mod in (
            ("ok", True, A), ("oculto", False, A), ("pend", True, P), ("pend_oculto", False, P),
        ):
            productos[nombre] = crear_producto(
                gimnasio=self.gimnasio, nombre=nombre, precio=1, publicado=publicado, moderacion=mod
            )
            Variante.objects.create(producto=productos[nombre], stock=1)
        self.assertEqual(
            list(Producto.objects.filter(q_vendible())), [productos["ok"]]
        )
        self.assertEqual(
            list(Variante.objects.filter(q_vendible("producto__")).values_list("producto_id", flat=True)),
            [productos["ok"].pk],
        )


class VentanaDelDeployModeracionTests(_Base):
    """El `migrate` corre mientras la versión vieja atiende: sus INSERT no
    nombran las columnas nuevas y tienen que caer en `pendiente`."""

    def test_un_insert_viejo_cae_en_pendiente(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tienda_producto (creado, modificado, gimnasio_id, nombre, "
                "descripcion, precio, foto, publicado, orden) VALUES (CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, %s, 'Viejo', '', 1, '', TRUE, 0)",
                [self.gimnasio.pk],
            )
            producto_id = Producto.objects.get(nombre="Viejo").pk
            cursor.execute(
                "INSERT INTO tienda_fotoproducto (producto_id, imagen, posicion, creado) "
                "VALUES (%s, 'tienda/productos/v.png', 0, CURRENT_TIMESTAMP)",
                [producto_id],
            )
        producto = Producto.objects.get(pk=producto_id)
        self.assertEqual(producto.moderacion, "pendiente")
        self.assertIs(producto.cambio_pendiente, False)
        self.assertEqual(producto.moderacion_motivo, "")
        self.assertIs(producto.fotos.get().aprobada, False)


class MigracionDeModeracionTests(TransactionTestCase):
    """`tienda/0009` deja aprobado lo que ya existía. Se migra de verdad: un
    fixture a mano no probaría que el `RunPython` corre sobre filas reales."""

    migrate_from = [("tienda", "0008_variantes_por_caracteristicas")]
    migrate_to = [("tienda", "0009_moderacion")]

    def _migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_lo_existente_queda_aprobado(self):
        apps = self._migrar(self.migrate_from)
        gimnasio = apps.get_model("tenants", "Gimnasio").objects.create(nombre="M", slug="m")
        producto = apps.get_model("tienda", "Producto").objects.create(
            gimnasio=gimnasio, nombre="Viejo", precio=1
        )
        foto = apps.get_model("tienda", "FotoProducto").objects.create(
            producto=producto, imagen="tienda/productos/v.png"
        )

        apps = self._migrar(self.migrate_to)

        producto = apps.get_model("tienda", "Producto").objects.get(pk=producto.pk)
        foto = apps.get_model("tienda", "FotoProducto").objects.get(pk=foto.pk)
        self.assertEqual(producto.moderacion, "aprobado")
        self.assertIs(foto.aprobada, True)
