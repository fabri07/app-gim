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


class MigracionDeLaFotoUnicaTests(_Base):
    """`tienda/0002` copia `Producto.foto` (columna que queda muerta, sin
    vaciar) a la galería como la foto principal. Se llama a la función de la
    migración con los modelos reales (molde: `rutinas/tests.py`, la de
    `0011_cerrar_rutinas_activas_duplicadas`)."""

    def _correr(self):
        from importlib import import_module

        from tienda.models import FotoProducto

        # `import_module`: el módulo arranca con un dígito.
        migracion = import_module("tienda.migrations.0002_fotoproducto")
        modelos = {"Producto": Producto, "FotoProducto": FotoProducto}

        class AppsFalso:
            def get_model(self, app, modelo):
                return modelos[modelo]

        migracion.copiar_foto_a_la_galeria(AppsFalso(), None)

    def test_copia_la_foto_como_principal_sin_vaciar_la_columna(self):
        Producto.objects.filter(pk=self.producto.pk).update(foto="tienda/productos/abc123.jpg")
        sin_foto = Producto.objects.create(gimnasio=self.otro, nombre="Remera", precio=Decimal("1"))

        self._correr()

        self.producto.refresh_from_db()
        foto = self.producto.foto_principal
        self.assertEqual(foto.imagen.name, "tienda/productos/abc123.jpg")
        self.assertEqual(foto.posicion, 0)
        self.assertEqual(self.producto.fotos.count(), 1)
        self.assertEqual(self.producto.foto.name, "tienda/productos/abc123.jpg")
        self.assertFalse(sin_foto.fotos.exists())


# --- Mercado Pago (B2) ------------------------------------------------------

from datetime import timedelta  # noqa: E402

from cryptography.fernet import Fernet  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from django.db.migrations.executor import MigrationExecutor  # noqa: E402
from django.test import TransactionTestCase, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from tienda.models import MARGEN_PREFERENCIA, CuentaMercadoPago  # noqa: E402


class CuentaMercadoPagoTests(_Base):
    def test_aislamiento_por_gimnasio(self):
        propia = CuentaMercadoPago.objects.create(gimnasio=self.gimnasio, mp_user_id="1")
        CuentaMercadoPago.objects.create(gimnasio=self.otro, mp_user_id="2")
        self.assertEqual(list(CuentaMercadoPago.objects.for_gimnasio(self.gimnasio)), [propia])

    def test_una_por_gimnasio(self):
        CuentaMercadoPago.objects.create(gimnasio=self.gimnasio)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CuentaMercadoPago.objects.create(gimnasio=self.gimnasio)

    def test_uuid_webhook_unico_y_estado_por_defecto(self):
        a = CuentaMercadoPago.objects.create(gimnasio=self.gimnasio)
        b = CuentaMercadoPago.objects.create(gimnasio=self.otro)
        self.assertNotEqual(a.uuid_webhook, b.uuid_webhook)
        self.assertEqual(a.estado, CuentaMercadoPago.Estado.CONECTADA)

    def test_los_tokens_se_guardan_cifrados_y_se_leen_en_claro(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            cuenta = CuentaMercadoPago.objects.create(
                gimnasio=self.gimnasio, access_token="APP_USR-secreto", refresh_token="TG-secreto",
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT access_token FROM tienda_cuentamercadopago")
                crudo = cursor.fetchone()[0]
            self.assertNotIn("APP_USR-secreto", crudo)
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.access_token, "APP_USR-secreto")

    def test_tokens_vacios_no_necesitan_la_clave(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            cuenta = CuentaMercadoPago.objects.create(gimnasio=self.gimnasio)
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.access_token, "")


class PedidoMercadoPagoTests(_Base):
    def setUp(self):
        super().setUp()
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")

    def _pedido(self, **extra):
        return Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, total=1, **extra
        )

    def test_referencia_externa_unica_y_no_nula_al_crear(self):
        a, b = self._pedido(), self._pedido()
        self.assertIsNotNone(a.referencia_externa)
        self.assertNotEqual(a.referencia_externa, b.referencia_externa)

    def test_payment_id_unico_salvo_vacio(self):
        self._pedido()
        self._pedido()  # dos con `""`: permitido
        self._pedido(payment_id="123")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pedido(payment_id="123")

    def test_un_insert_que_no_conoce_las_columnas_nuevas_funciona(self):
        """El código viejo sigue atendiendo durante el `migrate`: su INSERT no
        nombra ninguna columna de MP ni `acepta_*`/`minutos_reserva_mp`."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tienda_configuraciontienda (creado, modificado, gimnasio_id, "
                "publicada, horas_reserva_transferencia, max_pedidos_activos, instrucciones_retiro) "
                "VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, 0, 24, 2, '')",
                [self.gimnasio.pk],
            )
            cursor.execute(
                "INSERT INTO tienda_pedido (creado, modificado, gimnasio_id, alumno_id, medio, "
                "estado, total, motivo, comprobante) VALUES (CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, %s, %s, 'transferencia', 'pendiente_pago', 1, '', '')",
                [self.gimnasio.pk, self.alumno.pk],
            )
        config = ConfiguracionTienda.objects.get(gimnasio=self.gimnasio)
        self.assertTrue(config.acepta_transferencia)
        self.assertFalse(config.acepta_mercadopago)
        self.assertEqual(config.minutos_reserva_mp, 30)
        pedido = Pedido.objects.get(gimnasio=self.gimnasio)
        self.assertIsNone(pedido.referencia_externa)
        self.assertEqual((pedido.payment_id, pedido.preference_id, pedido.estado_mp), ("", "", ""))
        self.assertFalse(pedido.revision_mp_pendiente)

    def test_revisar_tiene_badge_y_no_retiene_stock(self):
        from tienda.models import _BADGE_POR_ESTADO, ESTADOS_QUE_RETIENEN

        self.assertEqual(self._pedido(estado=Pedido.Estado.REVISAR).badge_clase, "badge--alerta")
        self.assertEqual(set(_BADGE_POR_ESTADO), set(Pedido.Estado.values))
        self.assertNotIn(Pedido.Estado.REVISAR, ESTADOS_QUE_RETIENEN)

    def test_vence_preferencia_es_reservado_hasta_menos_el_margen(self):
        hasta = timezone.now() + timedelta(minutes=30)
        self.assertEqual(self._pedido(reservado_hasta=hasta).vence_preferencia, hasta - MARGEN_PREFERENCIA)
        self.assertEqual(MARGEN_PREFERENCIA, timedelta(minutes=10))
        self.assertIsNone(self._pedido().vence_preferencia)

    def test_el_admin_no_deja_editar_los_campos_de_mp(self):
        from django.contrib import admin

        readonly = admin.site._registry[Pedido].readonly_fields
        for campo in ("payment_id", "estado_mp", "respuesta_mp", "referencia_externa",
                      "preference_id", "init_point", "revision_mp_pendiente"):
            self.assertIn(campo, readonly)


class AdminCuentaMercadoPagoTests(_Base):
    def test_el_admin_no_muestra_los_tokens(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            cuenta = CuentaMercadoPago.objects.create(
                gimnasio=self.gimnasio, access_token="APP_USR-no-debe-verse",
                refresh_token="TG-no-debe-verse",
            )
            User.objects.create_superuser("root", "r@x.com", "clave-123456")
            self.client.login(username="root", password="clave-123456")
            for url in (
                reverse("admin:tienda_cuentamercadopago_change", args=[cuenta.pk]),
                reverse("admin:tienda_cuentamercadopago_changelist"),
            ):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertNotContains(respuesta, "no-debe-verse")
            # Guardar desde el admin no toca los tokens.
            respuesta = self.client.post(
                reverse("admin:tienda_cuentamercadopago_change", args=[cuenta.pk]), {}
            )
            self.assertIn(respuesta.status_code, (200, 302))
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.access_token, "APP_USR-no-debe-verse")


class MigracionDeReferenciaExternaTests(TransactionTestCase):
    """`tienda/0004` llena `referencia_externa` de los pedidos que ya existían
    (cada uno la suya) y `0005` la vuelve única. Se migra de verdad hacia atrás
    y adelante: un fixture a mano no probaría que el AlterField a `unique`
    soporta los datos que quedaron."""

    migrate_from = [("tienda", "0002_fotoproducto")]
    migrate_to = [("tienda", "0005_pedido_referencia_externa_unica")]

    def _migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        self._migrar(self.migrate_to)  # deja la base como la espera el resto
        super().tearDown()

    def test_los_pedidos_existentes_quedan_con_referencia_unica_y_no_nula(self):
        apps_viejas = self._migrar(self.migrate_from)
        Gimnasio_ = apps_viejas.get_model("tenants", "Gimnasio")
        Alumno_ = apps_viejas.get_model("alumnos", "Alumno")
        Pedido_ = apps_viejas.get_model("tienda", "Pedido")
        gimnasio = Gimnasio_.objects.create(nombre="M", slug="m")
        alumno = Alumno_.objects.create(gimnasio=gimnasio, nombre="A", apellido="B")
        for _ in range(3):
            Pedido_.objects.create(gimnasio=gimnasio, alumno=alumno, total=1)

        apps_nuevas = self._migrar(self.migrate_to)

        referencias = list(
            apps_nuevas.get_model("tienda", "Pedido").objects.values_list("referencia_externa", flat=True)
        )
        self.assertEqual(len(referencias), 3)
        self.assertNotIn(None, referencias)
        self.assertEqual(len(set(referencias)), 3)

    def test_la_migracion_de_datos_se_puede_revertir(self):
        self._migrar([("tienda", "0003_mercadopago_modelos")])
        self._migrar(self.migrate_to)
