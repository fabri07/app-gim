"""`suscripcion/services.py`: el camino de la plata.

`TransactionTestCase` y no `TestCase`: los mails salen por
`transaction.on_commit`, y bajo `TestCase` la transacción del test nunca
commitea, así que no correrían jamás y un test de «manda el mail» pasaría o
fallaría por el motivo equivocado (la lección de `CLAUDE.md` § «Datos de
demostración»).

Nada sale a la red: se parchean las funciones públicas de
`suscripcion.mercadopago` y `plataforma.cambio.cotizacion_dolar`.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from alumnos.models import Alumno
from plataforma import precios
from plataforma.models import PagoPlataforma
from suscripcion import services
from suscripcion.mercadopago import ErrorDeMercadoPago
from suscripcion.models import IntentoDeCobro
from tenants.models import Gimnasio

Estado = IntentoDeCobro.Estado
Plan = IntentoDeCobro.Plan

COTIZACION = {"compra": Decimal("1150.00"), "venta": Decimal("1200.00")}
PREFERENCIA = {"id": "pref-1", "init_point": "https://mp.test/checkout/pref-1"}


def _gimnasio(slug, *, dias_desde_inicio=40, alumnos=5, **extra):
    """Un gimnasio que empezó a facturar hace `dias_desde_inicio` días.

    Con 40 (el default) la prueba terminó hace 10 días, así que el primer
    período es `[hoy-10, hoy+19]`: pagarlo lo deja al día.
    """
    gimnasio = Gimnasio.objects.create(
        nombre=slug.replace("-", " ").title(),
        slug=slug,
        facturacion_inicio=timezone.localdate() - timedelta(days=dias_desde_inicio),
        **extra,
    )
    for i in range(alumnos):
        Alumno.objects.create(gimnasio=gimnasio, nombre=f"A{i}", apellido="Activo")
    return gimnasio


def _crear(gimnasio, plan=Plan.MENSUAL):
    return services.crear_intento(
        gimnasio.pk,
        plan,
        url_retorno_para=lambda ref: f"https://app.test/retorno/{ref}/",
        url_notificacion="https://app.test/webhook/",
        email_de_quien_pago="dueno@gimnasio.test",
    )


def _pago(intento, pago_id="111", status="approved", **extra):
    datos = {
        "id": int(pago_id),
        "status": status,
        "status_detail": "accredited",
        "transaction_amount": float(intento.monto_ars),
        "currency_id": "ARS",
        "external_reference": str(intento.referencia_externa),
        "date_approved": "2026-09-28T23:30:00.000-03:00",
        "payer": {"email": "nunca@se.guarda"},
    }
    datos.update(extra)
    return datos


@override_settings(
    COBRO_ONLINE_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    COBROS_AVISO_EMAIL="alertas@x.com",
)
class BaseServicios(TransactionTestCase):
    def setUp(self):
        self.preferencia = patch(
            "suscripcion.mercadopago.crear_preferencia", return_value=PREFERENCIA
        )
        self.mock_preferencia = self.preferencia.start()
        self.addCleanup(self.preferencia.stop)
        self.cotizacion = patch(
            "plataforma.cambio.cotizacion_dolar", return_value=COTIZACION
        )
        self.mock_cotizacion = self.cotizacion.start()
        self.addCleanup(self.cotizacion.stop)
        # Pagos que «devuelve» Mercado Pago, por id.
        self.pagos_mp = {}
        self.consultar = patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=lambda pid: self.pagos_mp[str(pid)],
        )
        self.consultar.start()
        self.addCleanup(self.consultar.stop)
        self.gimnasio = _gimnasio("vida-plena")

    def _mp(self, pago):
        self.pagos_mp[str(pago["id"])] = pago
        return pago["id"]

    def _acreditar(self, pago):
        return services.acreditar(
            self._mp(pago), url_panel_para=lambda g: f"https://app.test/panel/{g.pk}/"
        )

    def _asuntos(self):
        return [m.subject for m in mail.outbox]


class CrearIntentoTests(BaseServicios):
    def test_crea_el_intento_con_el_precio_congelado_y_el_link(self):
        intento = _crear(self.gimnasio)

        hoy = timezone.localdate()
        self.assertEqual(intento.estado, Estado.CREADO)
        self.assertEqual(intento.periodo_desde, hoy - timedelta(days=10))
        self.assertEqual(intento.periodo_hasta, hoy + timedelta(days=19))
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd(5)))
        self.assertEqual(intento.monto_ars, Decimal("12000.00"))
        self.assertEqual(intento.tipo_cambio, Decimal("1200.00"))
        self.assertEqual(intento.alumnos_activos, 5)
        self.assertEqual(intento.preference_id, "pref-1")
        self.assertEqual(intento.init_point, PREFERENCIA["init_point"])
        self.assertEqual(intento.email_de_quien_pago, "dueno@gimnasio.test")
        kwargs = self.mock_preferencia.call_args.kwargs
        self.assertEqual(kwargs["referencia_externa"], str(intento.referencia_externa))
        self.assertIn(str(intento.referencia_externa), kwargs["url_retorno"])
        self.assertTrue(kwargs["titulo"].startswith("TuGimApp — plan mensual del "))

    def test_demo_exento_e_inactivo_no_aplican(self):
        casos = [
            _gimnasio("demo", es_demo=True),
            _gimnasio("exento", facturacion_exenta=True),
            _gimnasio("retirado", activo=False),
        ]
        for gimnasio in casos:
            with self.subTest(gimnasio=gimnasio.slug):
                with self.assertRaises(services.CobroNoDisponible) as ctx:
                    _crear(gimnasio)
                self.assertEqual(ctx.exception.motivo, "no_aplica")
        self.assertFalse(IntentoDeCobro.objects.exists())
        self.mock_preferencia.assert_not_called()

    def test_sin_cotizacion_no_queda_ningun_intento(self):
        self.mock_cotizacion.return_value = None

        with self.assertLogs("suscripcion.services", "WARNING") as logs:
            with self.assertRaises(services.CobroNoDisponible) as ctx:
                _crear(self.gimnasio)

        self.assertEqual(ctx.exception.motivo, "sin_cotizacion")
        self.assertIn("vida-plena", logs.output[0])
        self.assertFalse(IntentoDeCobro.objects.exists())
        self.mock_preferencia.assert_not_called()

    def test_error_de_mercadopago_no_deja_ningun_intento(self):
        self.mock_preferencia.side_effect = ErrorDeMercadoPago("se cortó")

        with self.assertRaises(services.CobroNoDisponible) as ctx:
            _crear(self.gimnasio)

        self.assertEqual(ctx.exception.motivo, "mercadopago")
        self.assertFalse(IntentoDeCobro.objects.exists())

    def test_integracion_apagada(self):
        with override_settings(COBRO_ONLINE_ENABLED=False):
            with self.assertRaises(services.CobroNoDisponible) as ctx:
                _crear(self.gimnasio)

        self.assertEqual(ctx.exception.motivo, "integracion_apagada")
        self.assertFalse(IntentoDeCobro.objects.exists())

    def test_cada_motivo_tiene_su_mensaje(self):
        for motivo in ("integracion_apagada", "no_aplica", "sin_cotizacion", "mercadopago"):
            self.assertTrue(services.CobroNoDisponible(motivo).mensaje)
        self.assertEqual(
            services.MENSAJES["sin_cotizacion"],
            "Ahora mismo no podemos calcular el monto en pesos. Probá de nuevo "
            "en unos minutos.",
        )

    def test_con_un_pago_previo_cobra_el_periodo_siguiente(self):
        hoy = timezone.localdate()
        jefe = User.objects.create_user("jefe", is_superuser=True)
        PagoPlataforma.objects.create(
            gimnasio=self.gimnasio,
            fecha_pago=hoy,
            monto_usd=Decimal("10"),
            alumnos_activos=5,
            periodo_desde=hoy - timedelta(days=10),
            periodo_hasta=hoy + timedelta(days=19),
            registrado_por=jefe,
        )

        intento = _crear(self.gimnasio)

        self.assertEqual(intento.periodo_desde, hoy + timedelta(days=20))

    def test_dos_llamadas_del_mismo_plan_devuelven_el_mismo_intento(self):
        primero = _crear(self.gimnasio)
        segundo = _crear(self.gimnasio)

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(IntentoDeCobro.objects.count(), 1)
        self.assertEqual(self.mock_preferencia.call_count, 1)

    def test_cambiar_de_plan_cancela_el_anterior(self):
        mensual = _crear(self.gimnasio)
        anual = _crear(self.gimnasio, Plan.ANUAL)

        mensual.refresh_from_db()
        self.assertEqual(mensual.estado, Estado.CANCELADO)
        self.assertEqual(anual.estado, Estado.CREADO)
        self.assertNotEqual(mensual.pk, anual.pk)

    def test_un_creado_vencido_se_vence_solo_y_deja_crear_uno_nuevo(self):
        viejo = _crear(self.gimnasio)
        IntentoDeCobro.objects.filter(pk=viejo.pk).update(
            expira=timezone.now() - timedelta(minutes=1)
        )

        nuevo = _crear(self.gimnasio)

        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.VENCIDO)
        self.assertNotEqual(nuevo.pk, viejo.pk)
        self.assertEqual(nuevo.estado, Estado.CREADO)

    def test_el_anual_congela_el_precio_anual_y_360_dias(self):
        intento = _crear(self.gimnasio, Plan.ANUAL)

        self.assertEqual(intento.plan, Plan.ANUAL)
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd_anual(5)))
        self.assertEqual(
            (intento.periodo_hasta - intento.periodo_desde).days + 1, 360
        )
        self.assertEqual(
            intento.monto_ars, Decimal(precios.precio_usd_anual(5)) * Decimal("1200")
        )


class AcreditarTests(BaseServicios):
    def setUp(self):
        super().setUp()
        self.intento = _crear(self.gimnasio)

    def test_dos_veces_el_mismo_pago_es_un_solo_pago_plataforma(self):
        self._acreditar(_pago(self.intento))
        self._acreditar(_pago(self.intento))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.PAGADO)
        pago = PagoPlataforma.objects.get()
        self.assertEqual(self.intento.pago, pago)
        self.assertEqual(pago.origen, PagoPlataforma.Origen.ONLINE)
        self.assertIsNone(pago.registrado_por)
        self.assertEqual(pago.monto_usd, self.intento.monto_usd)
        self.assertEqual(pago.monto_ars, self.intento.monto_ars)
        self.assertEqual(pago.tipo_cambio, self.intento.tipo_cambio)
        self.assertEqual(pago.alumnos_activos, 5)
        self.assertEqual(pago.periodo_desde, self.intento.periodo_desde)
        self.assertEqual(pago.periodo_hasta, self.intento.periodo_hasta)
        self.assertEqual(pago.notas, "Mercado Pago, pago 111")
        # 23:30 de Buenos Aires es la fecha local, no el día siguiente en UTC.
        self.assertEqual(pago.fecha_pago.isoformat(), "2026-09-28")
        self.assertIsNotNone(self.intento.acreditado_en)
        self.assertNotIn("payer", self.intento.respuesta_mp)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Cobro acreditado", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["alertas@x.com"])
        self.assertIn("https://app.test/panel/", mail.outbox[0].body)

    def test_webhook_primero_o_retorno_primero_terminan_igual(self):
        """El retorno del navegador puede ver el pago todavía `in_process` y
        el webhook ya `approved`, o al revés los dos `approved`: el final es
        el mismo."""
        otro_gimnasio = _gimnasio("otro")
        otro = _crear(otro_gimnasio)

        # Orden 1: el retorno lo ve en proceso, después llega el webhook.
        self._acreditar(_pago(self.intento, "201", status="in_process"))
        self._acreditar(_pago(self.intento, "201"))
        # Orden 2: el webhook primero, el retorno después.
        self._acreditar(_pago(otro, "202"))
        self._acreditar(_pago(otro, "202"))

        for intento in (self.intento, otro):
            intento.refresh_from_db()
            self.assertEqual(intento.estado, Estado.PAGADO)
            self.assertEqual(intento.estado_mp, "approved")
        self.assertEqual(PagoPlataforma.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_monto_distinto_va_a_revisar_sin_pago_y_con_mail(self):
        self._acreditar(_pago(self.intento, transaction_amount=11000.0))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.REVISAR)
        self.assertFalse(PagoPlataforma.objects.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Monto a revisar", mail.outbox[0].subject)

    def test_moneda_distinta_va_a_revisar(self):
        self._acreditar(_pago(self.intento, currency_id="USD"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.REVISAR)
        self.assertFalse(PagoPlataforma.objects.exists())

    def test_solapamiento_con_un_pago_manual_es_duplicado(self):
        jefe = User.objects.create_user("jefe", is_superuser=True)
        # Arranca OTRO día: la UniqueConstraint(gimnasio, periodo_desde) no lo
        # ve, sólo el chequeo de solapamiento.
        PagoPlataforma.objects.create(
            gimnasio=self.gimnasio,
            fecha_pago=timezone.localdate(),
            monto_usd=Decimal("10"),
            alumnos_activos=5,
            periodo_desde=self.intento.periodo_desde + timedelta(days=5),
            periodo_hasta=self.intento.periodo_hasta + timedelta(days=5),
            registrado_por=jefe,
        )

        self._acreditar(_pago(self.intento))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.DUPLICADO)
        self.assertIsNone(self.intento.pago)
        self.assertEqual(PagoPlataforma.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Pago duplicado", mail.outbox[0].subject)

    def test_rechazado_y_despues_se_puede_crear_un_intento_nuevo(self):
        self._acreditar(_pago(self.intento, status="rejected"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.RECHAZADO)
        self.assertEqual(mail.outbox, [])
        nuevo = _crear(self.gimnasio)
        self.assertNotEqual(nuevo.pk, self.intento.pk)
        self.assertEqual(nuevo.estado, Estado.CREADO)

    def test_rechazado_y_despues_aprobado_sobre_el_mismo_intento_se_acredita(self):
        self._acreditar(_pago(self.intento, "301", status="rejected"))
        self._acreditar(_pago(self.intento, "302"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.PAGADO)
        self.assertEqual(self.intento.payment_id, "302")
        self.assertEqual(PagoPlataforma.objects.count(), 1)

    def test_un_vencido_que_aparece_aprobado_se_acredita(self):
        IntentoDeCobro.objects.filter(pk=self.intento.pk).update(estado=Estado.VENCIDO)

        self._acreditar(_pago(self.intento))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.PAGADO)
        self.assertEqual(PagoPlataforma.objects.count(), 1)

    def test_un_cancelado_que_aparece_aprobado_se_acredita(self):
        IntentoDeCobro.objects.filter(pk=self.intento.pk).update(
            estado=Estado.CANCELADO
        )

        self._acreditar(_pago(self.intento))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.PAGADO)

    def test_pago_adicional_sobre_un_intento_pagado_avisa_y_no_pisa_nada(self):
        self._acreditar(_pago(self.intento, "401"))
        mail.outbox.clear()

        self._acreditar(_pago(self.intento, "402"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.PAGADO)
        self.assertEqual(self.intento.payment_id, "401")
        self.assertEqual(PagoPlataforma.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Pago adicional", mail.outbox[0].subject)
        self.assertIn("402", mail.outbox[0].body)

    def test_rechazo_tardio_sobre_un_intento_pagado_no_cambia_nada(self):
        self._acreditar(_pago(self.intento, "502"))
        mail.outbox.clear()
        antes = IntentoDeCobro.objects.get(pk=self.intento.pk)

        self._acreditar(_pago(self.intento, "501", status="rejected"))

        despues = IntentoDeCobro.objects.get(pk=self.intento.pk)
        self.assertEqual(despues.estado, Estado.PAGADO)
        self.assertEqual(despues.payment_id, "502")
        self.assertEqual(despues.estado_mp, "approved")
        self.assertEqual(despues.modificado, antes.modificado)
        self.assertEqual(mail.outbox, [])

    def test_refunded_sobre_un_pagado_pasa_a_devuelto_sin_borrar_el_pago(self):
        self._acreditar(_pago(self.intento, "601"))
        mail.outbox.clear()

        # MISMO payment_id, otro status: es como avisa MP una devolución.
        self._acreditar(_pago(self.intento, "601", status="refunded"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.DEVUELTO)
        self.assertEqual(self.intento.estado_mp, "refunded")
        self.assertEqual(PagoPlataforma.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Pago devuelto", mail.outbox[0].subject)
        self.assertIn("Normal", mail.outbox[0].body)
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.NORMAL)

    def test_estado_pendiente_solo_queda_guardado(self):
        self._acreditar(_pago(self.intento, status="in_process"))

        self.intento.refresh_from_db()
        self.assertEqual(self.intento.estado, Estado.CREADO)
        self.assertEqual(self.intento.estado_mp, "in_process")
        self.assertFalse(PagoPlataforma.objects.exists())

    def test_referencia_desconocida_devuelve_none(self):
        for referencia in (str(uuid.uuid4()), "no-es-un-uuid", None):
            with self.subTest(referencia=referencia):
                pago = _pago(self.intento, "701", external_reference=referencia)
                self.assertIsNone(self._acreditar(pago))
        self.intento.refresh_from_db()
        self.assertEqual(self.intento.payment_id, "")

    def test_error_de_mercadopago_se_propaga(self):
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=ErrorDeMercadoPago("timeout"),
        ):
            with self.assertRaises(ErrorDeMercadoPago):
                services.acreditar("123")


class DescongelamientoTests(BaseServicios):
    def _congelar(self, gimnasio):
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            estado_cuenta_desde=timezone.now() - timedelta(days=3),
        )
        gimnasio.refresh_from_db()

    def test_suspendido_que_queda_al_dia_se_descongela_sin_tocar_modificado(self):
        self._congelar(self.gimnasio)
        modificado = self.gimnasio.modificado
        intento = _crear(self.gimnasio)

        self._acreditar(_pago(intento))

        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.NORMAL)
        self.assertIsNone(self.gimnasio.estado_cuenta_desde)
        self.assertEqual(self.gimnasio.modificado, modificado)
        self.assertIn("se descongeló", mail.outbox[0].body)

    def test_con_tres_periodos_adeudados_pagar_uno_no_descongela(self):
        # Empezó a facturar hace 150 días: el primer período sin pagar es
        # [hoy-120, hoy-91], y pagarlo deja otros tres sin cubrir.
        gimnasio = _gimnasio("deudor", dias_desde_inicio=150)
        self._congelar(gimnasio)
        intento = _crear(gimnasio)
        hoy = timezone.localdate()
        self.assertEqual(intento.periodo_hasta, hoy - timedelta(days=91))

        self._acreditar(_pago(intento))

        gimnasio.refresh_from_db()
        self.assertEqual(gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.SUSPENDIDA)
        self.assertIsNotNone(gimnasio.estado_cuenta_desde)
        intento.refresh_from_db()
        self.assertEqual(intento.estado, Estado.PAGADO)
        debe_desde = (hoy - timedelta(days=90)).strftime("%d/%m/%Y")
        self.assertIn(f"sigue debiendo desde el {debe_desde}", mail.outbox[0].body)


class ReconciliarTests(BaseServicios):
    def setUp(self):
        super().setUp()
        self.primero = _crear(self.gimnasio)
        self.segundo = _crear(_gimnasio("otro"))

    def test_acredita_lo_que_encuentra_y_un_error_no_corta_el_resto(self):
        def buscar(referencia):
            if referencia == str(self.primero.referencia_externa):
                raise ErrorDeMercadoPago("timeout")
            return [{"id": self._mp(_pago(self.segundo, "801"))}]

        with patch("suscripcion.mercadopago.buscar_pagos", side_effect=buscar):
            with self.assertLogs("suscripcion.services", "WARNING"):
                acreditados = services.reconciliar_pendientes()

        self.assertEqual(acreditados, 1)
        self.primero.refresh_from_db()
        self.segundo.refresh_from_db()
        self.assertEqual(self.primero.estado, Estado.CREADO)
        self.assertEqual(self.segundo.estado, Estado.PAGADO)

    def test_ignora_los_viejos_y_los_que_ya_tienen_pago(self):
        IntentoDeCobro.objects.filter(pk=self.primero.pk).update(
            creado=timezone.now() - timedelta(days=services.DIAS_RECONCILIACION + 1)
        )
        IntentoDeCobro.objects.filter(pk=self.segundo.pk).update(payment_id="999")

        with patch("suscripcion.mercadopago.buscar_pagos", return_value=[]) as buscar:
            self.assertEqual(services.reconciliar_pendientes(), 0)

        buscar.assert_not_called()

    def test_sin_integracion_no_reconcilia(self):
        with override_settings(COBRO_ONLINE_ENABLED=False):
            with self.assertRaises(services.CobroNoDisponible):
                services.reconciliar_pendientes()
