"""La pantalla del staff «Pagos con Mercado Pago» (T7 de «cuotas por MP»).

`pagos.views.CobroOnlineListView` / `CobroOnlineResueltoView`, el badge y el
banner del listado de cuotas, la alerta del panel, la URL del push al staff y
los textos al alumno que mencionan Mercado Pago sólo con cobro online.

La suite no sale a la red: acá nada llama a Mercado Pago (los intentos se
crean a mano en el estado que hace falta).
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import services as ingreso_services
from ingreso import tests_ficha_y_portal as portal
from notificaciones import services as avisos
from pagos import cobro
from pagos.models import CobroOnlineCuota, Cuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio, Perfil
from tienda.models import CuentaMercadoPago

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
I = CobroOnlineCuota.Estado
M = CobroOnlineCuota.MotivoRevision
C = Cuota.Estado
TABLA = "pagos_cobroonlinecuota"
BADGE = 'badge--neutro">Mercado Pago</span>'


@override_settings(**MP)
class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gym A", slug="a", cuotas_con_mercadopago=True
        )
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.hoy = timezone.localdate()
        self.alumno = self.nuevo_alumno(self.gimnasio, "Ana")
        self.client.force_login(self.staff)

    # --- ayudas ---

    _n = 0

    def nuevo_alumno(self, gimnasio, nombre):
        return Alumno.objects.create(gimnasio=gimnasio, nombre=nombre, apellido="G")

    def cuota(self, alumno=None, *, estado=C.PAGADO, dias=5, monto="28000", **extra):
        alumno = alumno or self.alumno
        _Base._n += 1
        return crear_cuota(
            gimnasio=alumno.gimnasio, alumno=alumno,
            inicio=self.hoy - timedelta(days=dias + _Base._n * 30),
            monto=Decimal(monto), estado=estado, **extra,
        )

    def intento(self, cuota=None, *, estado=I.PAGADO, payment_id=None, **extra):
        cuota = cuota or self.cuota()
        _Base._n += 1
        return CobroOnlineCuota.objects.create(
            gimnasio=cuota.gimnasio, cuota=cuota, monto=cuota.monto or Decimal("28000"),
            estado=estado, mp_user_id="111",
            payment_id=payment_id if payment_id is not None else f"9{_Base._n:05d}",
            acreditado_en=timezone.now(), **extra,
        )

    def revisar(self, motivo, *, estado=I.REVISAR, cuota=None, **extra):
        return self.intento(
            cuota, estado=estado, motivo_revision=motivo, revision_pendiente=True, **extra
        )

    def html(self, url_name="pagos:online", query=""):
        respuesta = self.client.get(reverse(url_name) + query)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.content.decode()


# --- Listado y pestañas ----------------------------------------------------------


class ListadoTests(_Base):
    def test_lista_los_que_movieron_plata_y_no_los_links_sin_pagar(self):
        con_plata = {
            estado: self.intento(estado=estado, payment_id=f"pago-{estado}")
            for estado in (I.PAGADO, I.DUPLICADO, I.REVISAR, I.DEVUELTO)
        }
        sin_plata = {
            estado: self.intento(
                self.cuota(estado=C.PENDIENTE), estado=estado, payment_id=f"pago-{estado}"
            )
            for estado in (I.RECHAZADO, I.VENCIDO, I.CANCELADO)
        }
        self.intento(self.cuota(estado=C.PENDIENTE), estado=I.CREADO, payment_id="")
        html = self.html()
        for estado in con_plata:
            self.assertIn(f"pago-{estado}", html)
        for estado in sin_plata:
            self.assertNotIn(f"pago-{estado}", html)
        self.assertIn("Todos (4)", html)

    def test_uno_sin_plata_con_revision_pendiente_tambien_aparece(self):
        self.intento(
            self.cuota(estado=C.PENDIENTE), estado=I.CANCELADO, payment_id="pago-raro",
            revision_pendiente=True,
        )
        self.assertIn("pago-raro", self.html(query="?filtro=revisar"))

    def test_cada_fila_muestra_alumno_periodo_monto_fecha_estado_y_pago(self):
        cuota = self.cuota(monto="28000")
        self.intento(cuota, payment_id="123456789")
        html = self.html()
        self.assertIn("G, Ana", html)
        self.assertIn(f"{cuota.periodo_inicio:%d/%m/%Y} al {cuota.periodo_fin:%d/%m/%Y}", html)
        self.assertIn("$ 28.000", html)
        self.assertIn(f"{timezone.localdate():%d/%m/%Y}", html)
        self.assertIn("Pagado", html)
        self.assertIn("123456789", html)

    def test_pestana_revisar_solo_lo_pendiente(self):
        self.intento(payment_id="normal")
        self.revisar(M.MONTO_DISTINTO, payment_id="a-revisar")
        # Un REVISAR cuya revisión ya se resolvió sale de la pestaña.
        self.intento(
            estado=I.REVISAR, motivo_revision=M.CUOTA_ANULADA, payment_id="pago-cerrado",
            revision_pendiente=False, revision_resuelta_en=timezone.now(),
        )
        html = self.html(query="?filtro=revisar")
        self.assertIn("a-revisar", html)
        self.assertNotIn("normal", html)
        self.assertNotIn("pago-cerrado", html)
        self.assertIn("Revisar (1)", html)
        self.assertIn("Todos (3)", html)

    def test_filtro_desconocido_es_todos(self):
        self.intento(payment_id="normal")
        self.assertIn("normal", self.html(query="?filtro=cualquiera"))

    def test_pagina_de_a_20(self):
        for _ in range(21):
            self.intento()
        html = self.html()
        self.assertIn("Página 1 de 2", html)
        self.assertEqual(html.count("<tr>") - 1, 20)  # menos el encabezado

    def test_vacio(self):
        self.assertIn("Todavía no hay pagos con Mercado Pago.", self.html())
        self.assertIn("No hay pagos para revisar.", self.html(query="?filtro=revisar"))

    def test_sin_datos_del_pagador(self):
        self.intento(respuesta_mp={"id": "1", "payer": {"email": "pagador@mail.test"}})
        self.assertNotIn("pagador@mail.test", self.html())


class ExplicacionTests(_Base):
    def test_una_explicacion_por_motivo(self):
        esperado = {
            M.MONTO_DISTINTO: "Mercado Pago cobró un monto distinto al de la cuota",
            M.CUOTA_YA_PAGADA: "La cuota ya estaba paga: este pago está de más, devolvelo",
            M.CUOTA_ANULADA: "La cuota estaba anulada y Mercado Pago igual cobró",
            M.MONTO_CORREGIDO: "con lo que cobró Mercado Pago ($ 28.000)",
            M.DEVUELTO: "Mercado Pago devolvió este pago: si corresponde, volvé la cuota a impaga",
        }
        estados = {
            M.CUOTA_YA_PAGADA: I.DUPLICADO, M.MONTO_CORREGIDO: I.PAGADO, M.DEVUELTO: I.DEVUELTO,
        }
        for motivo in esperado:
            self.revisar(motivo, estado=estados.get(motivo, I.REVISAR))
        html = self.html(query="?filtro=revisar")
        for texto in esperado.values():
            self.assertIn(texto, html)

    def test_sin_revision_pendiente_no_hay_explicacion_ni_boton(self):
        self.intento(
            estado=I.DUPLICADO, motivo_revision=M.CUOTA_YA_PAGADA,
            revision_resuelta_en=timezone.now(),
        )
        html = self.html()
        self.assertNotIn("devolvelo", html)
        self.assertNotIn("Ya lo resolví", html)

    def test_pagos_adicionales_sin_resolver_con_su_numero(self):
        self.intento(
            revision_pendiente=True,
            respuesta_mp={
                "id": "1",
                "pagos_adicionales": [
                    {"payment_id": "777", "status": "approved", "detectado": "x"},
                    {"payment_id": "888", "status": "approved", "resuelto": "y"},
                ],
            },
        )
        html = self.html(query="?filtro=revisar")
        self.assertIn("devolvé el pago N° 777 desde Mercado Pago", html)
        self.assertNotIn("N° 888", html)


# --- «Ya lo resolví» ---------------------------------------------------------------


class ResueltoTests(_Base):
    def post(self, intento, **data):
        return self.client.post(
            reverse("pagos:online_resuelto", args=[intento.pk]), data, follow=True
        )

    def test_lo_saca_de_revisar(self):
        intento = self.revisar(M.MONTO_DISTINTO)
        respuesta = self.post(intento, filtro="revisar")
        self.assertRedirects(respuesta, reverse("pagos:online") + "?filtro=revisar")
        self.assertContains(respuesta, "Listo, lo marcamos como resuelto.")
        intento.refresh_from_db()
        self.assertFalse(intento.revision_pendiente)
        self.assertIsNotNone(intento.revision_resuelta_en)
        self.assertGreater(intento.modificado, intento.creado)
        self.assertEqual(intento.estado, I.REVISAR)  # sólo la revisión
        self.assertIn("Revisar (0)", respuesta.content.decode())

    def test_ya_resuelto_no_se_toca_y_es_cartel(self):
        momento = timezone.now() - timedelta(days=3)
        intento = self.intento(
            estado=I.REVISAR, motivo_revision=M.MONTO_DISTINTO, revision_resuelta_en=momento
        )
        respuesta = self.post(intento)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "ya no tenía nada pendiente")
        intento.refresh_from_db()
        self.assertEqual(intento.revision_resuelta_en, momento)

    def test_el_de_otro_gimnasio_no_se_toca(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        ajeno = self.revisar(
            M.MONTO_DISTINTO, cuota=self.cuota(self.nuevo_alumno(otro, "Bea"))
        )
        respuesta = self.post(ajeno)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "ya no tenía nada pendiente")
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.revision_pendiente)

    def test_get_no_resuelve(self):
        intento = self.revisar(M.MONTO_DISTINTO)
        respuesta = self.client.get(reverse("pagos:online_resuelto", args=[intento.pk]))
        self.assertEqual(respuesta.status_code, 405)
        intento.refresh_from_db()
        self.assertTrue(intento.revision_pendiente)


# --- «Volver a impaga» -------------------------------------------------------------


class VolverAImpagaTests(_Base):
    FORM = 'action="/pagos/{pk}/volver-a-impaga/"'

    def test_solo_con_la_cuota_paga_y_el_pago_devuelto(self):
        devuelta = self.cuota()
        self.revisar(M.DEVUELTO, estado=I.DEVUELTO, cuota=devuelta)
        # Pagada por MP sin devolución.
        pagada = self.cuota()
        self.intento(pagada)
        # Devuelto, pero la cuota tiene OTRO intento PAGADO.
        con_otro_pago = self.cuota()
        self.revisar(M.DEVUELTO, estado=I.DEVUELTO, cuota=con_otro_pago)
        self.intento(con_otro_pago)
        # Devuelto con la cuota ya vuelta a impaga.
        ya_impaga = self.cuota(estado=C.VENCIDO)
        self.revisar(M.DEVUELTO, estado=I.DEVUELTO, cuota=ya_impaga)

        html = self.html()
        self.assertIn(self.FORM.format(pk=devuelta.pk), html)
        for cuota in (pagada, con_otro_pago, ya_impaga):
            self.assertNotIn(self.FORM.format(pk=cuota.pk), html)
        # Coincide con la regla del dominio.
        self.assertTrue(cobro.puede_volver_a_impaga(devuelta.pk))
        self.assertFalse(cobro.puede_volver_a_impaga(con_otro_pago.pk))

    def test_desde_la_pantalla_vuelve_a_la_pantalla(self):
        cuota = self.cuota()
        intento = self.revisar(M.DEVUELTO, estado=I.DEVUELTO, cuota=cuota)
        respuesta = self.client.post(
            reverse("pagos:volver_a_impaga", args=[cuota.pk]), {"volver_a": "online"}
        )
        self.assertRedirects(respuesta, reverse("pagos:online"))
        cuota.refresh_from_db()
        self.assertIn(cuota.estado, Cuota.ESTADOS_IMPAGOS)
        intento.refresh_from_db()
        self.assertFalse(intento.revision_pendiente)

    def test_sin_el_campo_sigue_volviendo_al_listado(self):
        cuota = self.cuota()
        self.revisar(M.DEVUELTO, estado=I.DEVUELTO, cuota=cuota)
        respuesta = self.client.post(reverse("pagos:volver_a_impaga", args=[cuota.pk]))
        self.assertRedirects(respuesta, reverse("pagos:listado"))


class LinksALaCuotaTests(_Base):
    def test_segun_el_estado_de_la_cuota(self):
        impaga = self.cuota(estado=C.PENDIENTE)
        self.revisar(M.MONTO_DISTINTO, cuota=impaga)
        anulada = self.cuota(estado=C.ANULADO)
        self.revisar(M.CUOTA_ANULADA, cuota=anulada)
        pagada = self.cuota()
        self.intento(pagada)
        html = self.html()
        for nombre in ("confirmar", "monto", "anular"):
            self.assertIn(reverse(f"pagos:{nombre}", args=[impaga.pk]), html)
        self.assertIn(reverse("pagos:anular", args=[anulada.pk]), html)
        self.assertNotIn(reverse("pagos:confirmar", args=[anulada.pk]), html)
        self.assertIn(reverse("pagos:confirmar", args=[pagada.pk]), html)
        self.assertNotIn(reverse("pagos:monto", args=[pagada.pk]), html)
        self.assertNotIn(reverse("pagos:anular", args=[pagada.pk]), html)


# --- Escala, aislamiento y permisos -----------------------------------------------


class EscalaTests(_Base):
    def consultas(self):
        with CaptureQueriesContext(connection) as ctx:
            self.html()
        return len(ctx.captured_queries)

    def sembrar(self, n):
        for i in range(n):
            alumno = self.nuevo_alumno(self.gimnasio, f"Alumno{i}")
            cuota = self.cuota(alumno)
            if i % 2:
                self.revisar(
                    M.DEVUELTO, estado=I.DEVUELTO, cuota=cuota,
                    respuesta_mp={"pagos_adicionales": [{"payment_id": str(i)}]},
                )
            else:
                self.intento(cuota)

    def test_costo_constante_con_2_y_con_20_intentos(self):
        self.html()  # la primera visita del día escribe la actividad
        self.sembrar(2)
        con_2 = self.consultas()
        self.sembrar(18)
        self.assertEqual(self.consultas(), con_2)

    def test_el_listado_de_cuotas_tampoco_crece(self):
        self.html("pagos:listado")
        self.sembrar(2)
        with CaptureQueriesContext(connection) as ctx:
            self.html("pagos:listado")
        con_2 = len(ctx.captured_queries)
        self.sembrar(18)
        with CaptureQueriesContext(connection) as ctx:
            self.html("pagos:listado")
        self.assertEqual(len(ctx.captured_queries), con_2)


class AislamientoTests(_Base):
    def test_no_ve_los_de_otro_gimnasio_ni_los_cuenta(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        self.revisar(
            M.MONTO_DISTINTO, cuota=self.cuota(self.nuevo_alumno(otro, "Bea")),
            payment_id="ajeno",
        )
        self.intento(payment_id="propio")
        html = self.html()
        self.assertIn("propio", html)
        self.assertNotIn("ajeno", html)
        self.assertIn("Todos (1)", html)
        self.assertIn("Revisar (0)", html)
        self.assertEqual(
            cobro.contadores_cobro_online(self.gimnasio), {"total": 1, "a_revisar": 0}
        )
        self.assertEqual(cobro.contadores_cobro_online(otro), {"total": 1, "a_revisar": 1})

    def test_el_alumno_no_entra(self):
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = perfil
        self.alumno.save()
        intento = self.revisar(M.MONTO_DISTINTO, payment_id="secreto")
        self.client.force_login(usuario)
        respuesta = self.client.get(reverse("pagos:online"))
        self.assertIn(respuesta.status_code, (302, 403))
        self.assertNotIn("secreto", respuesta.content.decode())
        respuesta = self.client.post(reverse("pagos:online_resuelto", args=[intento.pk]))
        self.assertIn(respuesta.status_code, (302, 403))
        intento.refresh_from_db()
        self.assertTrue(intento.revision_pendiente)

    def test_sin_login_va_al_login(self):
        self.client.logout()
        respuesta = self.client.get(reverse("pagos:online"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("login", respuesta["Location"])


class BanderaApagadaTests(_Base):
    @override_settings(TIENDA_MP_ENABLED=False)
    def test_sin_intentos_no_hay_pantalla_ni_link(self):
        respuesta = self.client.get(reverse("pagos:online"), follow=True)
        self.assertRedirects(respuesta, reverse("pagos:listado"))
        self.assertContains(respuesta, "Todavía no hay pagos con Mercado Pago.")
        self.assertNotContains(respuesta, 'href="/pagos/mercadopago/"')

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_con_intentos_se_ve_igual(self):
        self.revisar(M.MONTO_DISTINTO, payment_id="pendiente")
        self.assertIn("pendiente", self.html())
        listado = self.html("pagos:listado")
        self.assertIn('href="/pagos/mercadopago/"', listado)
        self.assertIn("1 pago de Mercado Pago para revisar", listado)


# --- Listado de cuotas: badge y banner ----------------------------------------------


class BadgeYBannerTests(_Base):
    def test_badge_solo_desde_un_intento_pagado(self):
        por_mp = self.cuota(medio_pago_texto="Mercado Pago")
        self.intento(por_mp)
        # Una transferencia al alias que el staff anotó como «Mercado Pago».
        self.cuota(medio_pago_texto="Mercado Pago")
        # Un intento DEVUELTO no es «pagada con Mercado Pago».
        self.intento(self.cuota(), estado=I.DEVUELTO)
        self.assertEqual(self.html("pagos:listado").count(BADGE), 1)

    def test_banner_con_el_conteo_y_link_a_revisar(self):
        self.revisar(M.MONTO_DISTINTO)
        self.revisar(M.CUOTA_YA_PAGADA, estado=I.DUPLICADO)
        self.intento()
        html = self.html("pagos:listado")
        self.assertIn("2 pagos de Mercado Pago para revisar", html)
        self.assertIn('href="/pagos/mercadopago/?filtro=revisar"', html)
        self.assertIn('href="/pagos/mercadopago/">Pagos con Mercado Pago', html)

    def test_sin_nada_para_revisar_no_hay_banner_pero_si_link(self):
        self.intento()
        html = self.html("pagos:listado")
        self.assertNotIn("para revisar", html)
        self.assertIn("Pagos con Mercado Pago", html)

    def test_contador_es_una_query_agregada(self):
        for _ in range(5):
            self.revisar(M.MONTO_DISTINTO)
        with self.assertNumQueries(1):
            self.assertEqual(
                cobro.contadores_cobro_online(self.gimnasio), {"total": 5, "a_revisar": 5}
            )


# --- Panel del staff ---------------------------------------------------------------


class PanelTests(_Base):
    ALERTA = "de Mercado Pago para revisar"

    def consultas(self):
        self.client.get(reverse("home"))  # la primera visita escribe la actividad
        with CaptureQueriesContext(connection) as ctx:
            html = self.html("home")
        return html, [q["sql"] for q in ctx.captured_queries]

    def test_alerta_con_el_conteo(self):
        self.revisar(M.MONTO_DISTINTO)
        self.revisar(M.DEVUELTO, estado=I.DEVUELTO)
        html, _ = self.consultas()
        self.assertIn("2 pagos de Mercado Pago para revisar", html)
        self.assertIn('href="/pagos/mercadopago/?filtro=revisar"', html)

    def test_sin_nada_para_revisar_no_hay_alerta(self):
        self.intento()
        html, _ = self.consultas()
        self.assertNotIn(self.ALERTA, html)

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_bandera_e_interruptor_apagados_ninguna_query_de_mas(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=False)
        html, sql = self.consultas()
        self.assertNotIn(self.ALERTA, html)
        self.assertFalse([q for q in sql if TABLA in q], sql)

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_bandera_apagada_con_plata_por_revisar_la_alerta_sigue(self):
        self.revisar(M.CUOTA_YA_PAGADA, estado=I.DUPLICADO)
        html, sql = self.consultas()
        self.assertIn("1 pago de Mercado Pago para revisar", html)
        self.assertEqual(len([q for q in sql if TABLA in q]), 1)

    def test_con_la_bandera_es_una_sola_query(self):
        html, sql = self.consultas()
        self.assertEqual(len([q for q in sql if TABLA in q]), 1)

    def test_no_cuenta_los_de_otro_gimnasio(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        self.revisar(M.MONTO_DISTINTO, cuota=self.cuota(self.nuevo_alumno(otro, "Bea")))
        html, _ = self.consultas()
        self.assertNotIn(self.ALERTA, html)


# --- Push al staff -----------------------------------------------------------------


class PushTests(_Base):
    def url(self, aviso):
        intento = CobroOnlineCuota.objects.select_related("cuota__alumno", "gimnasio").get(
            pk=self.intento().pk
        )
        with mock.patch.object(avisos, "notificar_a_gimnasio") as enviar:
            avisos.notificar_cuota_mp_al_staff(intento, aviso)
        self.assertEqual(enviar.call_args.kwargs["rol"], Perfil.Rol.STAFF)
        return enviar.call_args.args[1]["url"]

    def test_pago_normal_va_a_la_pantalla(self):
        self.assertEqual(self.url("pago_mp"), reverse("pagos:online"))

    def test_lo_demas_va_a_revisar(self):
        for aviso in ("revisar_mp", "pago_duplicado", "devuelto_mp"):
            self.assertEqual(self.url(aviso), reverse("pagos:online") + "?filtro=revisar")


# --- Portal del alumno: formato del monto y textos ---------------------------------


@override_settings(**MP)
class _BaseAlumno(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gym A", slug="a", cuotas_con_mercadopago=True
        )
        # Por UPDATE, como `tests_pagar_mp._Base.bloquear`: el `save()` no
        # estampa la activación del bloqueo.
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=3,
            fecha_activacion_bloqueo=timezone.localdate() - timedelta(days=365),
        )
        self.gimnasio.refresh_from_db()
        self.usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil
        )
        CuentaMercadoPago.objects.create(
            gimnasio=self.gimnasio, access_token="APP_USR-x", refresh_token="REF",
            mp_user_id="111", token_expira=timezone.now() + timedelta(days=100),
            estado=CuentaMercadoPago.Estado.CONECTADA,
        )
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=20),
            monto=Decimal("1234567.50"), estado=C.VENCIDO,
        )
        self.client.force_login(self.usuario)


class FormatoDelMontoTests(_BaseAlumno):
    def test_miles_con_punto_y_centavos_solo_si_hay(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Pagar $ 1.234.567,50 con Mercado Pago", html)
        self.assertIn("$ 1.234.567,50</span>", html)
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=Decimal("28000.00"))
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Pagar $ 28.000 con Mercado Pago", html)
        self.assertNotIn("28000", html)


class TextosCondicionadosTests(_BaseAlumno):
    MP_TEXTO = "Si la pagás con Mercado Pago, se habilita al instante."

    def rechazo_al_calificar(self):
        from rutinas.views import _cortar_si_esta_bloqueado

        with self.assertRaises(PermissionDenied) as ctx:
            _cortar_si_esta_bloqueado(self.alumno, self.gimnasio)
        return str(ctx.exception)

    def test_calificar_bloqueado_menciona_mercado_pago(self):
        self.assertIn(self.MP_TEXTO, self.rechazo_al_calificar())

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_calificar_bloqueado_sin_cobro_online_igual_que_antes(self):
        self.assertEqual(
            self.rechazo_al_calificar(),
            "Tu acceso a la rutina está pausado hasta que se registre el pago de tu cuota.",
        )

    @override_settings(TIENDA_MP_ENABLED=False)
    def test_calificar_sin_bandera_cero_queries_de_mas(self):
        from pagos import cobro_mp

        with self.assertNumQueries(0):
            cobro_mp.cobro_online_disponible(self.gimnasio)


class PuertaTests(portal._BasePortal):
    """«La puerta no te va a dejar pasar hasta que el gimnasio registre el
    pago»: en el inicio y en «Tu código para entrar», con Mercado Pago sólo si
    el alumno puede pagar online."""

    MP_TEXTO = "Si la pagás con Mercado Pago, se habilita al instante."

    def setUp(self):
        super().setUp()
        ingreso_services.generar_credencial_qr(self.alumno)
        self._deuda(puerta_rechaza=True)

    def conectar(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=True)
        Cuota.objects.filter(alumno=self.alumno).update(monto=Decimal("28000"))
        CuentaMercadoPago.objects.create(
            gimnasio=self.gimnasio, access_token="APP_USR-x", refresh_token="REF",
            mp_user_id="111", token_expira=timezone.now() + timedelta(days=100),
            estado=CuentaMercadoPago.Estado.CONECTADA,
        )

    def test_con_cobro_online_mi_codigo_y_el_inicio_lo_mencionan(self):
        with override_settings(**MP):
            self.conectar()
            mi_codigo = self._mi_codigo().content.decode()
            inicio = self.client.get(reverse("home")).content.decode()
        self.assertIn("la puerta no te va a dejar pasar", mi_codigo)
        self.assertIn("la pagás con Mercado Pago, se habilita al instante.", mi_codigo)
        self.assertIn("hasta que el gimnasio registre el pago. " + self.MP_TEXTO, inicio)

    def test_sin_cobro_online_quedan_como_antes(self):
        mi_codigo = self._mi_codigo().content.decode()
        inicio = self.client.get(reverse("home")).content.decode()
        self.assertIn("la puerta no te va a dejar pasar", mi_codigo)
        self.assertNotIn("Mercado Pago", mi_codigo)
        self.assertIn(
            "la puerta no te va a dejar pasar hasta que el gimnasio registre el pago.</p>",
            inicio,
        )

    def test_mi_codigo_sin_bandera_no_consulta_la_cuenta(self):
        self._login("alumno-a")
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("ingreso:mi_codigo"))
        self.assertFalse(
            [q for q in ctx.captured_queries if "tienda_cuentamercadopago" in q["sql"]]
        )
