"""Tests de la Tarea 13: la credencial de ingreso en la ficha del alumno, la
acción en bloque del listado, y el código en el portal del alumno.

Lo que se fija acá, además de las guardias de siempre:

- El QR es una llave al portador: solo el propio alumno ve el suyo, y toda
  respuesta que lo dibuja va con `no-store, private` y `hx-history="false"`
  (sin eso htmx la guarda en `localStorage` y el «atrás» la devuelve).
- Un QR que no abre la puerta (dado de baja, o de un alumno inactivo) no se
  dibuja: una imagen de QR se lee como «esto funciona».
- El alumno bloqueado por deuda ve igual su código; lo que cambia es el aviso.
- La tarjeta del portal y los últimos ingresos de la ficha tienen costo fijo.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from unittest.mock import patch

from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import presentacion, qr, services, tokens
from ingreso.models import ConfiguracionIngreso, Credencial, Dispositivo, EventoIngreso
from ingreso.tests_vistas import _BaseVistas
from pagos.models import Cuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio, Perfil

ATRIBUTO_HISTORIAL = 'hx-history="false"'


def _no_se_guarda(test, respuesta):
    control = respuesta.headers.get("Cache-Control", "")
    test.assertIn("no-store", control)
    test.assertIn("private", control)


# ---------------------------------------------------------------------------
# Ficha del alumno
# ---------------------------------------------------------------------------


class FichaCredencialTests(_BaseVistas):
    def _ficha(self, alumno=None):
        self._login()
        return self.client.get(
            reverse("alumnos:detalle", args=[(alumno or self.alumno).pk])
        )

    def test_sin_codigo_ofrece_generarlo(self):
        respuesta = self._ficha()

        self.assertContains(respuesta, "Credencial de ingreso")
        self.assertContains(
            respuesta,
            f'action="{reverse("ingreso:credencial_qr", args=[self.alumno.pk])}"',
        )
        self.assertContains(respuesta, "Generar código")

    def test_con_qr_vigente_lo_dibuja_con_su_fecha(self):
        credencial = services.generar_credencial_qr(self.alumno)

        respuesta = self._ficha()

        self.assertContains(respuesta, qr.svg_de(credencial.valor), html=False)
        fecha = timezone.localtime(credencial.modificado).strftime("%d/%m/%Y")
        self.assertContains(respuesta, f"Vigente desde el {fecha}")
        self.assertContains(
            respuesta, reverse("ingreso:credencial_qr", args=[self.alumno.pk])
        )
        self.assertContains(
            respuesta, reverse("ingreso:credencial_baja", args=[credencial.pk])
        )

    def test_la_respuesta_con_el_qr_no_se_guarda_en_ningun_cache(self):
        services.generar_credencial_qr(self.alumno)

        respuesta = self._ficha()

        _no_se_guarda(self, respuesta)
        self.assertContains(respuesta, ATRIBUTO_HISTORIAL)

    def test_un_qr_dado_de_baja_no_se_dibuja(self):
        credencial = services.generar_credencial_qr(self.alumno)
        services.dar_de_baja_credencial(credencial)

        respuesta = self._ficha()

        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)
        self.assertContains(respuesta, "Dado de baja")
        self.assertContains(respuesta, "Generar un código nuevo")

    def test_el_qr_de_un_alumno_inactivo_no_se_dibuja_como_valido(self):
        credencial = services.generar_credencial_qr(self.alumno)
        Alumno.objects.filter(pk=self.alumno.pk).update(estado=Alumno.Estado.INACTIVO)

        respuesta = self._ficha()

        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)
        self.assertContains(respuesta, "No abre la puerta")

    def test_muestra_el_id_facial_y_el_link_para_cargarlo(self):
        services.guardar_credencial_facial(self.alumno, "42")

        respuesta = self._ficha()

        self.assertContains(respuesta, "Usuario N° <strong>42</strong>", html=False)
        self.assertContains(
            respuesta, reverse("ingreso:credencial_facial", args=[self.alumno.pk])
        )

    def test_sin_id_facial_ofrece_cargarlo(self):
        respuesta = self._ficha()

        self.assertContains(respuesta, "Cargar número del lector facial")

    def test_ultimos_ingresos_con_la_hora_local(self):
        # 01:30 UTC del 10/03 son las 22:30 del 09/03 en Buenos Aires.
        self._evento(
            alumno=self.alumno,
            momento=datetime(2026, 3, 10, 1, 30, tzinfo=dt_timezone.utc),
        )

        respuesta = self._ficha()

        self.assertContains(respuesta, "09/03/2026 22:30")
        self.assertContains(
            respuesta,
            f'{reverse("ingreso:listado")}?alumno={self.alumno.pk}',
        )

    def test_ultimos_ingresos_son_solo_los_de_este_alumno(self):
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Otro", apellido="X")
        self._evento(
            alumno=otro,
            momento=datetime(2026, 3, 10, 15, 0, tzinfo=dt_timezone.utc),
        )

        respuesta = self._ficha()

        self.assertNotContains(respuesta, "10/03/2026 12:00")
        self.assertContains(respuesta, "Todavía no pasó por la puerta")

    def _queries_de_la_ficha(self, cantidad):
        for n in range(cantidad):
            # Un lector distinto por evento: un N+1 sobre `dispositivo` se ve.
            lector = Dispositivo.objects.create(
                gimnasio=self.gimnasio, nombre=f"Lector {cantidad}-{n}",
                token_hash=tokens.hash_de(tokens.generar()),
            )
            self._evento(dispositivo=lector, alumno=self.alumno)
        self._login()
        url = reverse("alumnos:detalle", args=[self.alumno.pk])
        self.client.get(url)  # calienta la sesión y el registro del día
        with CaptureQueriesContext(connection) as capturadas:
            respuesta = self.client.get(url)
        self.assertContains(respuesta, f"Lector {cantidad}-0")
        return len(capturadas)

    def test_los_ultimos_ingresos_tienen_costo_fijo(self):
        con_uno = self._queries_de_la_ficha(1)
        EventoIngreso.objects.all().delete()
        con_cinco = self._queries_de_la_ficha(5)

        self.assertEqual(con_uno, con_cinco)


# ---------------------------------------------------------------------------
# Regenerar y dar de baja piden confirmación
# ---------------------------------------------------------------------------


class ConfirmacionesTests(_BaseVistas):
    def test_regenerar_pide_confirmacion_y_el_get_no_cambia_nada(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:credencial_qr", args=[self.alumno.pk])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "ingreso/credencial_qr_regenerar_confirmar.html")
        self.assertContains(respuesta, 'name="regenerar" value="1"')
        valor = credencial.valor
        credencial.refresh_from_db()
        self.assertEqual(credencial.valor, valor)

    def test_regenerar_sin_codigo_vuelve_a_la_ficha(self):
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:credencial_qr", args=[self.alumno.pk])
        )

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        self.assertFalse(Credencial.objects.filter(alumno=self.alumno).exists())

    def test_confirmar_regenerar_de_otro_gimnasio_da_404(self):
        services.generar_credencial_qr(self.alumno_ajeno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:credencial_qr", args=[self.alumno_ajeno.pk])
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_dar_de_baja_pide_confirmacion_y_el_get_no_cambia_nada(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:credencial_baja", args=[credencial.pk])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "ingreso/credencial_baja_confirmar.html")
        credencial.refresh_from_db()
        self.assertTrue(credencial.activa)

    def test_confirmar_la_baja_de_otro_gimnasio_da_404(self):
        ajena = services.generar_credencial_qr(self.alumno_ajeno)
        self._login()

        respuesta = self.client.get(reverse("ingreso:credencial_baja", args=[ajena.pk]))

        self.assertEqual(respuesta.status_code, 404)

    def test_la_confirmacion_de_regenerar_no_dibuja_el_qr(self):
        """La pantalla de confirmar no necesita la imagen: menos lugares que
        muestran la llave."""
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:credencial_qr", args=[self.alumno.pk])
        )

        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)


class ListadoDeAlumnosTests(_BaseVistas):
    def test_ofrece_generar_los_codigos_en_bloque(self):
        self._login()

        respuesta = self.client.get(reverse("alumnos:listado"))

        self.assertContains(
            respuesta, f'action="{reverse("ingreso:credenciales_en_bloque")}"'
        )


# ---------------------------------------------------------------------------
# Portal del alumno: «Tu código para entrar»
# ---------------------------------------------------------------------------


class _BasePortal(_BaseVistas):
    def setUp(self):
        super().setUp()
        self.alumno.perfil = Perfil.objects.get(usuario__username="alumno-a")
        self.alumno.save()

    def _mi_codigo(self, username="alumno-a"):
        self._login(username)
        return self.client.get(reverse("ingreso:mi_codigo"))

    def _deuda(self, *, puerta_rechaza):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5,
            fecha_activacion_bloqueo=timezone.localdate() - timedelta(days=365),
        )
        ConfiguracionIngreso.objects.create(
            gimnasio=self.gimnasio, bloquea_por_deuda=puerta_rechaza
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=20),
            estado=Cuota.Estado.VENCIDO,
        )


class MiCodigoTests(_BasePortal):
    def test_el_alumno_ve_su_qr_y_no_el_de_otro(self):
        propio = services.generar_credencial_qr(self.alumno)
        companiero = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Maria", apellido="G"
        )
        del_mismo_gimnasio = services.generar_credencial_qr(companiero)
        de_otro_gimnasio = services.generar_credencial_qr(self.alumno_ajeno)

        respuesta = self._mi_codigo()

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, qr.svg_de(propio.valor), html=False)
        self.assertNotContains(respuesta, qr.svg_de(del_mismo_gimnasio.valor), html=False)
        self.assertNotContains(respuesta, qr.svg_de(de_otro_gimnasio.valor), html=False)
        self.assertContains(respuesta, self.gimnasio.nombre)

    def test_el_otro_alumno_ve_el_suyo_y_no_este(self):
        propio = services.generar_credencial_qr(self.alumno)
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        otro.perfil = self._perfil_nuevo("alumno-maria")
        otro.save()
        suyo = services.generar_credencial_qr(otro)

        respuesta = self._mi_codigo("alumno-maria")

        self.assertContains(respuesta, qr.svg_de(suyo.valor), html=False)
        self.assertNotContains(respuesta, qr.svg_de(propio.valor), html=False)

    def _perfil_nuevo(self, username):
        self._usuario(username, self.gimnasio, Perfil.Rol.ALUMNO)
        return Perfil.objects.get(usuario__username=username)

    def test_no_se_guarda_en_ningun_cache(self):
        services.generar_credencial_qr(self.alumno)

        respuesta = self._mi_codigo()

        _no_se_guarda(self, respuesta)
        self.assertContains(respuesta, ATRIBUTO_HISTORIAL)

    def test_sin_codigo_muestra_un_estado_vacio(self):
        respuesta = self._mi_codigo()

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Todavía no tenés código")
        self.assertNotContains(respuesta, "Acercá la pantalla")

    def test_con_el_codigo_dado_de_baja_lo_dice_y_no_lo_dibuja(self):
        credencial = services.generar_credencial_qr(self.alumno)
        services.dar_de_baja_credencial(credencial)

        respuesta = self._mi_codigo()

        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)
        self.assertContains(respuesta, "dado de baja")

    def test_bloqueado_por_deuda_lo_ve_igual_con_el_aviso(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._deuda(puerta_rechaza=True)

        respuesta = self._mi_codigo()

        self.assertContains(respuesta, qr.svg_de(credencial.valor), html=False)
        self.assertContains(respuesta, "la puerta no te va a dejar pasar")

    def test_bloqueado_sin_rechazo_en_la_puerta_no_lleva_aviso(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._deuda(puerta_rechaza=False)

        respuesta = self._mi_codigo()

        self.assertContains(respuesta, qr.svg_de(credencial.valor), html=False)
        self.assertNotContains(respuesta, "la puerta no te va a dejar pasar")

    def test_no_le_crea_la_configuracion_al_gimnasio(self):
        services.generar_credencial_qr(self.alumno)

        self._mi_codigo()

        self.assertFalse(
            ConfiguracionIngreso.objects.filter(gimnasio=self.gimnasio).exists()
        )

    def test_perfil_sin_alumno_vinculado_ve_un_estado_vacio(self):
        self._usuario("suelto", self.gimnasio, Perfil.Rol.ALUMNO)

        respuesta = self._mi_codigo("suelto")

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Todavía no tenés código")

    def test_staff_recibe_403(self):
        self._login()
        self.assertEqual(
            self.client.get(reverse("ingreso:mi_codigo")).status_code, 403
        )

    def test_anonimo_es_redirigido_al_login(self):
        url = reverse("ingreso:mi_codigo")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_el_staff_suplantando_al_alumno_ve_su_codigo(self):
        """Decisión de producto: el staff ya lo ve (y lo regenera) desde la
        ficha, así que ocultarlo durante la suplantación no protege nada y le
        saca justo lo que vino a revisar («no me aparece el código»)."""
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))

        respuesta = self.client.get(reverse("ingreso:mi_codigo"))

        self.assertEqual(respuesta.wsgi_request.user.username, "alumno-a")
        self.assertContains(respuesta, qr.svg_de(credencial.valor), html=False)


class TarjetaDelPortalTests(_BasePortal):
    def _home(self):
        self._login("alumno-a")
        return self.client.get(reverse("home"))

    def test_sin_codigo_ni_lector_de_qr_no_hay_tarjeta(self):
        Dispositivo.objects.filter(gimnasio=self.gimnasio).update(
            tipo=Dispositivo.Tipo.FACIAL
        )

        respuesta = self._home()

        self.assertNotContains(respuesta, "Tu código para entrar")

    def test_sin_codigo_con_lector_de_qr_muestra_el_estado_vacio(self):
        respuesta = self._home()

        self.assertContains(respuesta, "Tu código para entrar")
        self.assertContains(respuesta, "pedíselo al gimnasio")

    def test_con_codigo_linkea_a_la_pantalla_y_no_lo_dibuja_en_el_inicio(self):
        credencial = services.generar_credencial_qr(self.alumno)

        respuesta = self._home()

        self.assertContains(respuesta, reverse("ingreso:mi_codigo"))
        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)

    def test_con_el_codigo_dado_de_baja_lo_dice(self):
        credencial = services.generar_credencial_qr(self.alumno)
        services.dar_de_baja_credencial(credencial)

        respuesta = self._home()

        self.assertContains(respuesta, "dado de baja")

    def test_bloqueado_por_deuda_el_inicio_tambien_avisa(self):
        services.generar_credencial_qr(self.alumno)
        self._deuda(puerta_rechaza=True)

        respuesta = self._home()

        self.assertContains(respuesta, reverse("ingreso:mi_codigo"))
        self.assertContains(respuesta, "la puerta no te va a dejar pasar")


class CostoDeLaTarjetaDelPortalTests(_BasePortal):
    """Corre en cada request del alumno: como mucho dos queries."""

    def _costo(self, *, bloqueo=None):
        with CaptureQueriesContext(connection) as capturadas:
            presentacion.tarjeta_del_portal(
                self.alumno, self.gimnasio, bloqueo=bloqueo
            )
        return len(capturadas)

    def test_sin_codigo(self):
        self.assertLessEqual(self._costo(), 2)

    def test_con_codigo(self):
        services.generar_credencial_qr(self.alumno)
        self.assertEqual(self._costo(), 1)

    def test_con_codigo_y_bloqueado(self):
        services.generar_credencial_qr(self.alumno)
        self.assertLessEqual(self._costo(bloqueo=object()), 2)


# ---------------------------------------------------------------------------
# Correcciones de la revisión de la Tarea 13
# ---------------------------------------------------------------------------


class QrComoDocumentoPropioTests(_BasePortal):
    """El QR nunca llega como swap de htmx adentro de otro documento: ese
    documento no lleva `no-store` y, al salir con «Salir», queda en el
    bfcache con la llave en el DOM."""

    def test_el_link_del_inicio_no_va_boosteado(self):
        services.generar_credencial_qr(self.alumno)
        self._login("alumno-a")

        respuesta = self.client.get(reverse("home"))

        self.assertContains(
            respuesta,
            f'hx-boost="false" href="{reverse("ingreso:mi_codigo")}"',
        )

    def test_el_link_del_listado_a_la_ficha_no_va_boosteado(self):
        self._login()

        respuesta = self.client.get(reverse("alumnos:listado"))

        self.assertContains(
            respuesta,
            f'hx-boost="false" href="{reverse("alumnos:detalle", args=[self.alumno.pk])}"',
        )

    def test_mi_codigo_pedido_por_htmx_fuerza_una_navegacion_completa(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._login("alumno-a")

        respuesta = self.client.get(
            reverse("ingreso:mi_codigo"), HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true"
        )

        self.assertEqual(respuesta["HX-Redirect"], reverse("ingreso:mi_codigo"))
        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)

    def test_la_ficha_con_qr_pedida_por_htmx_fuerza_una_navegacion_completa(self):
        """Cubre lo que no pasa por un link: el redirect a la ficha después de
        un POST boosteado («Generar código», el id facial, las bajas)."""
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()
        url = reverse("alumnos:detalle", args=[self.alumno.pk])

        respuesta = self.client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")

        self.assertEqual(respuesta["HX-Redirect"], url)
        self.assertNotContains(respuesta, qr.svg_de(credencial.valor), html=False)

    def test_la_ficha_sin_qr_sigue_boosteada(self):
        self._login()

        respuesta = self.client.get(
            reverse("alumnos:detalle", args=[self.alumno.pk]),
            HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true",
        )

        self.assertNotIn("HX-Redirect", respuesta)
        self.assertContains(respuesta, "Credencial de ingreso")


class AvisoDeDeudaSinDeudaTests(_BasePortal):
    """Con la puerta rechazando por deuda y el alumno AL DÍA, no hay aviso."""

    AVISO = "la puerta no te va a dejar pasar"

    def setUp(self):
        super().setUp()
        services.generar_credencial_qr(self.alumno)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5,
            fecha_activacion_bloqueo=timezone.localdate() - timedelta(days=365),
        )
        ConfiguracionIngreso.objects.create(gimnasio=self.gimnasio, bloquea_por_deuda=True)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=20),
            estado=Cuota.Estado.PAGADO,
        )

    def test_mi_codigo_no_avisa(self):
        respuesta = self._mi_codigo()

        self.assertContains(respuesta, "Acercá la pantalla")
        self.assertNotContains(respuesta, self.AVISO)

    def test_el_inicio_no_avisa(self):
        self._login("alumno-a")

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Mostrar mi código")
        self.assertNotContains(respuesta, self.AVISO)

    def test_la_tarjeta_sin_bloqueo_no_avisa(self):
        contexto = presentacion.tarjeta_del_portal(
            self.alumno, self.gimnasio, bloqueo=None
        )

        self.assertFalse(contexto["aviso_deuda_en_puerta"])


class LectorDesactivadoTests(_BasePortal):
    def test_con_solo_un_lector_de_qr_desactivado_no_hay_tarjeta(self):
        Dispositivo.objects.filter(gimnasio=self.gimnasio).update(activo=False)
        self._login("alumno-a")

        respuesta = self.client.get(reverse("home"))

        self.assertNotContains(respuesta, "Tu código para entrar")


class AccesibilidadDelQrTests(_BasePortal):
    def test_mi_codigo_anuncia_que_es_sin_decir_el_valor(self):
        credencial = services.generar_credencial_qr(self.alumno)

        respuesta = self._mi_codigo()

        self.assertContains(respuesta, 'role="img" aria-label="Tu código de ingreso"')
        self.assertEqual(respuesta.content.decode().count(credencial.valor), 0)

    def test_la_ficha_anuncia_que_es_sin_decir_el_valor(self):
        credencial = services.generar_credencial_qr(self.alumno)
        self._login()

        respuesta = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))

        self.assertContains(
            respuesta, 'role="img" aria-label="Código de ingreso de Juan"'
        )
        self.assertEqual(respuesta.content.decode().count(credencial.valor), 0)


class BajaRepetidaTests(_BaseVistas):
    def test_dar_de_baja_dos_veces_no_mueve_la_fecha(self):
        credencial = services.generar_credencial_qr(self.alumno)
        services.dar_de_baja_credencial(credencial)
        credencial.refresh_from_db()
        primera = credencial.modificado

        services.dar_de_baja_credencial(credencial)

        credencial.refresh_from_db()
        self.assertEqual(credencial.modificado, primera)
        self.assertFalse(credencial.activa)


class DobleSubmitDeGenerarTests(_BaseVistas):
    """Los dos requests ven que no hay fila; uno inserta y el otro choca
    contra la `UniqueConstraint`. Se simula el segundo: la fila ya existe y
    `get_or_create` levanta `IntegrityError` como en la carrera."""

    def _carrera(self):
        existente = Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.QR, valor=tokens.generar(),
        )
        return existente, patch.object(
            Credencial.objects.__class__, "get_or_create",
            side_effect=IntegrityError("credencial_una_por_alumno_y_tipo"),
        )

    def test_el_servicio_devuelve_la_existente(self):
        existente, carrera = self._carrera()
        with carrera:
            credencial = services.generar_credencial_qr(self.alumno)

        self.assertEqual(credencial.pk, existente.pk)
        self.assertEqual(credencial.valor, existente.valor)

    def test_la_vista_vuelve_a_la_ficha_sin_500(self):
        existente, carrera = self._carrera()
        self._login()
        with carrera:
            respuesta = self.client.post(
                reverse("ingreso:credencial_qr", args=[self.alumno.pk])
            )

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(Credencial.objects.filter(alumno=self.alumno).count(), 1)
