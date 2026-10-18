"""Staff y alumno alrededor de los pagos de Mercado Pago (B5): la pestaña,
los contadores y el panel «Revisar MP», las acciones de REVISAR («Entregar
igual», «Ya lo devolví», «Ya devolví el duplicado»), el aviso de devolver la
plata desde Mercado Pago, «Vendido este mes», el portal del alumno y los
textos de los avisos nuevos."""

import itertools
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import analitica, pagos_mp, services
from tienda.models import ConfiguracionTienda, CuentaMercadoPago, Pedido, Producto, Variante

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
E = Pedido.Estado
AL_ALUMNO = "notificaciones.services.notificar_pedido_al_alumno"
#: `payment_id` único por pedido (constraint parcial).
_IDS = itertools.count(1000)


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.usuario_alumno = usuario
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil,
            estado=Alumno.Estado.ACTIVO,
        )
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        self.variante = Variante.objects.create(producto=self.producto, nombre="M", stock=3)
        self.client.force_login(self.staff)

    def pedido(self, estado=E.REVISAR, **extra):
        datos = dict(
            gimnasio=self.gimnasio, alumno=self.alumno, total=Decimal("15000"),
            medio=Pedido.Medio.MERCADOPAGO, estado=estado, preference_id="pref-1",
            payment_id=str(next(_IDS)), estado_mp="approved",
            reservado_hasta=timezone.now() - timedelta(hours=1),
        )
        datos.update(extra)
        pedido = Pedido.objects.create(**datos)
        pedido.items.create(
            variante=self.variante, nombre="Remera · M",
            precio_unitario=Decimal("15000"), cantidad=1,
        )
        return pedido

    def con_duplicados(self, *ids, estado=E.PAGADO):
        ahora = timezone.now().isoformat()
        return self.pedido(
            estado,
            respuesta_mp={"id": 1, "pagos_adicionales": [
                {"payment_id": i, "status": "approved", "detectado": ahora} for i in ids
            ]},
            revision_mp_pendiente=True,
        )

    def accion(self, pedido, accion, **datos):
        with mock.patch(AL_ALUMNO) as push, self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                reverse("tienda:pedido_accion", args=[pedido.pk]),
                {"accion": accion, **datos}, follow=True,
            )
        self.push_alumno = [c.args[1] for c in push.call_args_list]
        pedido.refresh_from_db()
        return respuesta

    def stock(self):
        self.variante.refresh_from_db()
        return self.variante.stock


class PestanaYContadoresTests(_Base):
    def test_contadores_una_query_con_revisar_mp_y_vivos_suma_revisar(self):
        self.pedido(E.REVISAR)
        self.con_duplicados("2")
        self.pedido(E.PENDIENTE_PAGO, payment_id="")
        with self.assertNumQueries(1):
            c = services.contadores_panel(self.gimnasio)
        self.assertEqual(c["a_revisar_mp"], 2)
        self.assertEqual(c["vivos"], 3)  # REVISAR + PAGADO + PENDIENTE
        self.assertEqual(c["a_revisar"], 0)

    def test_filtro_revisar_mp_muestra_revisar_y_duplicados(self):
        revisar = self.pedido(E.REVISAR)
        duplicado = self.con_duplicados("2")
        normal = self.pedido(E.PAGADO, payment_id="9")
        with override_settings(**MP):
            r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar_mp")
        ids = {p.pk for p in r.context["object_list"]}
        self.assertEqual(ids, {revisar.pk, duplicado.pk})
        self.assertNotIn(normal.pk, ids)
        self.assertContains(r, "Revisar MP (2)")

    def test_con_la_bandera_apagada_y_nada_pendiente_no_hay_pestana(self):
        r = self.client.get(reverse("tienda:pedidos"))
        self.assertNotContains(r, "Revisar MP")

    def test_con_la_bandera_apagada_pero_algo_pendiente_la_pestana_sigue(self):
        self.pedido(E.REVISAR)
        r = self.client.get(reverse("tienda:pedidos"))
        self.assertContains(r, "Revisar MP (1)")

    def test_el_panel_del_staff_lo_muestra(self):
        self.pedido(E.REVISAR)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "1 pago de Mercado Pago para revisar")
        self.assertContains(r, "?filtro=revisar_mp")

    def test_el_portal_del_alumno_cuenta_revisar_como_sin_cerrar(self):
        """Con la tienda sin publicar, el alumno con un pago a revisar igual ve
        «Mis pedidos»."""
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"publicada": False}
        )
        self.pedido(E.REVISAR)
        self.client.force_login(self.usuario_alumno)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Tus pedidos de la tienda")


class DetalleTests(_Base):
    def test_revisar_muestra_entregar_igual_y_ya_lo_devolvi(self):
        p = self.pedido(E.REVISAR)
        r = self.client.get(reverse("tienda:pedido_detalle", args=[p.pk]))
        self.assertContains(r, 'value="entregar_igual"')
        self.assertContains(r, 'value="ya_devuelto"')
        self.assertNotContains(r, 'value="cancelar"')

    def test_duplicado_muestra_su_boton(self):
        p = self.con_duplicados("2")
        r = self.client.get(reverse("tienda:pedido_detalle", args=[p.pk]))
        self.assertContains(r, "Cobrado dos veces")
        self.assertContains(r, 'value="duplicado_devuelto"')
        self.assertContains(r, 'name="payment_id" value="2"')

    def test_pagado_por_mp_avisa_devolver_desde_mercado_pago_al_cancelar(self):
        p = self.pedido(E.PAGADO, cancelacion_pedida_en=timezone.now())
        r = self.client.get(reverse("tienda:pedido_detalle", args=[p.pk]))
        self.assertContains(r, "Devolvé la plata desde tu cuenta de Mercado Pago", count=2)

    def test_transferencia_no_lo_menciona(self):
        p = self.pedido(E.PAGADO, medio=Pedido.Medio.TRANSFERENCIA, payment_id="")
        r = self.client.get(reverse("tienda:pedido_detalle", args=[p.pk]))
        self.assertNotContains(r, "Mercado Pago")


class EntregarIgualTests(_Base):
    def test_pide_el_stock_pone_pagado_en_y_cuenta_en_vendido_este_mes(self):
        p = self.pedido(E.REVISAR)
        self.accion(p, "entregar_igual")
        self.assertEqual(p.estado, E.ENTREGADO)
        self.assertIsNotNone(p.pagado_en)
        self.assertIsNotNone(p.entregado_en)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(analitica.resumen_del_mes(self.gimnasio)["pedidos"], 1)

    def test_conserva_el_pagado_en_que_ya_tenia(self):
        antes = timezone.now() - timedelta(hours=2)
        p = self.pedido(E.REVISAR, pagado_en=antes)
        services.entregar_igual(p)
        self.assertEqual(p.pagado_en, antes)

    def test_sin_stock_es_error_y_queda_en_revisar(self):
        p = self.pedido(E.REVISAR)
        Variante.objects.filter(pk=self.variante.pk).update(stock=0)
        r = self.accion(p, "entregar_igual")
        self.assertContains(r, "No queda stock suficiente")
        self.assertEqual(p.estado, E.REVISAR)
        self.assertIsNone(p.entregado_en)
        self.assertEqual(self.stock(), 0)

    def test_solo_desde_revisar(self):
        p = self.pedido(E.PAGADO)
        with self.assertRaises(services.ErrorDeTienda):
            services.entregar_igual(p)
        self.assertEqual(self.stock(), 3)


class YaLoDevolviTests(_Base):
    def test_revisar_pasa_a_devuelto_sin_stock_y_avisa_al_alumno(self):
        p = self.pedido(E.REVISAR)
        self.accion(p, "ya_devuelto")
        self.assertEqual(p.estado, E.DEVUELTO)
        self.assertEqual(self.stock(), 3)
        self.assertEqual(self.push_alumno, ["devuelto_mp"])

    def test_resuelve_un_arrepentimiento_pendiente(self):
        p = self.pedido(E.REVISAR, cancelacion_pedida_en=timezone.now())
        services.marcar_devuelto(p)
        self.assertIsNotNone(p.cancelacion_resuelta_en)

    def test_solo_desde_revisar(self):
        p = self.pedido(E.PAGADO)
        with self.assertRaises(services.ErrorDeTienda):
            services.marcar_devuelto(p)


class DuplicadoDevueltoTests(_Base):
    def test_marca_resuelto_y_vacia_la_pestana(self):
        p = self.con_duplicados("2")
        self.accion(p, "duplicado_devuelto", payment_id="2")
        self.assertEqual(p.estado, E.PAGADO)
        self.assertFalse(p.revision_mp_pendiente)
        self.assertTrue(p.respuesta_mp["pagos_adicionales"][0]["resuelto"])
        self.assertEqual(p.respuesta_mp["id"], 1)
        self.assertEqual(services.contadores_panel(self.gimnasio)["a_revisar_mp"], 0)

    def test_con_dos_duplicados_resolver_uno_no_vacia_la_pestana(self):
        p = self.con_duplicados("2", "3")
        self.accion(p, "duplicado_devuelto", payment_id="2")
        self.assertTrue(p.revision_mp_pendiente)
        self.accion(p, "duplicado_devuelto", payment_id="3")
        self.assertFalse(p.revision_mp_pendiente)

    def test_un_pago_que_no_es_duplicado_es_error(self):
        p = self.con_duplicados("2")
        r = self.accion(p, "duplicado_devuelto", payment_id="99")
        self.assertContains(r, "Ese pedido ya cambió de estado")
        self.assertTrue(p.revision_mp_pendiente)

    def test_otro_gimnasio_no_puede(self):
        otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        staff_b = User.objects.create_user("staff-b", password="c-123456")
        Perfil.objects.create(usuario=staff_b, gimnasio=otro, rol=Perfil.Rol.STAFF)
        p = self.con_duplicados("2")
        self.client.force_login(staff_b)
        r = self.client.post(
            reverse("tienda:pedido_accion", args=[p.pk]),
            {"accion": "duplicado_devuelto", "payment_id": "2"},
        )
        self.assertEqual(r.status_code, 404)


class DevolverDesdeMercadoPagoTests(_Base):
    TEXTO = "Devolvé la plata desde tu cuenta de Mercado Pago"

    def test_cancelar_un_pedido_mp_pagado(self):
        p = self.pedido(E.PAGADO)
        r = self.accion(p, "cancelar", motivo="")
        self.assertEqual(p.estado, E.DEVUELTO)
        self.assertContains(r, self.TEXTO)

    def test_aceptar_el_arrepentimiento_de_un_pedido_mp(self):
        p = self.pedido(E.ENTREGADO, entregado_en=timezone.now(), cancelacion_pedida_en=timezone.now())
        r = self.accion(p, "aceptar_cancelacion", motivo="")
        self.assertEqual(p.estado, E.DEVUELTO)
        self.assertContains(r, self.TEXTO)

    def test_transferencia_no(self):
        p = self.pedido(E.PAGADO, medio=Pedido.Medio.TRANSFERENCIA, payment_id="")
        r = self.accion(p, "cancelar", motivo="")
        self.assertNotContains(r, self.TEXTO)

    def test_cancelar_uno_sin_pagar_no(self):
        p = self.pedido(E.PENDIENTE_PAGO, payment_id="", estado_mp="")
        r = self.accion(p, "cancelar", motivo="")
        self.assertEqual(p.estado, E.CANCELADO)
        self.assertNotContains(r, self.TEXTO)


@override_settings(**MP)
class VendidoEsteMesTests(_Base):
    def test_un_pedido_pagado_por_mercado_pago_suma(self):
        cuenta = CuentaMercadoPago.objects.create(
            gimnasio=self.gimnasio, access_token="APP_USR-x", refresh_token="R",
            token_expira=timezone.now() + timedelta(days=100),
        )
        p = self.pedido(
            E.PENDIENTE_PAGO, payment_id="", estado_mp="",
            reservado_hasta=timezone.now() + timedelta(minutes=30),
        )
        pago = {
            "id": 1, "status": "approved", "transaction_amount": 15000, "currency_id": "ARS",
            "external_reference": str(p.referencia_externa),
        }
        with mock.patch("suscripcion.mercadopago.consultar_pago", return_value=pago):
            pagos_mp.acreditar_pedido(cuenta, "1")
        resumen = analitica.resumen_del_mes(self.gimnasio)
        self.assertEqual((resumen["pedidos"], resumen["total"]), (1, Decimal("15000")))


class AvisosNuevosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.pedido = Pedido.objects.create(gimnasio=self.gimnasio, alumno=alumno, total=15000)

    @mock.patch("notificaciones.services.notificar_a_gimnasio")
    def test_staff_siempre_con_rol_staff_y_su_pestana(self, push):
        from notificaciones.services import notificar_pedido_al_staff

        esperado = {
            "pago_mp": "filtro=entregar", "revisar_mp": "filtro=revisar_mp",
            "pago_duplicado": "filtro=revisar_mp", "devuelto_mp": "filtro=todos",
        }
        for aviso, filtro in esperado.items():
            with self.subTest(aviso=aviso):
                notificar_pedido_al_staff(self.pedido, aviso)
                llamada = push.call_args
                self.assertEqual(llamada.kwargs["rol"], Perfil.Rol.STAFF)
                self.assertIn(filtro, llamada.args[1]["url"])
                self.assertIn(f"#{self.pedido.pk}", llamada.args[1]["body"])
        self.assertIn("devolvé uno desde Mercado Pago", push.call_args_list[2].args[1]["body"])

    @mock.patch("notificaciones.services.notificar_a_usuario")
    def test_alumno(self, push):
        from notificaciones.services import notificar_pedido_al_alumno

        for aviso in ("revisar_mp", "devuelto_mp"):
            notificar_pedido_al_alumno(self.pedido, aviso)
        cuerpos = [c.args[1]["body"] for c in push.call_args_list]
        self.assertIn("el gimnasio te va a contactar", cuerpos[0])
        self.assertIn("Se devolvió el pago", cuerpos[1])
