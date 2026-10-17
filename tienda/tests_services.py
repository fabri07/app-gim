"""Reglas de negocio de la tienda. Los nombres dicen el comportamiento; cada
test de regresión se confirmó rojo sin su arreglo (ver el plan)."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda import services
from tienda.models import ItemPedido, Pedido, Producto, Variante, obtener_configuracion
from tienda.services import ErrorDeTienda
from tienda.testing import PNG

AHORA = timezone.make_aware(datetime(2026, 10, 20, 12, 0))



class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.alumno2 = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        self.m = Variante.objects.create(producto=self.producto, nombre="M", stock=3)
        self.l = Variante.objects.create(producto=self.producto, nombre="L", stock=1)
        ajeno = Producto.objects.create(gimnasio=self.otro, nombre="Ajena", precio=1)
        self.ajena = Variante.objects.create(producto=ajeno, stock=10)

    def comprar(self, carrito, alumno=None, ahora=AHORA):
        return services.crear_pedido(
            gimnasio=self.gimnasio, alumno=alumno or self.alumno,
            carrito=carrito, suplantando=False, ahora=ahora,
        )

    def stock(self, variante):
        variante.refresh_from_db()
        return variante.stock

    def subir(self, pedido, *, ahora=AHORA, alumno=None):
        services.subir_comprobante(
            pedido, alumno=alumno or self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"), ahora=ahora,
        )


class CarritoTests(_Base):
    def setUp(self):
        super().setUp()
        self.session = SessionStore()

    def test_agregar_suma_y_respeta_el_tope(self):
        services.agregar_al_carrito(self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=2)
        services.agregar_al_carrito(self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=1)
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 3})
        with self.assertRaises(ErrorDeTienda) as e:
            services.agregar_al_carrito(
                self.session, gimnasio=self.gimnasio, variante_id=self.m.pk,
                cantidad=services.MAX_UNIDADES_POR_ITEM,
            )
        self.assertEqual(e.exception.motivo, "cantidad_invalida")

    def test_cantidad_cero_o_negativa_se_rechaza(self):
        for cantidad in (0, -5):
            with self.assertRaises(ErrorDeTienda):
                services.agregar_al_carrito(
                    self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=cantidad
                )
        self.assertEqual(services.leer_carrito(self.session), {})

    def test_no_se_agrega_la_variante_de_otro_gimnasio(self):
        with self.assertRaises(ErrorDeTienda) as e:
            services.agregar_al_carrito(
                self.session, gimnasio=self.gimnasio, variante_id=self.ajena.pk, cantidad=1
            )
        self.assertEqual(e.exception.motivo, "producto_no_disponible")

    def test_leer_tolera_basura_en_la_sesion(self):
        self.session[services.CLAVE_CARRITO] = {"x": "y", str(self.m.pk): "2", "9": -3}
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 2})

    def test_lineas_descarta_variantes_ajenas_y_archivadas(self):
        self.l.activa = False
        self.l.save()
        self.session[services.CLAVE_CARRITO] = {
            str(self.m.pk): 2, str(self.l.pk): 1, str(self.ajena.pk): 1,
        }
        lineas, total = services.lineas_del_carrito(self.session, gimnasio=self.gimnasio)
        self.assertEqual([l.variante.pk for l in lineas], [self.m.pk])
        self.assertEqual(total, Decimal("30000"))
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 2})


class CrearPedidoTests(_Base):
    def test_reserva_descuenta_stock_y_guarda_snapshot(self):
        pedido = self.comprar({self.m.pk: 2, self.l.pk: 1})
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)
        self.assertEqual(pedido.total, Decimal("45000"))
        self.assertEqual(pedido.reservado_hasta, AHORA + timedelta(hours=24))
        self.assertEqual((self.stock(self.m), self.stock(self.l)), (1, 0))
        nombres = sorted(pedido.items.values_list("nombre", "cantidad"))
        self.assertEqual(nombres, [("Remera · L", 1), ("Remera · M", 2)])

    def test_la_segunda_compra_de_la_ultima_unidad_falla_sin_tocar_nada(self):
        self.comprar({self.l.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1, self.l.pk: 1}, alumno=self.alumno2)
        self.assertEqual(e.exception.motivo, "sin_stock")
        self.assertIn("Remera · L", e.exception.mensaje)
        # Todo o nada: la M no quedó descontada por la compra fallida.
        self.assertEqual(self.stock(self.m), 3)
        self.assertEqual(Pedido.objects.filter(alumno=self.alumno2).count(), 0)

    def test_variante_de_otro_gimnasio_no_toca_su_stock(self):
        with self.assertRaises(ErrorDeTienda):
            self.comprar({self.ajena.pk: 1})
        self.assertEqual(self.stock(self.ajena), 10)

    def test_cantidad_negativa_no_suma_stock(self):
        # El motivo importa: sin la validación, la CHECK de `ItemPedido.cantidad`
        # también aborta (como «sin_stock») y taparía el test.
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: -5})
        self.assertEqual(e.exception.motivo, "cantidad_invalida")
        self.assertEqual(self.stock(self.m), 3)

    def test_carrito_vacio(self):
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({})
        self.assertEqual(e.exception.motivo, "carrito_vacio")

    def test_producto_despublicado_no_se_vende(self):
        self.producto.publicado = False
        self.producto.save()
        with self.assertRaises(ErrorDeTienda):
            self.comprar({self.m.pk: 1})
        self.assertEqual(self.stock(self.m), 3)

    def test_tope_de_pedidos_en_curso(self):
        obtener_configuracion(self.gimnasio)  # default 2
        self.comprar({self.m.pk: 1})
        self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1})
        self.assertEqual(e.exception.motivo, "tope_pedidos")

    def test_no_se_compra_suplantando(self):
        with self.assertRaises(ErrorDeTienda) as e:
            services.crear_pedido(
                gimnasio=self.gimnasio, alumno=self.alumno,
                carrito={self.m.pk: 1}, suplantando=True, ahora=AHORA,
            )
        self.assertEqual(e.exception.motivo, "suplantando")
        self.assertEqual(self.stock(self.m), 3)

    def test_tienda_sin_add_on(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        self.gimnasio.refresh_from_db()
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1})
        self.assertEqual(e.exception.motivo, "tienda_cerrada")


class VencimientoTests(_Base):
    def test_vence_despues_de_la_gracia_y_devuelve_el_stock(self):
        pedido = self.comprar({self.m.pk: 2})
        limite = pedido.reservado_hasta + services.GRACIA_COMPROBANTE
        self.assertEqual(services.vencer_pedidos(ahora=limite), [])
        self.assertEqual(services.vencer_pedidos(ahora=limite + timedelta(seconds=1)), [pedido.pk])
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.VENCIDO)
        self.assertEqual(self.stock(self.m), 3)

    def test_vencer_dos_veces_no_devuelve_dos_veces(self):
        self.comprar({self.m.pk: 2})
        despues = AHORA + timedelta(days=2)
        services.vencer_pedidos(ahora=despues)
        services.vencer_pedidos(ahora=despues)
        self.assertEqual(self.stock(self.m), 3)

    def test_en_revision_no_vence(self):
        pedido = self.comprar({self.m.pk: 1})
        services.subir_comprobante(
            pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"), ahora=AHORA,
        )
        services.vencer_pedidos(ahora=AHORA + timedelta(days=30))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_acotado_a_un_gimnasio(self):
        pedido = self.comprar({self.m.pk: 1})
        self.assertEqual(services.vencer_pedidos(gimnasio=self.otro, ahora=AHORA + timedelta(days=2)), [])
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_si_otro_proceso_movio_un_pedido_no_libera_su_stock(self):
        """La carrera de SQLite (donde `select_for_update` no hace nada): el
        SELECT vio dos pedidos pendientes, pero antes del UPDATE el alumno de
        `b` subió el comprobante (commiteado por otro proceso). Se simula con
        un SELECT viejo; un UPDATE dentro del savepoint se desharía con el
        rollback de `_Carrera` y el test no probaría nada."""
        a = self.comprar({self.m.pk: 1})
        b = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=b.pk).update(estado=Pedido.Estado.EN_REVISION)
        with patch.object(services, "_candidatos_a_vencer", lambda *args, **kwargs: [a.pk, b.pk]):
            vencidos = services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        self.assertEqual(vencidos, [a.pk])
        self.assertEqual(self.stock(self.m), 2)  # 3 - 2 + 1 (sólo el de `a`)

    def test_el_costo_no_crece_con_la_cantidad_de_pedidos(self):
        Variante.objects.filter(pk=self.m.pk).update(stock=100)
        self.comprar({self.m.pk: 1})
        with CaptureQueriesContext(connection) as uno:
            services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        config = obtener_configuracion(self.gimnasio)
        config.max_pedidos_activos = 10
        config.save()
        for _ in range(8):
            self.comprar({self.m.pk: 1})
        with CaptureQueriesContext(connection) as ocho:
            services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        self.assertEqual(len(uno), len(ocho))


class ComprobanteTests(_Base):
    def archivo(self):
        return SimpleUploadedFile("c.png", PNG, content_type="image/png")

    def test_pasa_a_revision(self):
        pedido = self.comprar({self.m.pk: 1})
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)
        self.assertTrue(pedido.comprobante.name.startswith("tienda/comprobantes/"))
        self.assertEqual(pedido.comprobante_subido_en, AHORA)

    def test_dentro_de_la_gracia_se_acepta(self):
        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=1)
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_el_vencimiento_perezoso_respeta_la_gracia(self):
        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=1)
        services.vencer_pedidos(ahora=tarde)  # otro alumno abrió el catálogo
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_fuera_de_plazo_se_rechaza_y_no_deja_el_archivo(self):
        from django.core.files.storage import default_storage

        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=3)
        with patch.object(default_storage, "delete", wraps=default_storage.delete) as borrar:
            with self.assertRaises(ErrorDeTienda) as e:
                services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        self.assertEqual(e.exception.motivo, "plazo_vencido")
        borrar.assert_called_once()

    def test_otro_alumno_no_sube(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda):
            services.subir_comprobante(pedido, alumno=self.alumno2, archivo=self.archivo(), ahora=AHORA)


class StockTests(_Base):
    def test_ajuste_positivo_y_negativo(self):
        services.ajustar_stock(self.m, 5)
        self.assertEqual(self.stock(self.m), 8)
        services.ajustar_stock(self.m, -8)
        self.assertEqual(self.stock(self.m), 0)
        with self.assertRaises(ErrorDeTienda):
            services.ajustar_stock(self.m, -1)

    def test_ajuste_concurrente_con_una_reserva_no_pisa(self):
        self.comprar({self.m.pk: 2})  # el staff había visto 3
        services.ajustar_stock(self.m, 1)
        self.assertEqual(self.stock(self.m), 2)

    def test_reservado_por_variante(self):
        self.comprar({self.m.pk: 2})
        pagado = self.comprar({self.l.pk: 1}, alumno=self.alumno2)
        # `marcar_pagado` llega en la Tarea 3: acá alcanza con el estado.
        Pedido.objects.filter(pk=pagado.pk).update(estado=Pedido.Estado.PAGADO)
        self.assertEqual(services.reservado_por_variante(self.gimnasio), {self.m.pk: 2, self.l.pk: 1})


class MensajesTests(TestCase):
    def test_cada_motivo_tiene_su_mensaje(self):
        import inspect

        fuente = inspect.getsource(services)
        import re

        usados = set(re.findall(r'ErrorDeTienda\("(\w+)"', fuente))
        self.assertTrue(usados)
        self.assertEqual(usados - set(services.MENSAJES), set())
