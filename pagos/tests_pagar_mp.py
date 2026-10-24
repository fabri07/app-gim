"""El alumno paga su cuota con Mercado Pago (T6 de «cuotas por MP»).

`pagos.views.PagarCuotaMPView` / `RetornoCuotaMPView`, `pagos.cobro_mp`
(`cobro_online_disponible`, `link_de_pago`), el botón del portal y los textos
que lo mencionan (portal, pantalla de bloqueado, push y recordatorios).

La suite no sale a la red: se parchean `suscripcion.mercadopago.crear_preferencia`
y `consultar_pago`.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from notificaciones import services as avisos
from pagos import cobro_mp
from pagos.models import CobroOnlineCuota, Cuota
from pagos.testing import crear_cuota
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto
from tenants import suplantacion
from tenants.models import Gimnasio, Perfil
from tienda.models import CuentaMercadoPago

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
CREAR = "suscripcion.mercadopago.crear_preferencia"
CONSULTAR = "suscripcion.mercadopago.consultar_pago"
TOKEN = "APP_USR-token-del-gimnasio-777"
I = CobroOnlineCuota.Estado
C = Cuota.Estado
FORM = 'action="/pagos/{pk}/mercadopago/"'


def preferencia(n=1):
    return {"id": f"pref-{n}", "init_point": f"https://mp.test/checkout/{n}"}


@override_settings(**MP)
class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gym A", slug="a", cuotas_con_mercadopago=True
        )
        self.usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil
        )
        self.cuenta = CuentaMercadoPago.objects.create(
            gimnasio=self.gimnasio, access_token=TOKEN, refresh_token="REF",
            mp_user_id="111", token_expira=timezone.now() + timedelta(days=100),
            estado=CuentaMercadoPago.Estado.CONECTADA, es_de_prueba=True,
        )
        self.hoy = timezone.localdate()
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=5), monto=Decimal("28000"),
        )
        self.client.force_login(self.usuario)

    # --- ayudas ---

    def bloquear(self):
        """Prende el bloqueo y deja la cuota bloqueando."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=3, fecha_activacion_bloqueo=self.hoy - timedelta(days=365)
        )
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=C.VENCIDO, periodo_inicio=self.hoy - timedelta(days=20),
            periodo_fin=self.hoy + timedelta(days=7),
        )

    def suplantar(self):
        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION] = {"inicio": timezone.now().isoformat()}
        sesion.save()

    def pagar(self, cuota=None, devuelve=None, efecto=None):
        with mock.patch(CREAR, return_value=devuelve or preferencia(), side_effect=efecto) as crear:
            r = self.client.post(reverse("pagos:pagar_mp", args=[(cuota or self.cuota).pk]))
        self.crear = crear
        return r

    def mensajes(self, r):
        return [str(m) for m in r.wsgi_request._messages]

    def html_home(self):
        return self.client.get(reverse("home")).content.decode()

    def html_bloqueado(self):
        r = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertTemplateUsed(r, "rutinas/bloqueado.html")
        return r.content.decode()

    def form(self, cuota=None):
        return FORM.format(pk=(cuota or self.cuota).pk)


# --- Visibilidad del botón --------------------------------------------------------


class BotonTests(_Base):
    def test_todo_ok_hay_boton_con_boost_apagado_en_el_inicio(self):
        html = self.html_home()
        self.assertIn(self.form(), html)
        inicio = html.index(self.form())
        etiqueta = html[html.rindex("<form", 0, inicio):html.index(">", inicio)]
        self.assertIn('hx-boost="false"', etiqueta)
        self.assertIn('method="post"', etiqueta)
        self.assertIn("Pagar $ 28.000 con Mercado Pago", html)
        # El comprobante sigue al lado.
        self.assertIn(reverse("pagos:comprobante_subir", args=[self.cuota.pk]), html)

    def test_sin_alias_no_dice_consulta_como_pagar_si_hay_boton(self):
        """Sin alias de transferencia, «consultá con el gimnasio cómo pagar»
        contradice al botón de Mercado Pago que está justo arriba."""
        html = self.html_home()
        self.assertIn(self.form(), html)
        self.assertNotIn("todavía no cargó un alias", html)

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_sin_alias_y_sin_mercado_pago_sigue_el_aviso_de_siempre(self):
        self.assertIn("todavía no cargó un alias", self.html_home())

    def test_todo_ok_hay_boton_en_la_pantalla_de_bloqueado(self):
        self.bloquear()
        html = self.html_bloqueado()
        self.assertIn(self.form(), html)
        self.assertIn('hx-boost="false"', html)

    def assertSinBoton(self):
        self.assertNotIn(self.form(), self.html_home())

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_bandera_apagada(self):
        self.assertSinBoton()

    def test_demo(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.assertSinBoton()

    def test_interruptor_apagado(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=False)
        self.assertSinBoton()

    def test_cuenta_en_reconectar(self):
        CuentaMercadoPago.objects.update(estado=CuentaMercadoPago.Estado.RECONECTAR)
        self.assertSinBoton()

    def test_cuenta_desconectada(self):
        CuentaMercadoPago.objects.update(estado=CuentaMercadoPago.Estado.DESCONECTADA)
        self.assertSinBoton()

    def test_sin_token(self):
        CuentaMercadoPago.objects.update(access_token="")
        self.assertSinBoton()

    def test_sin_cuenta(self):
        CuentaMercadoPago.objects.all().delete()
        self.assertSinBoton()

    def test_monto_cero(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=0)
        self.assertSinBoton()

    def test_suplantando(self):
        self.suplantar()
        self.assertSinBoton()

    def test_suplantando_tampoco_en_bloqueado(self):
        self.bloquear()
        self.suplantar()
        self.assertNotIn(self.form(), self.html_bloqueado())


class QueriesConLaBanderaApagadaTests(_Base):
    """Con la bandera apagada (hoy, en producción) el portal no agrega
    ninguna query: ni la cuenta de MP se consulta."""

    def consultas(self):
        # La primera visita del día escribe la actividad: se descarta.
        self.client.get(reverse("home"))
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("home"))
        return [q["sql"] for q in ctx.captured_queries]

    def test_mismas_queries_con_y_sin_cuenta_e_interruptor(self):
        with override_settings(TIENDA_MP_ENABLED=False):
            con_todo = self.consultas()
            Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=False)
            CuentaMercadoPago.objects.all().delete()
            sin_nada = self.consultas()
        self.assertEqual(len(con_todo), len(sin_nada))
        self.assertFalse(
            [q for q in con_todo if "tienda_cuentamercadopago" in q], con_todo
        )

    def test_con_la_bandera_prendida_es_una_sola_query_mas(self):
        with override_settings(TIENDA_MP_ENABLED=False):
            apagada = len(self.consultas())
        prendida = len(self.consultas())
        self.assertEqual(prendida, apagada + 1)

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_pantalla_de_bloqueado_sin_query_a_la_cuenta(self):
        self.bloquear()
        with CaptureQueriesContext(connection) as ctx:
            self.html_bloqueado()
        self.assertFalse(
            [q for q in ctx.captured_queries if "tienda_cuentamercadopago" in q["sql"]]
        )

    def test_regla_con_la_bandera_apagada_cero_queries(self):
        with override_settings(TIENDA_MP_ENABLED=False), self.assertNumQueries(0):
            self.assertFalse(cobro_mp.cobro_online_disponible(self.gimnasio))
            self.assertEqual(cobro_mp.gimnasios_con_cobro_online(), frozenset())


# --- Pagar ---------------------------------------------------------------------------


class PagarTests(_Base):
    def test_vendedor_de_prueba_con_token_app_usr_usa_el_link_normal(self):
        # `es_de_prueba=True` con `APP_USR-`: el link de sandbox sólo anda con
        # `TEST-` y el pago del alumno fallaba sin crearse.
        resp = {"id": "pref-1", "init_point": "https://real", "sandbox_init_point": "https://sandbox"}
        with mock.patch("suscripcion.mercadopago._pedir", return_value=resp):
            r = self.client.post(reverse("pagos:pagar_mp", args=[self.cuota.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "https://real")

    def test_crea_el_link_con_el_token_del_gimnasio(self):
        r = self.pagar()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "https://mp.test/checkout/1")
        intento = CobroOnlineCuota.objects.get()
        kw = self.crear.call_args.kwargs
        self.assertIsInstance(kw["token"], Secreto)
        self.assertEqual(kw["token"].valor, TOKEN)
        self.assertEqual(kw["referencia_externa"], f"cuota-{intento.referencia_externa}")
        self.assertEqual(kw["monto_ars"], Decimal("28000"))
        self.assertIsNone(kw["statement_descriptor"])
        self.assertNotIn("sandbox", kw)
        self.assertIn(str(self.cuenta.uuid_webhook), kw["url_notificacion"])
        self.assertEqual(
            kw["url_notificacion"],
            "http://testserver" + reverse("tienda:mp_webhook", args=[self.cuenta.uuid_webhook]),
        )
        self.assertEqual(
            kw["url_retorno"],
            "http://testserver" + reverse("pagos:retorno_mp", args=[intento.referencia_externa]),
        )
        self.assertIn("Cuota del", kw["titulo"])
        self.assertIn("Gym A", kw["titulo"])
        self.assertEqual(kw["expira"], intento.expira)
        self.assertEqual(intento.estado, I.CREADO)
        self.assertEqual(intento.monto, Decimal("28000"))
        self.assertEqual(intento.mp_user_id, "111")
        self.assertEqual(intento.preference_id, "pref-1")
        self.assertEqual(intento.init_point, "https://mp.test/checkout/1")
        restante = intento.expira - timezone.now()
        self.assertGreater(restante, timedelta(hours=1, minutes=59))
        self.assertLessEqual(restante, timedelta(hours=cobro_mp.HORAS_VALIDEZ_LINK_CUOTA))

    def test_titulo_largo_se_recorta(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(nombre="X" * 200)
        self.pagar()
        self.assertLessEqual(len(self.crear.call_args.kwargs["titulo"]), cobro_mp.LARGO_TITULO)

    def test_alumno_bloqueado_puede_pagar(self):
        self.bloquear()
        r = self.pagar()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "https://mp.test/checkout/1")

    def test_get_es_405(self):
        r = self.client.get(reverse("pagos:pagar_mp", args=[self.cuota.pk]))
        self.assertEqual(r.status_code, 405)

    def test_cuota_ajena_es_404(self):
        otro_gym = Gimnasio.objects.create(nombre="B", slug="b")
        otro = Alumno.objects.create(gimnasio=otro_gym, nombre="X", apellido="Y")
        ajena = crear_cuota(
            gimnasio=otro_gym, alumno=otro, inicio=self.hoy, monto=Decimal("100")
        )
        self.assertEqual(self.pagar(ajena).status_code, 404)
        # De otro alumno del MISMO gimnasio, también.
        vecino = Alumno.objects.create(gimnasio=self.gimnasio, nombre="V", apellido="W")
        del_vecino = crear_cuota(
            gimnasio=self.gimnasio, alumno=vecino, inicio=self.hoy, monto=Decimal("100")
        )
        self.assertEqual(self.pagar(del_vecino).status_code, 404)
        self.crear.assert_not_called()

    def test_suplantando_se_rechaza_en_el_servidor(self):
        self.suplantar()
        r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.crear.assert_not_called()
        self.assertFalse(CobroOnlineCuota.objects.exists())
        self.assertIn("no podés pagar", " ".join(self.mensajes(r)))

    def test_cuota_pagada_o_anulada_es_mensaje(self):
        for estado in (C.PAGADO, C.ANULADO):
            Cuota.objects.filter(pk=self.cuota.pk).update(estado=estado)
            r = self.pagar()
            self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
            self.assertIn("ya no está pendiente", " ".join(self.mensajes(r)))
        self.assertFalse(CobroOnlineCuota.objects.exists())

    def test_monto_cero_es_mensaje(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=0)
        r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.crear.assert_not_called()

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_bandera_apagada_es_mensaje(self):
        r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.crear.assert_not_called()

    def test_interruptor_apagado_es_mensaje(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=False)
        r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.crear.assert_not_called()

    def test_token_ilegible_marca_reconectar(self):
        # Cifrado con OTRA clave: con la de la app, leerlo da None.
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            cuenta = CuentaMercadoPago.objects.get(pk=self.cuenta.pk)
            cuenta.access_token = "cifrado-con-otra-clave"
            cuenta.save(update_fields=["access_token"])
        self.assertIsNone(CuentaMercadoPago.objects.get(pk=self.cuenta.pk).access_token)
        r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.crear.assert_not_called()
        self.assertFalse(CobroOnlineCuota.objects.exists())
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.RECONECTAR)


class ReusoTests(_Base):
    def test_reusa_el_link_vivo(self):
        self.pagar()
        r = self.pagar()
        self.assertEqual(r["Location"], "https://mp.test/checkout/1")
        self.crear.assert_not_called()
        self.assertEqual(CobroOnlineCuota.objects.count(), 1)

    def assertNoReusa(self):
        viejo = CobroOnlineCuota.objects.get()
        r = self.pagar(devuelve=preferencia(2))
        self.assertEqual(r["Location"], "https://mp.test/checkout/2")
        self.crear.assert_called_once()
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, I.CANCELADO)
        nuevo = CobroOnlineCuota.objects.get(estado=I.CREADO)
        self.assertNotEqual(nuevo.pk, viejo.pk)
        return nuevo

    def test_no_reusa_con_menos_de_30_minutos(self):
        self.pagar()
        CobroOnlineCuota.objects.update(expira=timezone.now() + timedelta(minutes=29))
        self.assertNoReusa()

    def test_no_reusa_con_otro_monto(self):
        self.pagar()
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=Decimal("30000"))
        nuevo = self.assertNoReusa()
        self.assertEqual(nuevo.monto, Decimal("30000"))

    def test_no_reusa_con_otra_cuenta(self):
        self.pagar()
        CuentaMercadoPago.objects.update(mp_user_id="222")
        nuevo = self.assertNoReusa()
        self.assertEqual(nuevo.mp_user_id, "222")

    def test_no_reusa_sin_init_point(self):
        CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=self.cuota, monto=Decimal("28000"),
            mp_user_id="111", expira=timezone.now() + timedelta(hours=2),
        )
        self.assertNoReusa()

    def test_vence_perezosamente_el_expirado(self):
        self.pagar()
        CobroOnlineCuota.objects.update(expira=timezone.now() - timedelta(minutes=1))
        viejo = CobroOnlineCuota.objects.get()
        self.pagar(devuelve=preferencia(2))
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, I.VENCIDO)

    def test_doble_click_integrity_error_reusa_el_otro(self):
        """El otro request insertó su link vivo entre la lectura y el INSERT
        de este (sin lock: SQLite, o la misma fracción de segundo): el unique
        parcial salta y se reusa el del otro. El INSERT del otro no se puede
        simular adentro del savepoint (se desharía con él), así que el link
        del otro ya existe y la primera lectura «no lo ve»."""
        otro = CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=self.cuota, monto=Decimal("28000"),
            mp_user_id="111", expira=timezone.now() + timedelta(hours=2),
            preference_id="pref-otro", init_point="https://mp.test/checkout/otro",
        )
        with mock.patch.object(cobro_mp, "_link_vivo", side_effect=[None, otro]), \
                mock.patch.object(
                    CobroOnlineCuota.objects, "create",
                    side_effect=IntegrityError("cobro_cuota_un_link_vivo"),
                ):
            r = self.pagar()
        self.assertEqual(r["Location"], "https://mp.test/checkout/otro")
        self.crear.assert_not_called()
        self.assertEqual(CobroOnlineCuota.objects.count(), 1)

    def test_doble_click_con_el_otro_sin_link_todavia_es_mensaje(self):
        def otro_request_a_medias(**kwargs):
            CobroOnlineCuota.objects.bulk_create([CobroOnlineCuota(
                gimnasio=self.gimnasio, cuota=self.cuota, monto=Decimal("28000"),
                mp_user_id="111", expira=timezone.now() + timedelta(hours=2),
            )])
            raise IntegrityError("cobro_cuota_un_link_vivo")

        with mock.patch.object(CobroOnlineCuota.objects, "create", side_effect=otro_request_a_medias):
            r = self.pagar()
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.assertIn("Ya estamos abriendo", " ".join(self.mensajes(r)))


class FallasTests(_Base):
    def test_falla_de_mp_cancela_el_intento(self):
        r = self.pagar(efecto=ErrorDeMercadoPago("caído", codigo=500))
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(CobroOnlineCuota.objects.get().estado, I.CANCELADO)
        self.assertIn("No pudimos abrir Mercado Pago", " ".join(self.mensajes(r)))
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.CONECTADA)
        # El siguiente intento crea uno nuevo (el cancelado no traba el unique).
        r = self.pagar()
        self.assertEqual(r["Location"], "https://mp.test/checkout/1")

    def test_401_marca_reconectar(self):
        self.pagar(efecto=ErrorDeMercadoPago("token", codigo=401))
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.RECONECTAR)
        self.assertEqual(CobroOnlineCuota.objects.get().estado, I.CANCELADO)

    def test_el_staff_cobro_en_el_medio_no_va_a_mercado_pago(self):
        from pagos import cobro

        def staff_cobra(**kwargs):
            cuota = Cuota.objects.get(pk=self.cuota.pk)
            cobro.marcar_pagada(cuota, desde=Cuota.ESTADOS_IMPAGOS, medio_pago_texto="Efectivo")
            return preferencia()

        r = self.pagar(efecto=staff_cobra)
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        intento = CobroOnlineCuota.objects.get()
        self.assertEqual(intento.estado, I.CANCELADO)
        self.assertEqual(intento.init_point, "")


# --- Retorno -------------------------------------------------------------------------


class RetornoTests(_Base):
    def setUp(self):
        super().setUp()
        self.intento = CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=self.cuota, monto=Decimal("28000"),
            mp_user_id="111", preference_id="pref-1", init_point="https://mp/1",
            expira=timezone.now() + timedelta(hours=2),
        )

    def url(self, intento=None, **query):
        base = reverse("pagos:retorno_mp", args=[(intento or self.intento).referencia_externa])
        if query:
            base += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        return base

    def pago(self, status="approved", monto=28000, intento=None):
        return {
            "id": 901, "status": status, "transaction_amount": monto, "currency_id": "ARS",
            "external_reference": (intento or self.intento).referencia_mp,
            "date_approved": timezone.now().isoformat(),
        }

    def volver(self, pago=None, efecto=None, **query):
        with mock.patch(CONSULTAR, return_value=pago, side_effect=efecto) as consultar, \
                mock.patch("notificaciones.services.notificar_cuota_mp_al_staff"), \
                mock.patch("notificaciones.services.notificar_cuota_mp_al_alumno"), \
                self.captureOnCommitCallbacks(execute=True):
            r = self.client.get(self.url(**query))
        self.consultar = consultar
        return r

    def test_acredita_con_el_token_del_gimnasio_e_ignora_status(self):
        r = self.volver(self.pago(), payment_id="901", status="rejected")
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.consultar.assert_called_once()
        self.assertEqual(self.consultar.call_args.args[0], "901")
        token = self.consultar.call_args.kwargs["token"]
        self.assertIsInstance(token, Secreto)
        self.assertEqual(token.valor, TOKEN)
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertIn("¡Listo, tu cuota quedó paga!", self.mensajes(r))

    def test_status_approved_sin_pago_aprobado_no_marca_nada(self):
        r = self.volver(self.pago(status="rejected"), payment_id="901", status="approved")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)
        self.assertIn("No se registró ningún pago", " ".join(self.mensajes(r)))

    def test_collection_id_si_no_hay_payment_id_y_null_se_ignora(self):
        self.volver(self.pago(), payment_id="null", collection_id="901")
        self.assertEqual(self.consultar.call_args.args[0], "901")

    def test_sin_pago_no_consulta(self):
        r = self.volver(collection_id="null")
        self.consultar.assert_not_called()
        self.assertIn("No se registró ningún pago", " ".join(self.mensajes(r)))

    def test_paga_pero_otra_cuota_lo_sigue_bloqueando(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=3, fecha_activacion_bloqueo=self.hoy - timedelta(days=365)
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno, inicio=self.hoy - timedelta(days=60),
            estado=C.VENCIDO, monto=Decimal("28000"),
        )
        r = self.volver(self.pago(), payment_id="901")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        texto = " ".join(self.mensajes(r))
        self.assertNotIn("¡Listo", texto)
        self.assertIn("otra cuota impaga", texto)

    def test_error_de_mp_es_estamos_confirmando(self):
        r = self.volver(efecto=ErrorDeMercadoPago("caído", codigo=500), payment_id="901")
        self.assertIn("Estamos confirmando tu pago", " ".join(self.mensajes(r)))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)

    def test_acreditacion_reintentable_es_estamos_confirmando(self):
        from tienda.pagos_mp import AcreditacionReintentable

        with mock.patch.object(
            cobro_mp, "acreditar_cuota_por_id", side_effect=AcreditacionReintentable("x")
        ):
            r = self.client.get(self.url(payment_id="901"))
        self.assertIn("Estamos confirmando tu pago", " ".join(self.mensajes(r)))

    def test_monto_distinto_queda_a_revisar(self):
        r = self.volver(self.pago(monto=1), payment_id="901")
        self.assertIn("revisar", " ".join(self.mensajes(r)))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)

    def test_intento_ajeno_es_404(self):
        vecino = Alumno.objects.create(gimnasio=self.gimnasio, nombre="V", apellido="W")
        cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=vecino, inicio=self.hoy, monto=Decimal("1")
        )
        ajeno = CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=cuota, monto=Decimal("1"), mp_user_id="111",
        )
        with mock.patch(CONSULTAR) as consultar:
            r = self.client.get(self.url(intento=ajeno, payment_id="901"))
        self.assertEqual(r.status_code, 404)
        consultar.assert_not_called()

    def test_never_cache(self):
        r = self.volver(collection_id="null")
        self.assertIn("no-cache", r["Cache-Control"])
        self.assertIn("no-store", r["Cache-Control"])

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_bandera_apagada_no_consulta(self):
        r = self.volver(self.pago(), payment_id="901")
        self.consultar.assert_not_called()
        self.assertIn("Estamos confirmando", " ".join(self.mensajes(r)))

    def test_con_alumnos_bloqueados_no_llega_comportamiento_conocido(self):
        """El middleware de la plataforma corta antes de la vista: el pago lo
        acreditan el webhook y la reconciliación (aceptado)."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS
        )
        r = self.volver(self.pago(), payment_id="901")
        self.assertEqual(r.status_code, 200)
        self.consultar.assert_not_called()
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)


# --- Textos --------------------------------------------------------------------------


class TextosTests(_Base):
    def test_tarjeta_de_bloqueo_menciona_mercado_pago(self):
        self.bloquear()
        html = self.html_home()
        self.assertIn("Pagá con Mercado Pago acá abajo", html)
        self.assertIn("se habilita al instante", html)
        self.assertIn("Pagá con Mercado Pago y tu acceso vuelve", self.html_bloqueado())

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_sin_cobro_online_los_textos_quedan_como_antes(self):
        self.bloquear()
        html = self.html_home()
        self.assertNotIn("Mercado Pago", html)
        self.assertIn("Subí el comprobante acá abajo", html)
        bloqueado = self.html_bloqueado()
        self.assertNotIn("Mercado Pago", bloqueado)
        self.assertIn("Subí el comprobante y el gimnasio", bloqueado)

    def rechazo_al_reservar(self):
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory

        from turnos.views import ReservarView

        request = RequestFactory().post(reverse("turnos:reservar"))
        request.user = User.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied) as ctx:
            ReservarView.as_view()(request)
        return str(ctx.exception)

    def test_reservar_bloqueado_menciona_mercado_pago(self):
        self.bloquear()
        self.assertIn("Si la pagás con Mercado Pago", self.rechazo_al_reservar())

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_reservar_bloqueado_sin_cobro_online_igual_que_antes(self):
        self.bloquear()
        self.assertNotIn("Mercado Pago", self.rechazo_al_reservar())

    def test_privacidad_incluye_las_cuotas(self):
        self.client.logout()
        html = self.client.get(reverse("politica_privacidad")).content.decode()
        self.assertIn("la tienda\n    o las cuotas con Mercado Pago", html)


class AvisosTests(_Base):
    def setUp(self):
        super().setUp()
        self.cuota = Cuota.objects.select_related("gimnasio", "alumno__perfil__usuario").get(
            pk=self.cuota.pk
        )

    def cuerpo(self, funcion, **kw):
        with mock.patch.object(avisos, "notificar_a_usuario") as enviar:
            funcion(self.cuota, **kw)
        return enviar.call_args.args[1]["body"]

    def test_por_vencer_con_cobro_online(self):
        cuerpo = self.cuerpo(avisos.notificar_pago_por_vencer, cobro_online=True)
        self.assertIn("$ 28.000 · podés pagarla con Mercado Pago", cuerpo)

    def test_por_vencer_sin_cobro_online_igual_que_antes(self):
        self.assertNotIn("Mercado Pago", self.cuerpo(avisos.notificar_pago_por_vencer))

    def test_vencida_con_cobro_online(self):
        cuerpo = self.cuerpo(avisos.notificar_pago_vencido, cobro_online=True)
        self.assertIn("podés pagarla con Mercado Pago", cuerpo)

    def test_bloqueo_con_y_sin_cobro_online(self):
        con = self.cuerpo(avisos.notificar_acceso_bloqueado, cobro_online=True)
        self.assertIn("podés pagarla con Mercado Pago", con)
        avisos.RecordatorioEnviado.objects.all().delete()
        sin = self.cuerpo(avisos.notificar_acceso_bloqueado)
        self.assertNotIn("Mercado Pago", sin)
        self.assertIn("Subí el comprobante", sin)

    def test_monto_cero_no_menciona_mercado_pago(self):
        self.cuota.monto = Decimal("0")
        self.assertNotIn(
            "Mercado Pago", self.cuerpo(avisos.notificar_pago_vencido, cobro_online=True)
        )

    def test_pesos_con_centavos(self):
        self.assertEqual(avisos._pesos(Decimal("1234567.5")), "$ 1.234.567,50")
        self.assertEqual(avisos._pesos(Decimal("28000.00")), "$ 28.000")


class RecordatoriosSinNMasUnoTests(TestCase):
    """`enviar_recordatorios` calcula la disponibilidad UNA vez para todo el
    barrido: el costo no crece con la cantidad de gimnasios ni de cuotas."""

    def armar(self, gimnasios, cuotas_por_gimnasio, inicio):
        hoy = timezone.localdate()
        for g in range(gimnasios):
            gimnasio = Gimnasio.objects.create(
                nombre=f"G{inicio + g}", slug=f"g{inicio + g}", cuotas_con_mercadopago=True
            )
            CuentaMercadoPago.objects.create(
                gimnasio=gimnasio, access_token=TOKEN, mp_user_id=str(inicio + g),
                estado=CuentaMercadoPago.Estado.CONECTADA,
            )
            for n in range(cuotas_por_gimnasio):
                alumno = Alumno.objects.create(gimnasio=gimnasio, nombre=f"A{n}", apellido="B")
                cuota = crear_cuota(
                    gimnasio=gimnasio, alumno=alumno, inicio=hoy - timedelta(days=30),
                    monto=Decimal("1000"),
                )
                Cuota.objects.filter(pk=cuota.pk).update(estado=C.VENCIDO)

    def consultas_a_cuentas(self):
        with mock.patch.object(avisos, "notificar_pago_vencido") as vencido, \
                CaptureQueriesContext(connection) as ctx:
            call_command("enviar_recordatorios", stdout=open("/dev/null", "w"))
        self.vencido = vencido
        return len([q for q in ctx.captured_queries if "tienda_cuentamercadopago" in q["sql"]])

    @override_settings(**MP)
    def test_una_sola_query_a_las_cuentas_con_muchos_gimnasios_y_cuotas(self):
        self.armar(1, 1, 0)
        chico = self.consultas_a_cuentas()
        self.armar(4, 3, 10)
        grande = self.consultas_a_cuentas()
        self.assertEqual(chico, 1)
        self.assertEqual(grande, 1)
        self.assertEqual(self.vencido.call_count, 13)
        self.assertTrue(all(c.kwargs["cobro_online"] for c in self.vencido.call_args_list))

    @override_settings(TIENDA_MP_ENABLED=False, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
    def test_bandera_apagada_ninguna_query_y_sin_mercado_pago(self):
        self.armar(2, 2, 0)
        self.assertEqual(self.consultas_a_cuentas(), 0)
        self.assertFalse(any(c.kwargs["cobro_online"] for c in self.vencido.call_args_list))

    @override_settings(**MP)
    def test_demo_e_interruptor_apagado_no_cuentan(self):
        self.armar(3, 1, 0)
        Gimnasio.objects.filter(slug="g0").update(es_demo=True)
        Gimnasio.objects.filter(slug="g1").update(cuotas_con_mercadopago=False)
        self.assertEqual(
            cobro_mp.gimnasios_con_cobro_online(),
            frozenset(Gimnasio.objects.filter(slug="g2").values_list("pk", flat=True)),
        )
