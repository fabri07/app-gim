"""Acreditar cuotas cobradas por Mercado Pago (T5 de «cuotas por MP»).

`pagos.cobro_mp`: la máquina de estados por el PAR (intento × cuota), la
idempotencia, las lecturas viejas, las carreras, el desvío desde el webhook de
la tienda y los avisos. La suite no sale a la red: se parchea
`suscripcion.mercadopago.consultar_pago` y se corren los `on_commit`.
"""

import traceback
import urllib.error
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from alumnos.models import Alumno
from pagos import acceso, cobro, cobro_mp
from pagos.models import CobroOnlineCuota, Cuota
from pagos.testing import crear_cuota
from suscripcion.mercadopago import ErrorDeMercadoPago
from tenants.models import Gimnasio, Perfil
from tienda import pagos_mp
from tienda.models import CuentaMercadoPago, Pedido

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
CONSULTAR = "suscripcion.mercadopago.consultar_pago"
AL_STAFF = "notificaciones.services.notificar_cuota_mp_al_staff"
AL_ALUMNO = "notificaciones.services.notificar_cuota_mp_al_alumno"
I = CobroOnlineCuota.Estado
M = CobroOnlineCuota.MotivoRevision
C = Cuota.Estado
TOKEN = "APP_USR-token-del-gimnasio-777"


@override_settings(**MP)
class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym A", slug="a")
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil
        )
        self.cuenta = self.crear_cuenta()
        self.inicio = timezone.localdate() - timedelta(days=5)
        self.cuota = self.crear_cuota()
        self.intento = self.crear_intento()

    def crear_cuenta(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, access_token=TOKEN,
            refresh_token="REF", mp_user_id="111",
            token_expira=timezone.now() + timedelta(days=100),
            estado=CuentaMercadoPago.Estado.CONECTADA,
        )
        datos.update(extra)
        return CuentaMercadoPago.objects.create(**datos)

    def crear_cuota(self, inicio=None, **extra):
        extra.setdefault("monto", Decimal("28000"))
        return crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno, inicio=inicio or self.inicio, **extra
        )

    def crear_intento(self, cuota=None, **extra):
        datos = dict(
            gimnasio=self.gimnasio, cuota=cuota or self.cuota,
            monto=Decimal("28000"), mp_user_id="111",
            preference_id="pref-1", init_point="https://mp/pref-1",
        )
        datos.update(extra)
        return CobroOnlineCuota.objects.create(**datos)

    def pago(self, intento=None, id=501, status="approved", monto="28000", moneda="ARS",
             **extra):
        """Una respuesta cruda de MP, con datos del pagador (nunca se guardan)."""
        datos = {
            "id": id,
            "status": status,
            "transaction_amount": float(monto),
            "net_received_amount": float(monto) * 0.9,
            "currency_id": moneda,
            "external_reference": (intento or self.intento).referencia_mp,
            "date_approved": "2026-10-21T01:30:00.000-00:00",
            "payer": {"email": "nunca@se.guarda", "identification": {"number": "20111222"}},
        }
        datos.update(extra)
        return datos

    def acreditar(self, pago, cuenta=None):
        """`acreditar_cuota_por_id` con el pago parcheado y los avisos
        capturados. Devuelve (intento, avisos al staff, avisos al alumno)."""
        with mock.patch(CONSULTAR, return_value=pago) as consultar, \
                mock.patch(AL_STAFF) as staff, mock.patch(AL_ALUMNO) as alumno, \
                self.captureOnCommitCallbacks(execute=True):
            resultado = cobro_mp.acreditar_cuota_por_id(cuenta or self.cuenta, str(pago["id"]))
        self.consultar = consultar
        return (
            resultado,
            [c.args[1] for c in staff.call_args_list],
            [c.args[1] for c in alumno.call_args_list],
        )

    def refrescar(self, *objetos):
        for objeto in objetos or (self.cuota, self.intento):
            objeto.refresh_from_db()


# --- approved -------------------------------------------------------------------------


class AprobadoTests(_Base):
    def test_link_creado_y_cuota_impaga_queda_pagada(self):
        resultado, staff, alumno = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(resultado, self.intento)
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        # 01:30 UTC del 21 son las 22:30 del 20 en Argentina.
        self.assertEqual(self.cuota.fecha_pago, date(2026, 10, 20))
        self.assertEqual(self.cuota.medio_pago_texto, "Mercado Pago")
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.payment_id, "501")
        self.assertEqual(self.intento.estado_mp, "approved")
        self.assertIsNotNone(self.intento.acreditado_en)
        self.assertFalse(self.intento.revision_pendiente)
        self.assertEqual(staff, ["pago_mp"])
        self.assertEqual(alumno, ["pago_mp"])

    def test_sin_date_approved_la_fecha_es_hoy_local(self):
        pago = self.pago()
        del pago["date_approved"]
        self.acreditar(pago)
        self.refrescar()
        self.assertEqual(self.cuota.fecha_pago, timezone.localdate())

    def test_cuota_vencida_tambien_se_paga(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.VENCIDO)
        self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_link_cancelado_vencido_o_rechazado_se_acredita_igual(self):
        """La plata manda: cancelar un link no impide que MP cobre un pago
        que el alumno ya había empezado."""
        for i, estado in enumerate((I.CANCELADO, I.VENCIDO, I.RECHAZADO)):
            with self.subTest(estado=estado):
                cuota = self.crear_cuota(inicio=self.inicio - timedelta(days=28 * (i + 1)))
                intento = self.crear_intento(cuota=cuota, estado=estado)
                self.acreditar(self.pago(intento, id=600 + i))
                self.refrescar(cuota, intento)
                self.assertEqual(cuota.estado, C.PAGADO)
                self.assertEqual(intento.estado, I.PAGADO)

    def test_pagar_cancela_el_link_vivo_de_la_misma_cuota(self):
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(estado=I.CANCELADO)
        vivo = self.crear_intento(preference_id="pref-2")
        self.acreditar(self.pago())
        self.refrescar(self.intento, vivo)
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(vivo.estado, I.CANCELADO)

    def test_monto_corregido_con_el_link_abierto_paga_lo_cobrado_y_queda_para_revisar(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=Decimal("30000"))
        _, staff, alumno = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.motivo_revision, M.MONTO_CORREGIDO)
        self.assertTrue(self.intento.revision_pendiente)
        self.assertEqual(staff, ["revisar_mp"])
        self.assertEqual(alumno, ["pago_mp"])

    def test_cuota_ya_pagada_queda_duplicado_y_la_cuota_no_se_toca(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=C.PAGADO, fecha_pago=date(2026, 1, 2), medio_pago_texto="Efectivo"
        )
        _, staff, alumno = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DUPLICADO)
        self.assertEqual(self.intento.motivo_revision, M.CUOTA_YA_PAGADA)
        self.assertTrue(self.intento.revision_pendiente)
        self.assertEqual(self.cuota.fecha_pago, date(2026, 1, 2))
        self.assertEqual(self.cuota.medio_pago_texto, "Efectivo")
        self.assertEqual(staff, ["pago_duplicado"])
        self.assertEqual(alumno, ["revisar"])

    def test_cuota_anulada_queda_para_revisar(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.ANULADO)
        _, staff, _ = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.ANULADO)
        self.assertEqual(self.intento.estado, I.REVISAR)
        self.assertEqual(self.intento.motivo_revision, M.CUOTA_ANULADA)
        self.assertEqual(staff, ["revisar_mp"])

    def test_monto_o_moneda_distintos_quedan_para_revisar_sin_tocar_la_cuota(self):
        for i, pago in enumerate((
            self.pago(monto="1000"), self.pago(moneda="USD"), self.pago(monto="28000.02")
        )):
            with self.subTest(pago=(pago["transaction_amount"], pago["currency_id"])):
                CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(
                    estado=I.CREADO, payment_id="", estado_mp="", motivo_revision=""
                )
                pago["id"] = 700 + i
                _, staff, _ = self.acreditar(pago)
                self.refrescar()
                self.assertEqual(self.cuota.estado, C.PENDIENTE)
                self.assertEqual(self.intento.estado, I.REVISAR)
                self.assertEqual(self.intento.motivo_revision, M.MONTO_DISTINTO)
                self.assertEqual(staff, ["revisar_mp"])

    def test_un_centavo_de_diferencia_se_tolera(self):
        self.acreditar(self.pago(monto="28000.01"))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.monto, Decimal("28000"))

    def test_no_se_niega_por_cuenta_congelada_desconectada_ni_cuotas_apagadas(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            cuotas_con_mercadopago=False, tienda_habilitada=False,
        )
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).update(
            estado=CuentaMercadoPago.Estado.DESCONECTADA,
            desconectada_en=timezone.now() - timedelta(days=2),
        )
        self.cuenta.refresh_from_db()
        with override_settings(TIENDA_MP_ENABLED=False):
            self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_lee_el_pago_con_el_token_del_gimnasio(self):
        """Sin `token=` Mercado Pago usaría el de la PLATAFORMA."""
        self.acreditar(self.pago())
        self.consultar.assert_called_once()
        self.assertEqual(self.consultar.call_args.args, ("501",))
        self.assertEqual(self.consultar.call_args.kwargs["token"].valor, TOKEN)

    def test_respuesta_mp_guarda_solo_el_resumen(self):
        self.acreditar(self.pago())
        self.refrescar()
        self.assertNotIn("payer", self.intento.respuesta_mp)
        self.assertNotIn("net_received_amount", self.intento.respuesta_mp)
        self.assertNotIn("nunca@se.guarda", str(self.intento.respuesta_mp))
        self.assertEqual(self.intento.respuesta_mp["status"], "approved")


class IdempotenciaTests(_Base):
    def test_el_mismo_aprobado_dos_veces_no_hace_nada_la_segunda(self):
        self.acreditar(self.pago())
        self.refrescar()
        antes = (self.intento.modificado, self.cuota.modificado)
        _, staff, alumno = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual((self.intento.modificado, self.cuota.modificado), antes)
        self.assertEqual((staff, alumno), ([], []))

    def test_otro_aprobado_sobre_un_link_que_ya_cobro_se_anota_una_vez(self):
        self.acreditar(self.pago())
        _, staff, _ = self.acreditar(self.pago(id=502))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.payment_id, "501")
        self.assertTrue(self.intento.revision_pendiente)
        adicionales = self.intento.respuesta_mp["pagos_adicionales"]
        self.assertEqual([a["payment_id"] for a in adicionales], ["502"])
        self.assertEqual(staff, ["pago_duplicado"])

        _, staff, _ = self.acreditar(self.pago(id=502))
        self.refrescar()
        self.assertEqual(len(self.intento.respuesta_mp["pagos_adicionales"]), 1)
        self.assertEqual(staff, [])

    def test_la_devolucion_del_adicional_lo_resuelve_sin_alarma(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=502))
        with self.assertNoLogs("pagos.cobro_mp", level="WARNING"):
            _, staff, alumno = self.acreditar(self.pago(id=502, status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertFalse(self.intento.revision_pendiente)
        self.assertTrue(self.intento.respuesta_mp["pagos_adicionales"][0]["resuelto"])
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual((staff, alumno), ([], []))

    def test_la_devolucion_del_principal_conserva_los_adicionales(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=502))
        self.acreditar(self.pago(status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DEVUELTO)
        self.assertEqual(
            self.intento.respuesta_mp["pagos_adicionales"][0]["payment_id"], "502"
        )


class RechazadoTests(_Base):
    def test_rechazado_solo_se_anota_y_el_link_sigue_vivo(self):
        for status in ("rejected", "cancelled"):
            with self.subTest(status=status):
                _, staff, alumno = self.acreditar(self.pago(id=f"9{len(status)}", status=status))
                self.refrescar()
                self.assertEqual(self.intento.estado, I.CREADO)
                self.assertEqual(self.intento.estado_mp, status)
                self.assertEqual(self.cuota.estado, C.PENDIENTE)
                self.assertEqual((staff, alumno), ([], []))

    def test_rechazo_y_reintento_aprobado_en_el_mismo_link(self):
        self.acreditar(self.pago(id=801, status="rejected"))
        self.acreditar(self.pago(id=802))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.payment_id, "802")
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_un_rechazo_viejo_no_pisa_lo_pagado(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=800, status="rejected"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.payment_id, "501")


class DevueltoTests(_Base):
    def test_devolucion_del_pago_que_pago_la_cuota(self):
        self.acreditar(self.pago())
        _, staff, alumno = self.acreditar(self.pago(status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DEVUELTO)
        self.assertEqual(self.intento.motivo_revision, M.DEVUELTO)
        self.assertTrue(self.intento.revision_pendiente)
        # La cuota NO vuelve sola a impaga: lo decide el staff.
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertTrue(cobro.puede_volver_a_impaga(self.cuota.pk))
        self.assertEqual(staff, ["devuelto_mp"])
        self.assertEqual(alumno, [])

    def test_contracargo_despues_de_la_devolucion_solo_se_anota(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(status="refunded"))
        _, staff, _ = self.acreditar(self.pago(status="charged_back"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DEVUELTO)
        self.assertEqual(self.intento.estado_mp, "charged_back")
        self.assertEqual(staff, [])

    def test_devolucion_del_pago_duplicado_lo_resuelve_sin_alarma(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.PAGADO)
        self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DUPLICADO)
        _, staff, alumno = self.acreditar(self.pago(status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DUPLICADO)
        self.assertFalse(self.intento.revision_pendiente)
        self.assertIsNotNone(self.intento.revision_resuelta_en)
        self.assertEqual((staff, alumno), ([], []))
        self.assertFalse(cobro.puede_volver_a_impaga(self.cuota.pk))

    def test_devolucion_de_un_monto_distinto_lo_resuelve(self):
        self.acreditar(self.pago(monto="1"))
        self.acreditar(self.pago(monto="1", status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.REVISAR)
        self.assertFalse(self.intento.revision_pendiente)
        self.assertEqual(self.cuota.estado, C.PENDIENTE)


class LecturasViejasTests(_Base):
    def test_un_aprobado_viejo_no_revive_un_pago_devuelto(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(status="refunded"))
        _, staff, _ = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DEVUELTO)
        self.assertEqual(self.intento.estado_mp, "refunded")
        self.assertEqual(staff, [])

    def test_devuelto_antes_que_el_aprobado_nunca_paga_la_cuota(self):
        self.acreditar(self.pago(status="refunded"))
        self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(self.intento.estado_mp, "refunded")
        self.assertEqual(self.cuota.estado, C.PENDIENTE)

    def test_un_in_process_viejo_no_pisa_el_aprobado(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(status="in_process"))
        self.refrescar()
        self.assertEqual(self.intento.estado_mp, "approved")

    def test_in_process_no_hace_nada(self):
        _, staff, _ = self.acreditar(self.pago(status="in_process"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.CREADO)
        self.assertEqual(self.intento.estado_mp, "")
        self.assertEqual(staff, [])


class CarrerasTests(_Base):
    def test_la_cuota_cambia_entre_la_lectura_y_el_lock_se_relee(self):
        """Se leyó impaga, pero el staff la confirmó a mano antes del UPDATE:
        se deshace la vuelta y la relectura la ve PAGADA → DUPLICADO."""
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=C.PAGADO, medio_pago_texto="Efectivo"
        )
        vieja = Cuota.objects.get(pk=self.cuota.pk)
        vieja.estado = C.PENDIENTE
        leer = cobro_mp._leer
        llamadas = []

        def vieja_la_primera_vez(cuota_pk, intento_pk):
            cuota, intento = leer(cuota_pk, intento_pk)
            llamadas.append(cuota_pk)
            return (vieja if len(llamadas) == 1 else cuota), intento

        with mock.patch("pagos.cobro_mp._leer", side_effect=vieja_la_primera_vez):
            _, staff, _ = self.acreditar(self.pago())
        self.refrescar()
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(self.intento.estado, I.DUPLICADO)
        self.assertEqual(self.cuota.medio_pago_texto, "Efectivo")
        # El aviso de la vuelta deshecha no sale.
        self.assertEqual(staff, ["pago_duplicado"])

    def test_dos_veces_es_un_error_reintentable_y_no_queda_nada_escrito(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.PAGADO)
        vieja = Cuota.objects.get(pk=self.cuota.pk)
        vieja.estado = C.PENDIENTE

        def siempre_vieja(cuota_pk, intento_pk):
            return vieja, CobroOnlineCuota.objects.get(pk=intento_pk)

        with mock.patch("pagos.cobro_mp._leer", side_effect=siempre_vieja), \
                mock.patch(CONSULTAR, return_value=self.pago()):
            with self.assertRaises(pagos_mp.AcreditacionReintentable):
                cobro_mp.acreditar_cuota_por_id(self.cuenta, "501")
        self.refrescar()
        self.assertEqual(self.intento.estado, I.CREADO)
        self.assertEqual(self.intento.payment_id, "")


class DeQuienEsElPagoTests(_Base):
    def test_un_cobro_de_otro_gimnasio_no_se_encuentra(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        cuenta_b = self.crear_cuenta(gimnasio=otro)
        resultado, _, _ = self.acreditar(self.pago(), cuenta=cuenta_b)
        self.assertIsNone(resultado)
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)
        self.assertEqual(self.intento.estado_mp, "")

    def test_un_link_de_otra_cuenta_de_mp_se_ignora_sin_datos_del_pagador(self):
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(mp_user_id="999")
        with self.assertLogs("pagos.cobro_mp", level="WARNING") as logs:
            resultado, _, _ = self.acreditar(self.pago())
        self.assertIsNone(resultado)
        self.assertNotIn("nunca@se.guarda", "\n".join(logs.output))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)

    def test_referencias_que_no_son_de_cuotas(self):
        for referencia in ("", "cuota-no-es-uuid", str(self.intento.referencia_externa)):
            with self.subTest(referencia=referencia):
                pago = self.pago(external_reference=referencia)
                self.assertIsNone(self.acreditar(pago)[0])
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)

    def test_el_error_de_mercado_pago_se_propaga(self):
        with mock.patch(CONSULTAR, side_effect=ErrorDeMercadoPago("x", codigo=500)):
            with self.assertRaises(ErrorDeMercadoPago):
                cobro_mp.acreditar_cuota_por_id(self.cuenta, "1")


class DesvioDesdeLaTiendaTests(_Base):
    def test_acreditar_pedido_desvia_y_no_toca_pedidos(self):
        """Un pedido con la MISMA referencia (imposible en la vida real, pero
        prueba el desvío): el pago de la cuota nunca lo toca."""
        pedido = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, total=Decimal("28000"),
            medio=Pedido.Medio.MERCADOPAGO, estado=Pedido.Estado.PENDIENTE_PAGO,
            reservado_hasta=timezone.now() + timedelta(minutes=30),
            preference_id="pref-p", referencia_externa=self.intento.referencia_externa,
        )
        with mock.patch(CONSULTAR, return_value=self.pago()) as consultar, \
                mock.patch(AL_STAFF), mock.patch(AL_ALUMNO), \
                self.captureOnCommitCallbacks(execute=True):
            resultado = pagos_mp.acreditar_pedido(self.cuenta, "501")
        self.assertIsNone(resultado)
        self.assertEqual(consultar.call_args.kwargs["token"].valor, TOKEN)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)
        self.assertEqual(pedido.payment_id, "")
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)


class WebhookTests(_Base):
    def notificar(self, **consultar):
        with mock.patch(CONSULTAR, **consultar), mock.patch(AL_STAFF), \
                mock.patch(AL_ALUMNO), self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                f"/tienda/v1/webhook/mercadopago/{self.cuenta.uuid_webhook}/",
                data={"type": "payment", "data": {"id": "501"}},
                content_type="application/json",
            )

    def test_el_webhook_de_la_tienda_acredita_la_cuota(self):
        respuesta = self.notificar(return_value=self.pago())
        self.assertEqual(respuesta.status_code, 200)
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.intento.estado, I.PAGADO)

    def test_una_carrera_repetida_responde_500(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.PAGADO)
        vieja = Cuota.objects.get(pk=self.cuota.pk)
        vieja.estado = C.PENDIENTE
        with mock.patch(
            "pagos.cobro_mp._leer",
            side_effect=lambda c, i: (vieja, CobroOnlineCuota.objects.get(pk=i)),
        ):
            respuesta = self.notificar(return_value=self.pago())
        self.assertEqual(respuesta.status_code, 500)
        self.refrescar()
        self.assertEqual(self.intento.estado, I.CREADO)


class EfectosTests(_Base):
    def test_ingresos_por_mes_suma_lo_cobrado_no_lo_neto(self):
        from tenants.analitica import ingresos_por_mes

        self.acreditar(self.pago())
        mes = self.inicio.replace(day=1)
        fila = next(f for f in ingresos_por_mes(self.gimnasio) if f["mes"] == mes)
        self.assertEqual(fila["total"], 28000.0)

    def test_pagar_desbloquea_al_alumno(self):
        hoy = timezone.localdate()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=3, fecha_activacion_bloqueo=hoy - timedelta(days=365)
        )
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=C.VENCIDO, periodo_inicio=hoy - timedelta(days=20),
            periodo_fin=hoy + timedelta(days=7),
        )
        self.alumno.refresh_from_db()
        self.assertIsNotNone(acceso.bloqueo_de(self.alumno))

        self.acreditar(self.pago())
        self.alumno.refresh_from_db()
        self.assertIsNone(acceso.bloqueo_de(self.alumno))


class AvisosTests(_Base):
    def test_al_staff_siempre_con_rol_staff_y_al_alumno_a_su_usuario(self):
        with mock.patch(CONSULTAR, return_value=self.pago()), \
                mock.patch("notificaciones.services.notificar_a_gimnasio") as gimnasio, \
                mock.patch("notificaciones.services.notificar_a_usuario") as usuario, \
                self.captureOnCommitCallbacks(execute=True):
            cobro_mp.acreditar_cuota_por_id(self.cuenta, "501")
        gimnasio.assert_called_once()
        self.assertEqual(gimnasio.call_args.kwargs["rol"], Perfil.Rol.STAFF)
        self.assertIn("Ana", gimnasio.call_args.args[1]["body"])
        usuario.assert_called_once()
        self.assertEqual(usuario.call_args.args[0], self.alumno.perfil.usuario)
        self.assertEqual(usuario.call_args.args[1]["title"], "Recibimos el pago de tu cuota")

    def test_los_avisos_salen_despues_del_commit(self):
        with mock.patch(CONSULTAR, return_value=self.pago()), mock.patch(AL_STAFF) as staff, \
                mock.patch(AL_ALUMNO), self.captureOnCommitCallbacks(execute=False) as callbacks:
            cobro_mp.acreditar_cuota_por_id(self.cuenta, "501")
        staff.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_un_push_caido_no_rompe_la_acreditacion(self):
        # `assertLogs` por fuera: los `on_commit` corren al salir de
        # `captureOnCommitCallbacks`, con los patches todavía puestos.
        with self.assertLogs("pagos.cobro_mp", level="ERROR"), \
                mock.patch(CONSULTAR, return_value=self.pago()), \
                mock.patch(AL_STAFF, side_effect=RuntimeError("push caído")), \
                self.captureOnCommitCallbacks(execute=True):
            cobro_mp.acreditar_cuota_por_id(self.cuenta, "501")
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_alumno_sin_acceso_al_portal_no_rompe(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(perfil=None)
        with mock.patch(CONSULTAR, return_value=self.pago()), \
                mock.patch("notificaciones.services.notificar_a_usuario") as usuario, \
                mock.patch("notificaciones.services.notificar_a_gimnasio"), \
                self.captureOnCommitCallbacks(execute=True):
            cobro_mp.acreditar_cuota_por_id(self.cuenta, "501")
        usuario.assert_not_called()


class DatosFueraDeLosFramesTests(_Base):
    """Sentry captura las variables locales de cada frame de la traza (su
    scrubber no es recursivo): ni los datos del pagador ni el token del
    gimnasio pueden quedar en ninguno. try/except a mano: `assertRaises`
    limpia los frames."""

    EMAIL = "pagador-reconocible@secreto.test"

    def _frames(self, llamada, error):
        try:
            llamada()
        except error as e:
            traza = e.__traceback__
        else:
            self.fail(f"debía levantar {error.__name__}")
        return [f for f, _ in traceback.walk_tb(traza)]

    def test_datos_del_pagador(self):
        pago = self.pago(payer={"email": self.EMAIL, "identification": {"number": "20123456"}})
        for entrada, nombre in (
            (lambda: cobro_mp.acreditar_cuota_por_id(self.cuenta, "501"), "acreditar_cuota_por_id"),
            (lambda: pagos_mp.acreditar_pedido(self.cuenta, "501"), "acreditar_cuota_leida"),
        ):
            with self.subTest(nombre=nombre), mock.patch(CONSULTAR, return_value=pago), \
                    mock.patch("pagos.cobro_mp._aplicar", side_effect=RuntimeError("boom")):
                frames = self._frames(entrada, RuntimeError)
            self.assertTrue(any(f.f_code.co_name == nombre for f in frames))
            for frame in frames:
                self.assertNotIn(self.EMAIL, repr(frame.f_locals))
                self.assertNotIn("20123456", repr(frame.f_locals))

    @override_settings(TESTING=False, MERCADOPAGO_ACCESS_TOKEN="APP_USR-plataforma")
    def test_token_del_gimnasio(self):
        error = urllib.error.HTTPError("https://x", 500, "no", {}, BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            frames = self._frames(
                lambda: cobro_mp.acreditar_cuota_por_id(self.cuenta, "501"),
                ErrorDeMercadoPago,
            )
        self.assertTrue(any(f.f_code.co_name == "acreditar_cuota_por_id" for f in frames))
        for frame in frames:
            volcado = repr(frame.f_locals)
            self.assertNotIn("token-del-gimnasio-777", volcado, frame.f_code.co_name)
            self.assertNotIn("Bearer", volcado)
