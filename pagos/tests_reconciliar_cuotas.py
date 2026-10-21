"""Reconciliar cuotas cobradas por Mercado Pago (T8 de «cuotas por MP»).

`pagos.cobro_mp.reconciliar_cuotas`, su comando `reconciliar_cuotas_mp`, el
paso del workflow y el pulido de «Ya lo resolví» con pagos adicionales. Sin
red: se parchean `suscripcion.mercadopago.buscar_pagos` y `consultar_pago`."""

import traceback
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from alumnos.models import Alumno
from pagos import cobro, cobro_mp
from pagos.models import CobroOnlineCuota
from pagos.tests_cobro_mp import AL_ALUMNO, AL_STAFF, C, CONSULTAR, TOKEN, I, _Base
from suscripcion.mercadopago import ErrorDeMercadoPago
from tenants.models import Gimnasio
from tienda.models import CuentaMercadoPago

BUSCAR = "suscripcion.mercadopago.buscar_pagos"
RECONECTAR = "notificaciones.services.notificar_cuenta_mp"


class _BaseReconciliar(_Base):
    def otra_cuota(self, dias_atras, **intento):
        """Otra cuota del alumno con su intento (el unique parcial admite un
        solo link vivo por cuota)."""
        cuota = self.crear_cuota(inicio=self.inicio - timedelta(days=28 * dias_atras))
        return self.crear_intento(cuota=cuota, **intento)

    def envejecer(self, intento, **delta):
        CobroOnlineCuota.objects.filter(pk=intento.pk).update(
            creado=timezone.now() - timedelta(**delta)
        )

    def correr(self, pagos_por_id=None, buscar=None, **kwargs):
        pagos_por_id = pagos_por_id or {}

        def consultar(payment_id, token=None):
            return pagos_por_id[str(payment_id)]

        with mock.patch(CONSULTAR, side_effect=consultar) as consultar_mock, \
                mock.patch(BUSCAR, side_effect=buscar or (lambda ref, token=None: [])) as b, \
                mock.patch(AL_STAFF), mock.patch(AL_ALUMNO), mock.patch(RECONECTAR), \
                self.captureOnCommitCallbacks(execute=True):
            resultado = cobro_mp.reconciliar_cuotas(**kwargs)
        self.buscar = b
        self.consultar = consultar_mock
        return resultado

    def refs(self):
        return [c.args[0] for c in self.buscar.call_args_list]


class ReconciliarCuotasTests(_BaseReconciliar):
    def test_link_perdido_aprobado_se_acredita_con_el_token_del_gimnasio(self):
        resultado = self.correr(
            {"501": self.pago()}, buscar=lambda ref, token=None: [{"id": 501}]
        )
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(
            resultado,
            {"revisados": 1, "acreditados": 1, "errores": 0, "salteados": 0, "vencidos": 0},
        )
        self.assertEqual(self.refs(), [self.intento.referencia_mp])
        # Nunca el token de la plataforma: el del gimnasio, en los dos pasos.
        self.assertEqual(self.buscar.call_args.kwargs["token"].valor, TOKEN)
        self.assertEqual(self.consultar.call_args.kwargs["token"].valor, TOKEN)

    def test_filtra_por_estado_no_por_payment_id(self):
        """Un CANCELADO con el id de un rechazo y un reintento aprobado se
        acredita: filtrar por `payment_id` vacío lo perdería."""
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(
            estado=I.CANCELADO, payment_id="400", estado_mp="rejected"
        )
        pagos = {"501": self.pago(), "400": self.pago(id=400, status="rejected")}
        self.correr(pagos, buscar=lambda ref, token=None: [{"id": 501}, {"id": 400}])
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.payment_id, "501")
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_candidatos_por_estado_fecha_y_preferencia(self):
        candidatos = {self.intento.referencia_mp}
        for n, estado in enumerate((I.RECHAZADO, I.CANCELADO, I.VENCIDO), start=1):
            candidatos.add(self.otra_cuota(n, estado=estado).referencia_mp)
        for n, estado in enumerate((I.PAGADO, I.DUPLICADO, I.REVISAR, I.DEVUELTO), start=4):
            self.otra_cuota(n, estado=estado, payment_id=str(n))
        self.otra_cuota(8, estado=I.CANCELADO, preference_id="")
        viejo = self.otra_cuota(9, estado=I.VENCIDO)
        self.envejecer(viejo, days=8)
        self.correr()
        self.assertEqual(set(self.refs()), candidatos)
        self.assertEqual(len(self.refs()), 4)

    def test_un_link_vencido_hace_dias_no_gasta_el_tope(self):
        """Un link vive 2 h y MP no acepta pagos después de `expires`: uno
        vencido hace 3 días no se busca (si no, los links muertos de 7 días
        consumen el tope de 50 y dejan afuera a los que importan). Uno
        vencido hace 2 h sí (aprobación tardía, cron demorado)."""
        ahora = timezone.now()
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(
            estado=I.VENCIDO, expira=ahora - timedelta(days=3)
        )
        reciente = self.otra_cuota(1, estado=I.CANCELADO, expira=ahora - timedelta(hours=2))
        sin_vencimiento = self.otra_cuota(2, estado=I.RECHAZADO, expira=None)
        self.correr()
        self.assertEqual(
            set(self.refs()), {reciente.referencia_mp, sin_vencimiento.referencia_mp}
        )

    def test_del_mas_nuevo_al_mas_viejo_con_tope(self):
        self.envejecer(self.intento, days=2)
        nuevo = self.otra_cuota(1, estado=I.CANCELADO)
        self.correr(limite=1)
        self.assertEqual(self.refs(), [nuevo.referencia_mp])

    def test_vence_los_creado_expirados_y_los_sigue_mirando(self):
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(
            expira=timezone.now() - timedelta(minutes=1)
        )
        vivo = self.otra_cuota(1, expira=timezone.now() + timedelta(hours=1))
        antes = CobroOnlineCuota.objects.get(pk=self.intento.pk).modificado
        resultado = self.correr()
        self.refrescar()
        vivo.refresh_from_db()
        self.assertEqual(self.intento.estado, I.VENCIDO)
        self.assertGreater(self.intento.modificado, antes)
        self.assertEqual(vivo.estado, I.CREADO)
        self.assertEqual(resultado["vencidos"], 1)
        # Un link vencido igual pudo haberse pagado antes de vencer.
        self.assertIn(self.intento.referencia_mp, self.refs())

    def test_cuenta_sin_tokens_se_saltea(self):
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).update(access_token="")
        resultado = self.correr()
        self.buscar.assert_not_called()
        self.assertEqual((resultado["revisados"], resultado["salteados"]), (0, 1))

    def test_cuenta_con_token_ilegible_se_saltea(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=cobro_mp_otra_clave()):
            resultado = self.correr()
        self.buscar.assert_not_called()
        self.assertEqual((resultado["revisados"], resultado["salteados"]), (0, 1))

    def test_sin_cuenta_se_saltea(self):
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).delete()
        resultado = self.correr()
        self.buscar.assert_not_called()
        self.assertEqual(resultado["salteados"], 1)

    def test_link_de_otra_cuenta_de_mp_se_saltea(self):
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(mp_user_id="999")
        resultado = self.correr()
        self.buscar.assert_not_called()
        self.assertEqual((resultado["revisados"], resultado["salteados"]), (0, 1))

    def test_401_marca_reconectar_y_no_consulta_mas_esa_cuenta(self):
        for n in range(1, 3):
            self.otra_cuota(n, estado=I.CANCELADO)
        resultado = self.correr(
            buscar=mock.Mock(side_effect=ErrorDeMercadoPago("x", codigo=401))
        )
        self.assertEqual(self.buscar.call_count, 1)
        self.assertEqual(
            resultado,
            {"revisados": 1, "acreditados": 0, "errores": 1, "salteados": 2, "vencidos": 0},
        )
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.RECONECTAR)

    def test_una_cuenta_con_error_no_frena_a_otra(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        self.crear_cuenta(gimnasio=otro, access_token="TOK-B", mp_user_id="222")
        alumno_b = Alumno.objects.create(gimnasio=otro, nombre="B", apellido="B")
        cuota_b = crear_cuota_b(otro, alumno_b, self.inicio)
        intento_b = CobroOnlineCuota.objects.create(
            gimnasio=otro, cuota=cuota_b, monto=cuota_b.monto, mp_user_id="222",
            preference_id="pref-b",
        )
        # El de B es el más nuevo: va primero y falla.
        CobroOnlineCuota.objects.filter(pk=intento_b.pk).update(
            creado=timezone.now() + timedelta(seconds=5)
        )

        def buscar(ref, token=None):
            if token.valor == "TOK-B":
                raise ErrorDeMercadoPago("x", codigo=500)
            return [{"id": 501}]

        resultado = self.correr({"501": self.pago()}, buscar=buscar)
        self.refrescar()
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(
            (resultado["revisados"], resultado["acreditados"], resultado["errores"]), (2, 1, 1)
        )
        intento_b.refresh_from_db()
        self.assertEqual(intento_b.estado, I.CREADO)

    def test_una_carrera_reintentable_se_cuenta_y_sigue(self):
        from tienda.pagos_mp import AcreditacionReintentable

        self.otra_cuota(1, estado=I.CANCELADO)
        with mock.patch(
            "pagos.cobro_mp.acreditar_cuota_por_id",
            side_effect=[AcreditacionReintentable("x"), None],
        ):
            resultado = self.correr(buscar=lambda ref, token=None: [{"id": 501}])
        self.assertEqual((resultado["revisados"], resultado["errores"]), (2, 1))

    def test_las_cuentas_se_leen_en_una_query(self):
        for n in range(1, 4):
            self.otra_cuota(n, estado=I.CANCELADO)
        # Vencer + candidatos + cuentas.
        with mock.patch(BUSCAR, return_value=[]), self.assertNumQueries(3):
            cobro_mp.reconciliar_cuotas()


def cobro_mp_otra_clave():
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def crear_cuota_b(gimnasio, alumno, inicio):
    from decimal import Decimal

    from pagos.testing import crear_cuota

    return crear_cuota(gimnasio=gimnasio, alumno=alumno, inicio=inicio, monto=Decimal("28000"))


def _relanzar(*args, **kwargs):
    raise RuntimeError("relanzado")


class DatosDelPagadorFueraDeLosFramesTests(_BaseReconciliar):
    """Sentry captura las variables locales de cada frame (su scrubber no es
    recursivo): los resultados de la búsqueda traen los datos del pagador y
    no pueden quedar atados a ningún nombre local de la reconciliación."""

    EMAIL = "pagador-reconocible@secreto.test"

    def test_reconciliar(self):
        try:
            with mock.patch(BUSCAR, return_value=[
                {"id": 501, "payer": {"email": self.EMAIL}, "status": "approved"}
            ]), mock.patch(
                "pagos.cobro_mp.acreditar_cuota_por_id", side_effect=RuntimeError("boom")
            ), mock.patch("pagos.cobro_mp.logger") as log:
                # El `except Exception` traga el error: se lo vuelve a levantar
                # desde el log para mirar la traza que vio el cron.
                log.error.side_effect = _relanzar
                cobro_mp.reconciliar_cuotas()
        except RuntimeError as e:
            traza = e.__traceback__
        else:
            self.fail("debía levantar RuntimeError")
        frames = [f for f, _ in traceback.walk_tb(traza)]
        self.assertTrue(any(f.f_code.co_name == "reconciliar_cuotas" for f in frames))
        for frame in frames:
            self.assertNotIn(self.EMAIL, repr(frame.f_locals))
            self.assertNotIn(TOKEN, repr(frame.f_locals))


class ComandoTests(_BaseReconciliar):
    def correr_comando(self):
        salida = StringIO()
        call_command("reconciliar_cuotas_mp", stdout=salida)
        return salida.getvalue()

    def test_bandera_apagada_sin_cuentas_conectadas_sale_0(self):
        CuentaMercadoPago.objects.update(estado=CuentaMercadoPago.Estado.DESCONECTADA)
        with override_settings(TIENDA_MP_ENABLED=False), mock.patch(BUSCAR) as buscar:
            self.assertIn("apagado", self.correr_comando())
        buscar.assert_not_called()

    def test_bandera_apagada_con_una_cuenta_conectada_sale_1(self):
        with override_settings(TIENDA_MP_ENABLED=False), self.assertRaises(CommandError):
            self.correr_comando()

    def test_prendido_reconcilia(self):
        with mock.patch(BUSCAR, return_value=[{"id": 501}]), \
                mock.patch(CONSULTAR, return_value=self.pago()), \
                mock.patch(AL_STAFF), mock.patch(AL_ALUMNO):
            salida = self.correr_comando()
        self.assertIn("Cobros de cuotas revisados: 1", salida)
        self.assertIn("Acreditados: 1", salida)
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)

    def test_limite(self):
        self.otra_cuota(1, estado=I.CANCELADO)
        salida = StringIO()
        with mock.patch(BUSCAR, return_value=[]) as buscar:
            call_command("reconciliar_cuotas_mp", "--limite", "1", stdout=salida)
        self.assertEqual(buscar.call_count, 1)

    def test_el_paso_del_workflow_va_antes_del_ping_con_su_env_completo(self):
        texto = (Path(settings.BASE_DIR) / ".github/workflows/reconciliar-cobros.yml").read_text()
        pedidos = texto.index("name: Reconciliar pedidos de Mercado Pago de la tienda")
        paso = texto.index("name: Reconciliar cuotas cobradas por Mercado Pago")
        ping = texto.index("name: Avisar a Healthchecks")
        self.assertLess(pedidos, paso)
        self.assertLess(paso, ping)
        bloque = texto[paso:ping]
        self.assertIn("if: ${{ !cancelled() }}", bloque)
        self.assertIn("run: python manage.py reconciliar_cuotas_mp", bloque)
        for variable in (
            "DATABASE_URL", "DJANGO_DEBUG", "MERCADOPAGO_CLIENT_ID",
            "MERCADOPAGO_CLIENT_SECRET", "MERCADOPAGO_OAUTH_REDIRECT_URI",
            "MP_TOKEN_ENCRYPTION_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY",
            "VAPID_ADMIN_EMAIL",
        ):
            self.assertIn(f"{variable}: ", bloque)


class YaLoResolviConAdicionalesTests(_Base):
    """«Ya lo resolví» marca resueltos todos los pagos adicionales: si no, la
    devolución posterior de UNO recalcula `revision_pendiente` y la vuelve a
    prender por los que el staff ya resolvió."""

    def test_la_devolucion_de_un_adicional_despues_no_reabre_la_revision(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=502))
        self.acreditar(self.pago(id=503))
        self.refrescar()
        self.assertTrue(self.intento.revision_pendiente)

        self.assertTrue(cobro.marcar_revision_resuelta(self.intento.pk, self.gimnasio))
        self.refrescar()
        self.assertFalse(self.intento.revision_pendiente)
        adicionales = self.intento.respuesta_mp["pagos_adicionales"]
        self.assertEqual(len(adicionales), 2)
        self.assertTrue(all(a.get("resuelto") for a in adicionales))

        _, staff, alumno = self.acreditar(self.pago(id=502, status="refunded"))
        self.refrescar()
        self.assertFalse(self.intento.revision_pendiente)
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual((staff, alumno), ([], []))

    def test_conserva_la_fecha_de_un_adicional_ya_resuelto(self):
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=502))
        self.acreditar(self.pago(id=502, status="refunded"))
        self.acreditar(self.pago(id=503))
        self.refrescar()
        primera = self.intento.respuesta_mp["pagos_adicionales"][0]["resuelto"]
        cobro.marcar_revision_resuelta(self.intento.pk, self.gimnasio)
        self.refrescar()
        self.assertEqual(self.intento.respuesta_mp["pagos_adicionales"][0]["resuelto"], primera)
        self.assertTrue(self.intento.respuesta_mp["pagos_adicionales"][1]["resuelto"])

    def test_de_otro_gimnasio_o_sin_revision_no_toca_nada(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        self.acreditar(self.pago())
        self.acreditar(self.pago(id=502))
        self.assertFalse(cobro.marcar_revision_resuelta(self.intento.pk, otro))
        self.refrescar()
        self.assertTrue(self.intento.revision_pendiente)
        self.assertFalse(self.intento.respuesta_mp["pagos_adicionales"][0].get("resuelto"))
        self.assertTrue(cobro.marcar_revision_resuelta(self.intento.pk, self.gimnasio))
        self.assertFalse(cobro.marcar_revision_resuelta(self.intento.pk, self.gimnasio))
