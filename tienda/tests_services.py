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
from tienda.testing import PNG, crear_producto

AHORA = timezone.make_aware(datetime(2026, 10, 20, 12, 0))



class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.alumno2 = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        self.producto = crear_producto(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        self.m = Variante.objects.create(producto=self.producto, nombre="M", stock=3)
        self.l = Variante.objects.create(producto=self.producto, nombre="L", stock=1)
        ajeno = crear_producto(gimnasio=self.otro, nombre="Ajena", precio=1)
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


class TransicionesDelStaffTests(_Base):
    def test_marcar_pagado_desde_pendiente_o_revision(self):
        a = self.comprar({self.m.pk: 1})
        services.marcar_pagado(a, ahora=AHORA)
        a.refresh_from_db()
        self.assertEqual((a.estado, a.pagado_en), (Pedido.Estado.PAGADO, AHORA))
        self.assertEqual(self.stock(self.m), 2)  # sigue retenido

    def test_rechazar_vuelve_a_esperar_el_pago_sin_devolver_stock(self):
        pedido = self.comprar({self.m.pk: 2})
        self.subir(pedido)
        despues = AHORA + timedelta(hours=3)
        services.rechazar_comprobante(pedido, motivo="No se lee el monto", ahora=despues)
        pedido.refresh_from_db()
        self.assertEqual(
            (pedido.estado, pedido.motivo), (Pedido.Estado.PENDIENTE_PAGO, "No se lee el monto")
        )
        self.assertEqual(pedido.reservado_hasta, despues + timedelta(hours=24))
        self.assertEqual(self.stock(self.m), 1)  # sigue reservado

    def test_el_plazo_nuevo_sale_de_la_configuracion_del_gimnasio(self):
        config = obtener_configuracion(self.gimnasio)
        config.horas_reserva_transferencia = 48
        config.save()
        pedido = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.reservado_hasta, AHORA + timedelta(hours=48))

    def test_rechazar_dos_veces_falla_la_segunda(self):
        pedido = self.comprar({self.m.pk: 2})
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        with self.assertRaises(ErrorDeTienda):
            services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        self.assertEqual(self.stock(self.m), 1)

    def test_cancelar_un_pagado_lo_devuelve_y_libera(self):
        pedido = self.comprar({self.m.pk: 2})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.cancelar_por_staff(pedido, motivo="Se arrepintió", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.DEVUELTO)
        self.assertEqual(self.stock(self.m), 3)

    def test_entregar_solo_desde_pagado(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda):
            services.entregar(pedido, ahora=AHORA)
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        self.assertEqual(self.stock(self.m), 2)  # entregado: no vuelve

    def test_motivo_largo_se_rechaza(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            services.cancelar_por_staff(pedido, motivo="x" * 301, ahora=AHORA)
        self.assertEqual(e.exception.motivo, "motivo_largo")


class ReintentoDelComprobanteTests(_Base):
    def rechazado(self):
        pedido = self.comprar({self.m.pk: 1})
        self.subir(pedido)
        pedido.refresh_from_db()
        self.viejo = pedido.comprobante.name
        services.rechazar_comprobante(pedido, motivo="Borroso", ahora=AHORA)
        return pedido

    def test_resubir_reemplaza_borra_el_viejo_y_limpia_el_motivo(self):
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        self.subir(pedido, ahora=AHORA + timedelta(hours=1))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)
        self.assertNotEqual(pedido.comprobante.name, self.viejo)
        self.assertEqual(pedido.motivo, "")
        self.assertEqual(pedido.comprobante_subido_en, AHORA + timedelta(hours=1))
        self.assertFalse(default_storage.exists(self.viejo))
        self.assertTrue(default_storage.exists(pedido.comprobante.name))

    def test_resubir_tarde_no_borra_el_comprobante_anterior(self):
        """El viejo es lo único que el staff tiene para mirar: se borra sólo
        si el nuevo entró."""
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        tarde = pedido.reservado_hasta + timedelta(minutes=3)
        with self.assertRaises(ErrorDeTienda):
            self.subir(pedido, ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.comprobante.name, self.viejo)
        self.assertTrue(default_storage.exists(self.viejo))
        self.assertEqual(pedido.motivo, "Borroso")

    def test_si_vence_despues_del_rechazo_no_queda_el_motivo(self):
        pedido = self.rechazado()
        services.vencer_pedidos(ahora=pedido.reservado_hasta + timedelta(days=1))
        pedido.refresh_from_db()
        self.assertEqual((pedido.estado, pedido.motivo), (Pedido.Estado.VENCIDO, ""))

    def test_si_el_alumno_cancela_despues_del_rechazo_no_queda_el_motivo(self):
        pedido = self.rechazado()
        services.cancelar_por_alumno(pedido, alumno=self.alumno, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual((pedido.estado, pedido.motivo), (Pedido.Estado.CANCELADO, ""))

    def test_con_la_instancia_vieja_borra_el_archivo_que_dice_la_base(self):
        """Dos subidas casi juntas: la instancia del request trae un nombre
        viejo. Se borra el que la base tenía al momento del UPDATE."""
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        otro = default_storage.save("tienda/comprobantes/otro.png", ContentFile(PNG))
        Pedido.objects.filter(pk=pedido.pk).update(comprobante=otro)
        self.subir(pedido, ahora=AHORA + timedelta(hours=1))  # `pedido` todavía dice `self.viejo`
        self.assertFalse(default_storage.exists(otro))

    def test_si_no_resube_vence_y_devuelve_el_stock_una_sola_vez(self):
        pedido = self.rechazado()
        despues = pedido.reservado_hasta + timedelta(days=1)
        self.assertEqual(services.vencer_pedidos(ahora=despues), [pedido.pk])
        services.vencer_pedidos(ahora=despues)
        self.assertEqual(self.stock(self.m), 3)

    def test_si_otro_proceso_lo_movio_es_estado_invalido_no_plazo_vencido(self):
        """El staff lo marcó pagado mientras el alumno subía el archivo: el
        cartel no puede decir que venció el plazo."""
        pedido = self.comprar({self.m.pk: 1})
        copia = Pedido.objects.get(pk=pedido.pk)  # el request trae PENDIENTE_PAGO
        services.marcar_pagado(pedido, ahora=AHORA)
        with self.assertRaises(ErrorDeTienda) as e:
            self.subir(copia, ahora=AHORA)
        self.assertEqual(e.exception.motivo, "estado_invalido")
        pedido.refresh_from_db()
        self.assertEqual(pedido.comprobante.name, "")


class CancelacionTests(_Base):
    def test_el_alumno_cancela_su_pendiente(self):
        pedido = self.comprar({self.m.pk: 2})
        services.cancelar_por_alumno(pedido, alumno=self.alumno, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.CANCELADO)
        self.assertEqual(self.stock(self.m), 3)

    def test_mientras_no_lo_retira_siempre_puede_pedir(self):
        """Art. 34: el plazo corre desde la entrega o el contrato, lo último.
        Sin retirar, todavía no hubo entrega."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        lejos = timezone.localdate(AHORA) + timedelta(days=60)
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=lejos))

    def test_el_plazo_se_cuenta_desde_el_retiro(self):
        """Pagó el 20/10 y retiró 15 días después: contar desde el pago lo
        dejaba sin derecho el mismo día del retiro."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        retiro = AHORA + timedelta(days=15)
        services.entregar(pedido, ahora=retiro)
        pedido.refresh_from_db()
        dia_retiro = timezone.localdate(retiro)
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro))
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro + timedelta(days=10)))
        self.assertFalse(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro + timedelta(days=11)))
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=retiro + timedelta(days=10))
        with self.assertRaises(ErrorDeTienda):  # no se pide dos veces
            services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=retiro + timedelta(days=10))

    def test_fuera_de_plazo_explica_desde_cuando_se_cuenta(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        with self.assertRaises(ErrorDeTienda) as e:
            services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA + timedelta(days=11))
        self.assertIn("desde que retiraste", e.exception.mensaje)

    def test_el_filtro_en_la_base_coincide_con_la_regla(self):
        config = obtener_configuracion(self.gimnasio)
        config.max_pedidos_activos = 10
        config.save()
        Variante.objects.filter(pk=self.m.pk).update(stock=20)
        hoy = timezone.localdate(AHORA) + timedelta(days=20)  # 9/11

        pagado = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pagado, ahora=AHORA)
        justo = self.comprar({self.m.pk: 1})  # retirado el 30/10: 30/10 + 10 = 9/11
        services.marcar_pagado(justo, ahora=AHORA)
        services.entregar(justo, ahora=AHORA + timedelta(days=10))
        tarde = self.comprar({self.m.pk: 1})  # retirado el 29/10: ya pasó
        services.marcar_pagado(tarde, ahora=AHORA)
        services.entregar(tarde, ahora=AHORA + timedelta(days=9))
        self.comprar({self.m.pk: 1})  # pendiente
        ya_pedida = self.comprar({self.m.pk: 1})
        services.marcar_pagado(ya_pedida, ahora=AHORA)
        services.pedir_cancelacion(ya_pedida, alumno=self.alumno, ahora=AHORA)

        regla = {p.pk for p in Pedido.objects.all() if services.puede_pedir_cancelacion(p, hoy=hoy)}
        base = set(
            Pedido.objects.filter(services.q_puede_pedir_cancelacion(hoy=hoy)).values_list("pk", flat=True)
        )
        self.assertEqual(regla, {pagado.pk, justo.pk})
        self.assertEqual(base, regla)

    def test_el_plazo_cuenta_en_fecha_argentina_no_utc(self):
        """Retirado el 20/10 a las 22:30 (Argentina) = 21/10 01:30 UTC. El día
        10 es el 30/10, no el 31/10. Con `now().date()` daría un día de más."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        noche = timezone.make_aware(datetime(2026, 10, 20, 22, 30))
        services.entregar(pedido, ahora=noche)
        pedido.refresh_from_db()
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=datetime(2026, 10, 30).date()))
        self.assertFalse(services.puede_pedir_cancelacion(pedido, hoy=datetime(2026, 10, 31).date()))

    def test_aceptar_cancelacion_de_un_pagado_libera_stock(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=True, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.DEVUELTO)
        self.assertEqual(self.stock(self.m), 3)

    def test_aceptar_cancelacion_de_un_entregado_no_toca_stock(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=True, ahora=AHORA)
        self.assertEqual(self.stock(self.m), 2)

    def test_rechazar_la_cancelacion_sin_motivo_no_pisa_el_que_habia(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        Pedido.objects.filter(pk=pedido.pk).update(motivo="Nota previa")
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=False, motivo="", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.motivo, "Nota previa")
        self.assertIsNotNone(pedido.cancelacion_resuelta_en)

    def test_marcar_pagado_limpia_el_motivo_de_un_rechazo_viejo(self):
        pedido = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=pedido.pk).update(motivo="No se lee")
        services.marcar_pagado(pedido, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.motivo, "")

    def test_cancelar_por_staff_cierra_la_cancelacion_pedida(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.cancelar_por_staff(pedido, motivo="Devuelto", ahora=AHORA)
        self.assertEqual(services.contadores_panel(self.gimnasio)["cancelaciones"], 0)
        # El contador ya da 0 porque DEVUELTO no está en Q_CANCELACION_PENDIENTE:
        # lo que fija el UPDATE de la resolución es este campo.
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.cancelacion_resuelta_en)

    def test_rechazar_cancelacion_deja_el_pedido_y_la_cierra(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=False, motivo="Ya usado", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PAGADO)
        self.assertIsNotNone(pedido.cancelacion_resuelta_en)


class TieneVentasTests(_Base):
    def test_tiene_ventas(self):
        self.assertFalse(services.tiene_ventas(self.producto))
        self.comprar({self.m.pk: 1})
        self.assertTrue(services.tiene_ventas(self.producto))


class ContadoresTests(_Base):
    def test_una_sola_query(self):
        a = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=a.pk).update(estado=Pedido.Estado.EN_REVISION)
        b = self.comprar({self.l.pk: 1}, alumno=self.alumno2)
        services.marcar_pagado(b, ahora=AHORA)
        with self.assertNumQueries(1):
            c = services.contadores_panel(self.gimnasio)
        self.assertEqual(
            c, {"a_revisar": 1, "a_revisar_mp": 0, "a_entregar": 1, "cancelaciones": 0, "vivos": 2}
        )
