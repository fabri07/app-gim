"""Tests de las pantallas de staff del control de ingreso.

Cada pantalla lleva los cuatro tests de guardia del molde
`ejercicios/tests.py::CategoriaCRUDTests` (anónimo al login, alumno 403, staff
ve lo suyo y no lo del otro gimnasio, 404 sobre un registro ajeno). Además se
fija lo que es propio de esta feature: el token del lector se muestra UNA vez
y nunca se guarda en claro, rotarlo le devuelve la puerta al dueño aunque un
atacante haya agotado el tope diario, y las pantallas SÍ quedan cortadas con
la cuenta congelada (la API no: `tests_api.py`).
"""

from datetime import datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import limites, services, tokens
from ingreso.forms import CredencialFacialForm
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from tenants.models import Gimnasio, Perfil

CLAVE = "clave-123456"


class _BaseVistas(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.staff = self._usuario("staff-a", self.gimnasio, Perfil.Rol.STAFF)
        self._usuario("alumno-a", self.gimnasio, Perfil.Rol.ALUMNO)
        self._usuario("staff-b", self.otro, Perfil.Rol.STAFF)
        self.puerta = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta principal",
            token_hash=tokens.hash_de(tokens.generar()),
        )
        self.puerta_ajena = Dispositivo.objects.create(
            gimnasio=self.otro, nombre="Puerta ajena",
            token_hash=tokens.hash_de(tokens.generar()),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.alumno_ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ajeno", apellido="Otro"
        )

    @staticmethod
    def _usuario(username, gimnasio, rol):
        usuario = User.objects.create_user(username, password=CLAVE)
        Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=rol)
        return usuario

    def _login(self, username="staff-a"):
        self.client.login(username=username, password=CLAVE)

    def _evento(self, gimnasio=None, dispositivo=None, alumno=None, **extra):
        datos = {
            "gimnasio": gimnasio or self.gimnasio,
            "dispositivo": dispositivo or self.puerta,
            "alumno": alumno,
            "resultado": EventoIngreso.Resultado.PERMITIDO,
            "momento": timezone.now(),
        }
        datos.update(extra)
        return EventoIngreso.objects.create(**datos)


class _GuardiasMixin:
    """Los tres primeros tests de guardia, para cualquier pantalla GET."""

    def _url_guardia(self):
        raise NotImplementedError

    def test_anonimo_es_redirigido_al_login(self):
        url = self._url_guardia()
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        self.assertEqual(self.client.get(self._url_guardia()).status_code, 403)


# ---------------------------------------------------------------------------
# ingreso:listado
# ---------------------------------------------------------------------------


class ListadoDeIngresosTests(_GuardiasMixin, _BaseVistas):
    def _url_guardia(self):
        return reverse("ingreso:listado")

    def test_staff_ve_sus_ingresos_y_no_los_de_otro_gimnasio(self):
        self._evento(alumno=self.alumno)
        self._evento(
            gimnasio=self.otro, dispositivo=self.puerta_ajena,
            alumno=self.alumno_ajeno,
        )
        self._login()

        respuesta = self.client.get(reverse("ingreso:listado"))

        self.assertContains(respuesta, "Perez")
        self.assertNotContains(respuesta, "Ajeno")
        self.assertNotContains(respuesta, "Puerta ajena")

    def test_filtra_por_alumno_de_otro_gimnasio_no_filtra_nada_ajeno(self):
        """Un `?alumno=` con el pk de un alumno ajeno no le muestra sus
        ingresos: el filtro corre sobre el queryset ya acotado."""
        self._evento(
            gimnasio=self.otro, dispositivo=self.puerta_ajena,
            alumno=self.alumno_ajeno,
        )
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:listado"), {"alumno": self.alumno_ajeno.pk}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, "Ajeno")

    def test_filtra_por_alumno(self):
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Maria", apellido="Gomez"
        )
        self._evento(alumno=self.alumno)
        self._evento(alumno=otro_alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:listado"), {"alumno": self.alumno.pk}
        )

        self.assertEqual(list(respuesta.context["eventos"]), list(
            EventoIngreso.objects.filter(alumno=self.alumno)
        ))

    def test_filtra_por_resultado_y_por_rechazados(self):
        pasado = self._evento(alumno=self.alumno)
        deuda = self._evento(
            alumno=self.alumno, resultado=EventoIngreso.Resultado.DEUDA
        )
        self._login()

        solo_deuda = self.client.get(
            reverse("ingreso:listado"), {"resultado": "deuda"}
        )
        rechazados = self.client.get(
            reverse("ingreso:listado"), {"resultado": "rechazados"}
        )

        self.assertEqual(list(solo_deuda.context["eventos"]), [deuda])
        self.assertEqual(list(rechazados.context["eventos"]), [deuda])
        self.assertNotIn(pasado, list(rechazados.context["eventos"]))

    def test_la_doble_lectura_no_sale_en_solo_rechazos(self):
        """`DUPLICADO` no es un rechazo (`models.RESULTADOS_RECHAZO`): se ve
        en el listado completo y filtrando por su resultado, no en
        «Solo rechazos»."""
        duplicado = self._evento(
            alumno=self.alumno, resultado=EventoIngreso.Resultado.DUPLICADO
        )
        self._login()

        rechazados = self.client.get(
            reverse("ingreso:listado"), {"resultado": "rechazados"}
        )
        solo_duplicados = self.client.get(
            reverse("ingreso:listado"), {"resultado": "duplicado"}
        )

        self.assertEqual(list(rechazados.context["eventos"]), [])
        self.assertEqual(list(solo_duplicados.context["eventos"]), [duplicado])

    def test_filtra_por_fecha_local_y_no_por_utc(self):
        """Un ingreso a las 22:30 de Buenos Aires ya es el día siguiente en
        UTC. Filtrar «hasta hoy» tiene que incluirlo, y la hora mostrada tiene
        que ser la local."""
        dia = datetime(2031, 5, 10, 22, 30)
        momento = timezone.make_aware(dia)
        self._evento(alumno=self.alumno, momento=momento)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:listado"),
            {"desde": "2031-05-10", "hasta": "2031-05-10"},
        )
        al_dia_siguiente = self.client.get(
            reverse("ingreso:listado"), {"desde": "2031-05-11"}
        )

        self.assertEqual(len(respuesta.context["eventos"]), 1)
        self.assertContains(respuesta, "22:30")
        self.assertNotContains(respuesta, "01:30")
        self.assertEqual(len(al_dia_siguiente.context["eventos"]), 0)

    def test_filtros_invalidos_se_ignoran_en_vez_de_dar_500(self):
        self._evento(alumno=self.alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:listado"),
            {"alumno": "x", "resultado": "rfid", "desde": "31/02", "hasta": "nada"},
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.context["eventos"]), 1)

    def test_fechas_en_los_extremos_del_calendario_no_dan_500(self):
        """`hasta=9999-12-31` no tiene día siguiente: `+ timedelta(days=1)`
        levantaba `OverflowError`. Queda sin tope."""
        self._evento(alumno=self.alumno)
        self._login()

        for filtros, cantidad in (
            ({"hasta": "9999-12-31"}, 1),
            ({"desde": "9999-12-31"}, 0),
            ({"desde": "0001-01-01", "hasta": "9999-12-31"}, 1),
            ({"hasta": "0001-01-01"}, 0),
        ):
            with self.subTest(filtros):
                respuesta = self.client.get(reverse("ingreso:listado"), filtros)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(len(respuesta.context["eventos"]), cantidad)

    def test_pagina_y_conserva_los_filtros_en_los_links(self):
        for _ in range(55):
            self._evento(alumno=self.alumno)
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:listado"), {"resultado": "permitido"}
        )

        self.assertEqual(len(respuesta.context["eventos"]), 50)
        self.assertTrue(respuesta.context["is_paginated"])
        self.assertContains(respuesta, "resultado=permitido&amp;page=2")

    def _queries_con(self, cantidad):
        EventoIngreso.objects.all().delete()
        for i in range(cantidad):
            alumno = Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"N{i}", apellido=f"A{i}"
            )
            credencial = services.generar_credencial_qr(alumno)
            self._evento(alumno=alumno, credencial=credencial)
        self._login()
        # Un request de calentamiento: el primero del día escribe la sesión y
        # la actividad diaria (`PlataformaMiddleware`), que no son del listado.
        self.client.get(reverse("ingreso:listado"))
        with CaptureQueriesContext(connection) as capturadas:
            respuesta = self.client.get(reverse("ingreso:listado"))
        self.assertEqual(len(respuesta.context["eventos"]), cantidad)
        self.assertContains(respuesta, f"A{cantidad - 1}")
        return len(capturadas)

    def test_el_costo_no_crece_con_la_cantidad_de_ingresos(self):
        """El N+1 que vive en un listado: sin `select_related`, cada fila es
        una query por alumno y otra por lector."""
        chico = self._queries_con(3)
        grande = self._queries_con(30)

        self.assertEqual(chico, grande)


# ---------------------------------------------------------------------------
# ingreso:dispositivos, dispositivo_crear, dispositivo_editar, rotar
# ---------------------------------------------------------------------------


class DispositivosTests(_GuardiasMixin, _BaseVistas):
    def _url_guardia(self):
        return reverse("ingreso:dispositivos")

    def test_staff_ve_sus_lectores_y_no_los_de_otro_gimnasio(self):
        self._login()

        respuesta = self.client.get(reverse("ingreso:dispositivos"))

        self.assertContains(respuesta, "Puerta principal")
        self.assertNotContains(respuesta, "Puerta ajena")

    def test_staff_de_otro_gimnasio_recibe_404_al_editar(self):
        self._login("staff-b")

        respuesta = self.client.get(
            reverse("ingreso:dispositivo_editar", args=[self.puerta.pk])
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_alumno_recibe_403_al_crear(self):
        self._login("alumno-a")
        respuesta = self.client.get(reverse("ingreso:dispositivo_crear"))
        self.assertEqual(respuesta.status_code, 403)

    def test_alta_muestra_el_token_una_vez_en_un_200_sin_redirect(self):
        self._login()

        with patch("ingreso.services.tokens.generar", return_value="TOKEN-SECRETO-1"):
            respuesta = self.client.post(
                reverse("ingreso:dispositivo_crear"),
                {"nombre": "Molinete", "tipo": Dispositivo.Tipo.QR},
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "TOKEN-SECRETO-1")
        self.assertIn("no-store", respuesta["Cache-Control"])
        nuevo = Dispositivo.objects.get(gimnasio=self.gimnasio, nombre="Molinete")
        self.assertEqual(nuevo.token_hash, tokens.hash_de("TOKEN-SECRETO-1"))
        self.assertTrue(nuevo.activo)

    def test_el_token_no_viaja_por_messages_ni_queda_en_claro(self):
        """`messages` se serializa en la sesión, que vive en la base: el token
        quedaría escrito en una tabla. Y en `Dispositivo` solo va el hash."""
        self._login()

        with patch("ingreso.services.tokens.generar", return_value="TOKEN-SECRETO-2"):
            respuesta = self.client.post(
                reverse("ingreso:dispositivo_crear"),
                {"nombre": "Molinete", "tipo": Dispositivo.Tipo.QR},
            )

        for mensaje in get_messages(respuesta.wsgi_request):
            self.assertNotIn("TOKEN-SECRETO-2", str(mensaje))
        self.assertNotIn("TOKEN-SECRETO-2", str(respuesta.cookies))
        for sesion in Session.objects.all():
            self.assertNotIn("TOKEN-SECRETO-2", str(sesion.get_decoded()))
        self.assertFalse(
            Dispositivo.objects.filter(token_hash="TOKEN-SECRETO-2").exists()
        )

    def test_el_token_no_vuelve_a_aparecer_al_editar(self):
        self._login()
        with patch("ingreso.services.tokens.generar", return_value="TOKEN-SECRETO-3"):
            self.client.post(
                reverse("ingreso:dispositivo_crear"),
                {"nombre": "Molinete", "tipo": Dispositivo.Tipo.QR},
            )
        nuevo = Dispositivo.objects.get(nombre="Molinete")
        url = reverse("ingreso:dispositivo_editar", args=[nuevo.pk])

        get = self.client.get(url)
        post = self.client.post(
            url, {"nombre": "Molinete 2", "tipo": Dispositivo.Tipo.QR, "activo": "on"},
            follow=True,
        )

        self.assertNotContains(get, "TOKEN-SECRETO-3")
        self.assertNotContains(post, "TOKEN-SECRETO-3")

    def test_nombre_repetido_es_un_error_de_campo_y_no_un_500(self):
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:dispositivo_crear"),
            {"nombre": " puerta principal ", "tipo": Dispositivo.Tipo.QR},
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFormError(
            respuesta.context["form"], "nombre",
            "Ya tenés un lector con ese nombre.",
        )
        self.assertEqual(Dispositivo.objects.for_gimnasio(self.gimnasio).count(), 1)

    def test_nombre_repetido_que_se_escapa_del_form_tampoco_da_500(self):
        """La carrera del doble submit: el `clean` no lo ve, la base sí."""
        self._login()

        with patch("ingreso.forms.DispositivoForm.clean_nombre", lambda f: "PUERTA principal"):
            respuesta = self.client.post(
                reverse("ingreso:dispositivo_crear"),
                {"nombre": "PUERTA principal", "tipo": Dispositivo.Tipo.QR},
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFormError(
            respuesta.context["form"], "nombre",
            "Ya tenés un lector con ese nombre.",
        )

    def test_la_base_tampoco_distingue_mayusculas(self):
        """La misma regla que el form, en la base: si el `clean_nombre` no
        corre (otro camino de alta, la carrera), «PUERTA PRINCIPAL» choca
        igual contra «Puerta principal»."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            Dispositivo.objects.create(
                gimnasio=self.gimnasio, nombre="PUERTA PRINCIPAL",
                token_hash=tokens.hash_de(tokens.generar()),
            )
        # En otro gimnasio, el mismo nombre sí.
        Dispositivo.objects.create(
            gimnasio=self.otro, nombre="PUERTA PRINCIPAL",
            token_hash=tokens.hash_de(tokens.generar()),
        )

    def test_el_mismo_nombre_en_otro_gimnasio_se_puede(self):
        self._login()

        self.client.post(
            reverse("ingreso:dispositivo_crear"),
            {"nombre": "Puerta ajena", "tipo": Dispositivo.Tipo.QR},
        )

        self.assertTrue(
            Dispositivo.objects.for_gimnasio(self.gimnasio)
            .filter(nombre="Puerta ajena").exists()
        )

    def test_crear_un_lector_invalida_el_set_de_activos(self):
        """Sin esto, el lector nuevo no figura en el set durante un minuto y
        recibe 429 si la IP del gimnasio está frenada."""
        limites.hashes_activos()  # llena el cache
        self._login()

        with patch("ingreso.services.tokens.generar", return_value="TOKEN-NUEVO"):
            self.client.post(
                reverse("ingreso:dispositivo_crear"),
                {"nombre": "Molinete", "tipo": Dispositivo.Tipo.QR},
            )

        self.assertIn(tokens.hash_de("TOKEN-NUEVO"), limites.hashes_activos())

    def test_desactivar_un_lector_invalida_el_set_de_activos(self):
        self.assertIn(self.puerta.token_hash, limites.hashes_activos())
        self._login()

        self.client.post(
            reverse("ingreso:dispositivo_editar", args=[self.puerta.pk]),
            {"nombre": "Puerta principal", "tipo": Dispositivo.Tipo.QR},
        )

        self.puerta.refresh_from_db()
        self.assertFalse(self.puerta.activo)
        self.assertNotIn(self.puerta.token_hash, limites.hashes_activos())

    def test_reactivar_un_lector_invalida_el_set_de_activos(self):
        Dispositivo.objects.filter(pk=self.puerta.pk).update(activo=False)
        self.assertNotIn(self.puerta.token_hash, limites.hashes_activos())
        self._login()

        self.client.post(
            reverse("ingreso:dispositivo_editar", args=[self.puerta.pk]),
            {"nombre": "Puerta principal", "tipo": Dispositivo.Tipo.QR, "activo": "on"},
        )

        self.assertIn(self.puerta.token_hash, limites.hashes_activos())

    def test_editar_con_nombre_de_otro_lector_propio_es_error_de_campo(self):
        Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Salida",
            token_hash=tokens.hash_de(tokens.generar()),
        )
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:dispositivo_editar", args=[self.puerta.pk]),
            {"nombre": "Salida", "tipo": Dispositivo.Tipo.QR, "activo": "on"},
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFormError(
            respuesta.context["form"], "nombre", "Ya tenés un lector con ese nombre."
        )

    def test_no_hay_vista_para_borrar_un_lector(self):
        """Se desactiva: el `PROTECT` de los eventos lo respalda y borrarlo se
        llevaría el historial de asistencia."""
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("ingreso:dispositivo_eliminar", args=[self.puerta.pk])


class RotarTokenTests(_BaseVistas):
    def _rotar(self, dispositivo=None, **patch_token):
        dispositivo = dispositivo or self.puerta
        with patch("ingreso.services.tokens.generar", return_value="TOKEN-ROTADO"):
            return self.client.post(
                reverse("ingreso:dispositivo_rotar", args=[dispositivo.pk])
            )

    def test_muestra_el_token_nuevo_una_vez_y_guarda_solo_el_hash(self):
        self._login()

        respuesta = self._rotar()

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "TOKEN-ROTADO")
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.puerta.refresh_from_db()
        self.assertEqual(self.puerta.token_hash, tokens.hash_de("TOKEN-ROTADO"))
        for mensaje in get_messages(respuesta.wsgi_request):
            self.assertNotIn("TOKEN-ROTADO", str(mensaje))

    def test_rotar_pone_en_cero_el_tope_diario(self):
        """Un atacante que agotó el tope con el token robado dejaría la puerta
        cerrada todo el día aunque el dueño rote el token."""
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            eventos_hoy=limites.MAX_EVENTOS_POR_DIA,
            eventos_hoy_fecha=timezone.localdate(),
        )
        self._login()

        self._rotar()

        self.puerta.refresh_from_db()
        self.assertEqual(self.puerta.eventos_hoy, 0)
        self.assertFalse(limites.cupo_diario_excedido(self.puerta))

    def test_rotar_invalida_el_set_de_activos(self):
        viejo = self.puerta.token_hash
        self.assertIn(viejo, limites.hashes_activos())
        self._login()

        self._rotar()

        activos = limites.hashes_activos()
        self.assertNotIn(viejo, activos)
        self.assertIn(tokens.hash_de("TOKEN-ROTADO"), activos)

    def test_el_token_viejo_deja_de_abrir_la_puerta(self):
        token_viejo = tokens.generar()
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            token_hash=tokens.hash_de(token_viejo)
        )
        self._login()
        self._rotar()
        self.client.logout()

        respuesta = self.client.get(
            reverse("ingreso:api_padron"), HTTP_AUTHORIZATION=f"Bearer {token_viejo}"
        )

        self.assertEqual(respuesta.status_code, 401)

    def test_staff_de_otro_gimnasio_recibe_404_y_no_rota_nada(self):
        viejo = self.puerta.token_hash
        self._login("staff-b")

        respuesta = self._rotar()

        self.assertEqual(respuesta.status_code, 404)
        self.puerta.refresh_from_db()
        self.assertEqual(self.puerta.token_hash, viejo)

    def test_get_muestra_la_confirmacion_y_no_rota(self):
        viejo = self.puerta.token_hash
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "ingreso/dispositivo_rotar_confirmar.html")
        self.assertContains(respuesta, "deja de funcionar en el acto")
        self.puerta.refresh_from_db()
        self.assertEqual(self.puerta.token_hash, viejo)

    def test_get_de_otro_gimnasio_da_404(self):
        self._login("staff-b")
        respuesta = self.client.get(
            reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_la_ficha_del_lector_linkea_a_la_confirmacion_sin_rotar(self):
        """El botón de la ficha es un link (GET) a la confirmación, no un
        form que postea directo."""
        self._login()

        respuesta = self.client.get(
            reverse("ingreso:dispositivo_editar", args=[self.puerta.pk])
        )

        url = reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        self.assertContains(respuesta, f'href="{url}"')
        self.assertNotContains(respuesta, f'action="{url}"')

    def test_con_el_lector_desactivado_la_confirmacion_y_la_clave_lo_aclaran(self):
        Dispositivo.objects.filter(pk=self.puerta.pk).update(activo=False)
        self._login()

        confirmacion = self.client.get(
            reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        )
        clave = self._rotar()

        self.assertContains(confirmacion, "Este lector está desactivado")
        self.assertContains(clave, "Este lector está desactivado")

    def test_con_el_lector_activo_no_dice_que_esta_desactivado(self):
        self._login()
        self.assertNotContains(self._rotar(), "Este lector está desactivado")

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        self.assertEqual(self._rotar().status_code, 403)


# ---------------------------------------------------------------------------
# ingreso:configuracion
# ---------------------------------------------------------------------------


class ConfiguracionTests(_GuardiasMixin, _BaseVistas):
    def _url_guardia(self):
        return reverse("ingreso:configuracion")

    def test_staff_edita_la_de_su_gimnasio_y_no_toca_la_del_otro(self):
        ajena = obtener_configuracion(self.otro)
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:configuracion"),
            {"bloquea_por_deuda": "on", "minutos_antipassback": "5"},
        )

        self.assertRedirects(respuesta, reverse("ingreso:configuracion"))
        propia = ConfiguracionIngreso.objects.get(gimnasio=self.gimnasio)
        self.assertTrue(propia.bloquea_por_deuda)
        self.assertEqual(propia.minutos_antipassback, 5)
        ajena.refresh_from_db()
        self.assertFalse(ajena.bloquea_por_deuda)
        self.assertEqual(ajena.minutos_antipassback, 2)

    def test_avisa_que_el_bloqueo_no_hace_nada_sin_tolerancia(self):
        """`pagos/acceso.py::_umbral` hace fail-open: el interruptor queda
        muerto y sin síntoma si no hay días de tolerancia cargados."""
        ConfiguracionIngreso.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"bloquea_por_deuda": True}
        )
        self._login()

        respuesta = self.client.get(reverse("ingreso:configuracion"))

        self.assertContains(
            respuesta,
            "Esto no va a bloquear a nadie hasta que cargues los días de "
            "tolerancia en Mi gimnasio",
        )

    def test_con_tolerancia_cargada_no_avisa(self):
        self.gimnasio.dias_tolerancia_pago = 10
        self.gimnasio.save()  # la señal estampa fecha_activacion_bloqueo
        ConfiguracionIngreso.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"bloquea_por_deuda": True}
        )
        self._login()

        respuesta = self.client.get(reverse("ingreso:configuracion"))

        self.assertNotContains(respuesta, "Esto no va a bloquear a nadie")

    def test_con_el_bloqueo_apagado_no_avisa(self):
        self._login()

        respuesta = self.client.get(reverse("ingreso:configuracion"))

        self.assertNotContains(respuesta, "Esto no va a bloquear a nadie")

    def test_dice_que_reglas_no_se_aplican_sin_internet_al_lado_de_los_checkboxes(self):
        self._login()

        respuesta = self.client.get(reverse("ingreso:configuracion"))
        html = respuesta.content.decode()

        aviso = "Con el lector sin internet"
        self.assertIn(aviso, html)
        # Al lado de los campos, no en una nota al pie después del botón.
        self.assertLess(html.index('name="exige_reserva"'), html.index(aviso))
        self.assertLess(html.index(aviso), html.index('class="boton">Guardar'))

    def test_minutos_negativos_muestran_su_error(self):
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:configuracion"), {"minutos_antipassback": "-1"}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["minutos_antipassback"])
        self.assertContains(respuesta, 'class="config-error"')


# ---------------------------------------------------------------------------
# La otra mitad de la invariante de la Tarea 9
# ---------------------------------------------------------------------------


class PantallasConLaCuentaSuspendidaTests(_BaseVistas):
    """Con la cuenta congelada el panel de ingreso SÍ se corta, como el resto
    del panel. La puerta no (`tests_api.py::PuertaConLaCuentaSuspendidaTests`):
    las dos juntas son la decisión, y es lo que prueba que `ingreso` no está en
    `APPS_SIN_BLOQUEO`."""

    def setUp(self):
        super().setUp()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )

    def test_el_listado_muestra_el_cartel(self):
        self._login()

        respuesta = self.client.get(reverse("ingreso:listado"))

        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertTemplateNotUsed(respuesta, "ingreso/evento_list.html")

    def test_la_configuracion_y_los_lectores_tambien(self):
        self._login()

        for nombre in ("ingreso:configuracion", "ingreso:dispositivos"):
            with self.subTest(nombre):
                respuesta = self.client.get(reverse(nombre))
                self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_la_api_sigue_abriendo_la_puerta_en_el_mismo_gimnasio(self):
        token = tokens.generar()
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            token_hash=tokens.hash_de(token)
        )

        respuesta = self.client.get(
            reverse("ingreso:api_padron"), HTTP_AUTHORIZATION=f"Bearer {token}"
        )

        self.assertEqual(respuesta.status_code, 200)


# ---------------------------------------------------------------------------
# Credenciales: QR por alumno, en bloque, id facial y baja
# ---------------------------------------------------------------------------


class CredencialQrTests(_BaseVistas):
    def _url(self, alumno=None):
        return reverse("ingreso:credencial_qr", args=[(alumno or self.alumno).pk])

    def test_genera_el_qr_y_vuelve_a_la_ficha(self):
        self._login()

        respuesta = self.client.post(self._url())

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            Credencial.objects.filter(alumno=self.alumno, tipo="qr").exists()
        )

    def test_sin_regenerar_no_cambia_el_valor(self):
        qr = services.generar_credencial_qr(self.alumno)
        self._login()

        self.client.post(self._url())

        qr.refresh_from_db()
        self.assertEqual(
            Credencial.objects.get(alumno=self.alumno, tipo="qr").valor, qr.valor
        )

    def test_regenerar_cambia_el_valor_y_reactiva(self):
        qr = services.generar_credencial_qr(self.alumno)
        Credencial.objects.filter(pk=qr.pk).update(activa=False)
        viejo = qr.valor
        self._login()

        self.client.post(self._url(), {"regenerar": "1"})

        qr.refresh_from_db()
        self.assertNotEqual(qr.valor, viejo)
        self.assertTrue(qr.activa)

    def test_generar_sobre_un_qr_dado_de_baja_no_lo_reactiva(self):
        """La baja pudo ser por una tarjeta robada: reactivar el mismo valor le
        devolvería la puerta a quien la tiene. Se avisa y se ofrece regenerar."""
        qr = services.generar_credencial_qr(self.alumno)
        services.dar_de_baja_credencial(qr)
        valor = qr.valor
        self._login()

        respuesta = self.client.post(self._url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "ingreso/credencial_qr_dada_de_baja.html")
        self.assertContains(respuesta, 'name="regenerar" value="1"')
        self.assertContains(respuesta, f'action="{self._url()}"')
        qr.refresh_from_db()
        self.assertEqual((qr.valor, qr.activa), (valor, False))
        self.assertEqual(list(get_messages(respuesta.wsgi_request)), [])

    def test_el_valor_no_viaja_por_messages(self):
        self._login()

        respuesta = self.client.post(self._url())

        valor = Credencial.objects.get(alumno=self.alumno, tipo="qr").valor
        for mensaje in get_messages(respuesta.wsgi_request):
            self.assertNotIn(valor, str(mensaje))

    def test_alumno_de_otro_gimnasio_da_404_y_no_crea_nada(self):
        self._login()

        respuesta = self.client.post(self._url(self.alumno_ajeno))

        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(Credencial.objects.filter(alumno=self.alumno_ajeno).exists())

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        self.assertEqual(self.client.post(self._url()).status_code, 403)

    def test_get_no_genera_nada(self):
        """Desde la Tarea 13 el GET es la confirmación de regenerar
        (`tests_ficha_y_portal.ConfirmacionesTests`): nunca escribe."""
        self._login()
        self.client.get(self._url())
        self.assertFalse(Credencial.objects.filter(alumno=self.alumno).exists())


class CredencialesEnBloqueTests(_BaseVistas):
    def test_genera_para_los_activos_de_su_gimnasio_y_nada_del_otro(self):
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Baja", apellido="B",
            estado=Alumno.Estado.INACTIVO,
        )
        self._login()

        respuesta = self.client.post(reverse("ingreso:credenciales_en_bloque"))

        self.assertRedirects(
            respuesta, reverse("alumnos:listado"), fetch_redirect_response=False
        )
        self.assertEqual(
            Credencial.objects.for_gimnasio(self.gimnasio).filter(tipo="qr").count(), 2
        )
        self.assertFalse(Credencial.objects.for_gimnasio(self.otro).exists())
        mensajes = [str(m) for m in get_messages(respuesta.wsgi_request)]
        self.assertTrue(any("2" in m for m in mensajes), mensajes)

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        respuesta = self.client.post(reverse("ingreso:credenciales_en_bloque"))
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Credencial.objects.exists())

    def test_get_da_405(self):
        self._login()
        respuesta = self.client.get(reverse("ingreso:credenciales_en_bloque"))
        self.assertEqual(respuesta.status_code, 405)


class CredencialFacialTests(_BaseVistas):
    def _url(self, alumno=None):
        return reverse("ingreso:credencial_facial", args=[(alumno or self.alumno).pk])

    def test_anonimo_es_redirigido_al_login(self):
        url = self._url()
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        self.assertEqual(self.client.get(self._url()).status_code, 403)

    def test_alumno_de_otro_gimnasio_da_404(self):
        self._login()
        self.assertEqual(self.client.get(self._url(self.alumno_ajeno)).status_code, 404)
        self.assertEqual(
            self.client.post(self._url(self.alumno_ajeno), {"valor": "42"}).status_code,
            404,
        )
        self.assertFalse(Credencial.objects.filter(alumno=self.alumno_ajeno).exists())

    def test_alta_normaliza_con_strip_y_vuelve_a_la_ficha(self):
        self._login()

        respuesta = self.client.post(self._url(), {"valor": "  42\r\n"})

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        credencial = Credencial.objects.get(alumno=self.alumno, tipo="facial")
        self.assertEqual(credencial.valor, "42")
        self.assertEqual(credencial.gimnasio, self.gimnasio)

    def test_editar_reemplaza_el_id_y_reactiva(self):
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, tipo="facial",
            valor="41", activa=False,
        )
        self._login()

        get = self.client.get(self._url())
        self.client.post(self._url(), {"valor": "42"})

        self.assertContains(get, 'value="41"')
        credencial = Credencial.objects.get(alumno=self.alumno, tipo="facial")
        self.assertEqual((credencial.valor, credencial.activa), ("42", True))

    def test_id_de_otro_alumno_es_error_de_campo_y_no_500(self):
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=otro, tipo="facial", valor="42"
        )
        self._login()

        respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Maria", respuesta.context["form"].errors["valor"][0])
        self.assertContains(respuesta, 'class="config-error"')
        self.assertFalse(Credencial.objects.filter(alumno=self.alumno).exists())

    def test_el_id_de_un_ex_alumno_dado_de_baja_se_puede_reasignar(self):
        """El equipo reusa el número de un usuario borrado: dar de baja la
        credencial lo libera."""
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=otro, tipo="facial", valor="42",
            activa=False,
        )
        self._login()

        respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        credencial = Credencial.objects.get(alumno=self.alumno, tipo="facial")
        self.assertEqual((credencial.valor, credencial.activa), ("42", True))

    def test_recargar_el_id_que_tuvo_cuando_ahora_es_de_otro_es_error_de_campo(self):
        """Juan tuvo el 42, se le dio de baja y el equipo se lo pasó a Maria.
        Volver a cargarle el 42 a Juan (lo que reactivaría su fila) choca con
        la de Maria: error de campo, y la de Juan sigue dada de baja."""
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, tipo="facial",
            valor="42", activa=False,
        )
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=otro, tipo="facial", valor="42"
        )
        self._login()

        respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Maria", respuesta.context["form"].errors["valor"][0])
        credencial = Credencial.objects.get(alumno=self.alumno, tipo="facial")
        self.assertFalse(credencial.activa)

    def test_reactivar_contra_otro_activo_que_se_escapa_del_form_no_da_500(self):
        """La carrera del caso anterior: el `clean` no la vio y la base
        rechaza la reactivación (la unicidad parcial). Error de campo."""
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, tipo="facial",
            valor="42", activa=False,
        )
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=otro, tipo="facial", valor="42"
        )
        self._login()

        with patch.object(CredencialFacialForm, "clean_valor", lambda f: "42"):
            respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Maria", respuesta.context["form"].errors["valor"][0])
        self.assertFalse(
            Credencial.objects.get(alumno=self.alumno, tipo="facial").activa
        )

    def test_choque_que_se_escapa_del_form_tampoco_da_500(self):
        """La carrera: el `clean` no lo vio y la base lo rechaza. Se traduce
        al mismo error de campo."""
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Maria", apellido="G")
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=otro, tipo="facial", valor="42"
        )
        self._login()

        with patch.object(CredencialFacialForm, "clean_valor", lambda f: "42"):
            respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["valor"])

    def test_doble_submit_del_primer_alta_no_da_un_error_falso(self):
        """El `IntegrityError` puede ser la unicidad (gimnasio, alumno, tipo):
        el otro request del doble submit ya guardó. No hay otro alumno con ese
        número, así que no se inventa «ya lo tiene otro»: se vuelve a la
        ficha."""
        self._login()

        def ya_lo_guardo_el_otro_request(alumno, valor):
            Credencial.objects.create(
                gimnasio=alumno.gimnasio, alumno=alumno, tipo="facial", valor=valor
            )
            raise IntegrityError("credencial_una_por_alumno_y_tipo")

        with patch(
            "ingreso.views.services.guardar_credencial_facial",
            ya_lo_guardo_el_otro_request,
        ):
            respuesta = self.client.post(self._url(), {"valor": "42"})

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )

    def test_el_mismo_id_en_otro_gimnasio_se_puede(self):
        """Cada lector numera a su gente: el 42 de un gimnasio no es el del
        otro, y el error no puede revelar que existe."""
        Credencial.objects.create(
            gimnasio=self.otro, alumno=self.alumno_ajeno, tipo="facial", valor="42"
        )
        self._login()

        self.client.post(self._url(), {"valor": "42"})

        self.assertTrue(
            Credencial.objects.filter(alumno=self.alumno, valor="42").exists()
        )

    def test_vacio_muestra_su_error(self):
        self._login()

        respuesta = self.client.post(self._url(), {"valor": "   "})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["valor"])


class CredencialBajaTests(_BaseVistas):
    def test_da_de_baja_sin_borrar(self):
        qr = services.generar_credencial_qr(self.alumno)
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:credencial_baja", args=[qr.pk])
        )

        self.assertRedirects(
            respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]),
            fetch_redirect_response=False,
        )
        qr.refresh_from_db()
        self.assertFalse(qr.activa)

    def test_credencial_de_otro_gimnasio_da_404(self):
        ajena = services.generar_credencial_qr(self.alumno_ajeno)
        self._login()

        respuesta = self.client.post(
            reverse("ingreso:credencial_baja", args=[ajena.pk])
        )

        self.assertEqual(respuesta.status_code, 404)
        ajena.refresh_from_db()
        self.assertTrue(ajena.activa)

    def test_alumno_recibe_403(self):
        qr = services.generar_credencial_qr(self.alumno)
        self._login("alumno-a")
        respuesta = self.client.post(reverse("ingreso:credencial_baja", args=[qr.pk]))
        self.assertEqual(respuesta.status_code, 403)


# ---------------------------------------------------------------------------
# Entrada a la sección sin sumar ítems al nav
# ---------------------------------------------------------------------------


class EntradaALaSeccionTests(_BaseVistas):
    def test_mi_gimnasio_linkea_la_configuracion(self):
        self._login()

        respuesta = self.client.get(reverse("gimnasio_editar"))

        self.assertContains(respuesta, reverse("ingreso:configuracion"))

    def test_el_nav_no_linkea_ingreso(self):
        """El nav de staff se queda en 8 ítems: la sección entra por «Mi
        gimnasio» y por la tarjeta del dashboard."""
        from pathlib import Path

        from django.conf import settings

        base = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text()
        self.assertNotIn("ingreso:", base)


# ---------------------------------------------------------------------------
# Secretos fuera del caché de historial de htmx
# ---------------------------------------------------------------------------


class SecretosFueraDelHistorialDeHtmxTests(_BaseVistas):
    """Toda pantalla que muestra un secreto UNA vez lleva `hx-history="false"`.

    Antes de navegar, htmx 2 (el que carga `base.html`) guarda el `<body>`
    actual en `localStorage["htmx-history-cache"]`, sin vencimiento, y el
    «atrás» lo restaura sin pedirlo al servidor: el `no-store` no alcanza. Con
    el atributo en cualquier elemento de la página, htmx no la guarda.
    """

    ATRIBUTO = 'hx-history="false"'

    def _clave_de_un_lector(self):
        self._login()
        return self.client.post(
            reverse("ingreso:dispositivo_crear"),
            {"nombre": "Molinete", "tipo": Dispositivo.Tipo.QR},
        )

    def _clave_rotada(self):
        self._login()
        return self.client.post(
            reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        )

    def _contrasenia_de_un_alumno(self):
        self._login()
        return self.client.post(
            reverse("alumnos:acceso_crear", args=[self.alumno.pk]),
            {"tipo": "email", "identificador": "juan@ejemplo.com"},
        )

    def _contrasenia_de_un_gimnasio_nuevo(self):
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", CLAVE)
        self.client.login(username="jefe", password=CLAVE)
        return self.client.post(
            reverse("plataforma:gimnasio_crear"),
            {"nombre": "Gimnasio Nuevo", "email": "duenio@ejemplo.com"},
        )

    def test_cada_pantalla_de_secreto_queda_fuera_del_historial(self):
        pantallas = {
            "ingreso/dispositivo_token.html (alta)": (
                self._clave_de_un_lector, "token"
            ),
            "ingreso/dispositivo_token.html (rotación)": (
                self._clave_rotada, "token"
            ),
            "alumnos/acceso_credenciales.html": (
                self._contrasenia_de_un_alumno, "password"
            ),
            "plataforma/gimnasio_creado.html": (
                self._contrasenia_de_un_gimnasio_nuevo, "password"
            ),
        }
        for nombre, (pedir, clave) in pantallas.items():
            with self.subTest(nombre):
                self.client.logout()
                respuesta = pedir()
                self.assertEqual(respuesta.status_code, 200)
                # Que de verdad sea la pantalla del secreto, no un error.
                secreto = respuesta.context[clave]
                self.assertTrue(secreto)
                self.assertContains(respuesta, secreto)
                self.assertContains(respuesta, self.ATRIBUTO)


class _FormsDeLaPagina(HTMLParser):
    """Junta los atributos de cada `<form>` de una página."""

    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append(dict(attrs))


class FormsDeSecretoFueraDelBoostTests(_BaseVistas):
    """Todo form cuyo POST devuelve un secreto en la respuesta sale del boost.

    `hx-history="false"` + `no-store` cubren el `localStorage` de htmx y el
    cache HTTP, pero no el bfcache: un POST boosteado mete el 200 con la
    clave ADENTRO del documento anterior, que no lleva `no-store`, y después de
    «Salir» ese documento vuelve con «atrás». Con `hx-boost="false"` el POST es
    una navegación completa y la respuesta es un documento propio, con su
    `no-store`. Mismo criterio que los links a la ficha con QR
    (`tests_ficha_y_portal.QrComoDocumentoPropioTests`).
    """

    def _forms_que_postean_a(self, respuesta, url, pagina):
        parser = _FormsDeLaPagina()
        parser.feed(respuesta.content.decode())
        return [
            form for form in parser.forms
            if (form.get("method") or "").lower() == "post"
            and (form.get("action") or pagina) == url
        ]

    def _con_acceso(self):
        perfil = Perfil.objects.get(usuario__username="alumno-a")
        Alumno.objects.filter(pk=self.alumno.pk).update(perfil=perfil)

    def test_cada_form_que_devuelve_un_secreto_sale_del_boost(self):
        alta_lector = reverse("ingreso:dispositivo_crear")
        rotar = reverse("ingreso:dispositivo_rotar", args=[self.puerta.pk])
        alta_acceso = reverse("alumnos:acceso_crear", args=[self.alumno.pk])
        regenerar = reverse("alumnos:acceso_regenerar", args=[self.alumno.pk])
        ficha = reverse("alumnos:detalle", args=[self.alumno.pk])
        accesos = reverse("alumnos:accesos")
        alta_gimnasio = reverse("plataforma:gimnasio_crear")
        # (página que tiene el form, adónde postea, quién la abre, preparar)
        casos = {
            "alta de lector": (alta_lector, alta_lector, "staff-a", None),
            "rotar la clave del lector": (rotar, rotar, "staff-a", None),
            "alta de acceso del alumno": (alta_acceso, alta_acceso, "staff-a", None),
            "contraseña nueva desde la ficha": (
                ficha, regenerar, "staff-a", self._con_acceso
            ),
            "contraseña nueva desde el panel de accesos": (
                accesos, regenerar, "staff-a", self._con_acceso
            ),
            "alta de gimnasio": (alta_gimnasio, alta_gimnasio, "jefe", None),
        }
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", CLAVE)
        for nombre, (pagina, destino, usuario, preparar) in casos.items():
            with self.subTest(nombre):
                if preparar is not None:
                    preparar()
                self.client.logout()
                self._login(usuario)
                respuesta = self.client.get(pagina)
                self.assertEqual(respuesta.status_code, 200)
                forms = self._forms_que_postean_a(respuesta, destino, pagina)
                self.assertTrue(forms, f"no encontré el form que postea a {destino}")
                for form in forms:
                    self.assertEqual(form.get("hx-boost"), "false")


# ---------------------------------------------------------------------------
# Cuenta suspendida: el superadmin revoca desde /admin/
# ---------------------------------------------------------------------------


class RevocarDesdeElAdminTests(_BaseVistas):
    """Con la cuenta SUSPENDIDA el dueño del gimnasio no ve las pantallas de
    `ingreso` (riesgo aceptado 14 de la spec): un token robado lo revoca el
    dueño del producto desde `/admin/`, destildando `activo`."""

    def setUp(self):
        super().setUp()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )
        self.token = tokens.generar()
        Dispositivo.objects.filter(pk=self.puerta.pk).update(
            token_hash=tokens.hash_de(self.token)
        )
        self.puerta.refresh_from_db()
        User.objects.create_superuser("jefe", "jefe@ejemplo.com", CLAVE)

    def _padron(self):
        return self.client.get(
            reverse("ingreso:api_padron"), HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )

    def _desactivar_desde_el_admin(self):
        self.client.login(username="jefe", password=CLAVE)
        respuesta = self.client.post(
            reverse("admin:ingreso_dispositivo_change", args=[self.puerta.pk]),
            {
                "gimnasio": self.gimnasio.pk,
                "nombre": self.puerta.nombre,
                "tipo": self.puerta.tipo,
                # `activo` ausente = destildado
            },
        )
        self.client.logout()
        return respuesta

    def test_desactivado_desde_el_admin_la_api_responde_403(self):
        self.assertEqual(self._padron().status_code, 200)

        respuesta = self._desactivar_desde_el_admin()

        self.assertEqual(respuesta.status_code, 302)
        self.puerta.refresh_from_db()
        self.assertFalse(self.puerta.activo)
        self.assertEqual(self._padron().status_code, 403)

    def test_desactivar_desde_el_admin_invalida_el_set_de_activos(self):
        self.assertIn(self.puerta.token_hash, limites.hashes_activos())

        self._desactivar_desde_el_admin()

        self.assertNotIn(self.puerta.token_hash, limites.hashes_activos())

    def test_el_admin_no_permite_dar_de_alta_un_lector(self):
        """Desde el admin el lector nacía sin hash (no abre nada) y el segundo
        daba 500 contra la unicidad de `token_hash`. El alta es
        `services.crear_dispositivo`, desde el panel del gimnasio."""
        self.client.login(username="jefe", password=CLAVE)

        alta = self.client.get(reverse("admin:ingreso_dispositivo_add"))
        post = self.client.post(
            reverse("admin:ingreso_dispositivo_add"),
            {"gimnasio": self.gimnasio.pk, "nombre": "Nuevo", "tipo": "qr",
             "activo": "on"},
        )
        listado = self.client.get(reverse("admin:ingreso_dispositivo_changelist"))

        self.assertEqual(alta.status_code, 403)
        self.assertEqual(post.status_code, 403)
        self.assertFalse(Dispositivo.objects.filter(nombre="Nuevo").exists())
        self.assertNotContains(listado, reverse("admin:ingreso_dispositivo_add"))

    def test_el_admin_no_muestra_el_hash_del_token(self):
        self.client.login(username="jefe", password=CLAVE)

        ficha = self.client.get(
            reverse("admin:ingreso_dispositivo_change", args=[self.puerta.pk])
        )
        listado = self.client.get(reverse("admin:ingreso_dispositivo_changelist"))

        self.assertEqual(ficha.status_code, 200)
        self.assertEqual(listado.status_code, 200)
        self.assertNotContains(ficha, self.puerta.token_hash[:12])
        self.assertNotContains(listado, self.puerta.token_hash[:12])


# ---------------------------------------------------------------------------
# ingreso:instructivo y las descargas para el instalador
# ---------------------------------------------------------------------------


class InstructivoTests(_GuardiasMixin, _BaseVistas):
    def _url_guardia(self):
        return reverse("ingreso:instructivo")

    def test_cubre_los_tres_casos_con_sus_anclas(self):
        self._login()
        respuesta = self.client.get(self._url_guardia())
        self.assertEqual(respuesta.status_code, 200)
        for ancla in ('id="qr"', 'id="facial"', 'id="ambos"'):
            self.assertContains(respuesta, ancla)
        self.assertContains(respuesta, "Molinete con QR")
        self.assertContains(respuesta, "Lector facial")

    def test_muestra_las_direcciones_absolutas_de_la_api(self):
        """El instalador las carga en el equipo: tienen que ser completas, no
        rutas relativas."""
        self._login()
        respuesta = self.client.get(self._url_guardia())
        self.assertContains(respuesta, "http://testserver/ingreso/v1/eventos/")
        self.assertContains(respuesta, "http://testserver/ingreso/v1/padron/")

    def test_no_muestra_ninguna_clave_de_lector(self):
        self._login()
        respuesta = self.client.get(self._url_guardia())
        self.assertNotContains(respuesta, self.puerta.token_hash[:12])

    def test_las_descargas_van_fuera_del_boost(self):
        """htmx se traga una respuesta con `Content-Disposition: attachment`."""
        self._login()
        contenido = self.client.get(self._url_guardia()).content.decode()
        for nombre in ("guia", "puente"):
            url = reverse("ingreso:archivo_instalador", args=[nombre])
            self.assertIn(f'hx-boost="false" href="{url}"', contenido)

    def test_se_llega_desde_las_pantallas_de_la_puerta(self):
        self._login()
        url = self._url_guardia()
        for pantalla in ("ingreso:listado", "ingreso:dispositivos", "ingreso:configuracion"):
            self.assertContains(self.client.get(reverse(pantalla)), f'href="{url}"')
        self.assertContains(self.client.get(reverse("gimnasio_editar")), f'href="{url}"')

    def test_la_pantalla_de_la_clave_linkea_al_instructivo(self):
        self._login()
        respuesta = self.client.post(
            reverse("ingreso:dispositivo_crear"),
            {"nombre": "Puerta nueva", "tipo": Dispositivo.Tipo.QR},
        )
        self.assertContains(respuesta, f'href="{self._url_guardia()}"')


class ArchivoParaInstaladorTests(_BaseVistas):
    def test_descarga_la_guia_tal_cual_esta_en_el_repo(self):
        from django.conf import settings

        self._login()
        respuesta = self.client.get(reverse("ingreso:archivo_instalador", args=["guia"]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("attachment", respuesta["Content-Disposition"])
        esperado = (settings.BASE_DIR / "docs/integracion-control-de-ingreso.md").read_bytes()
        self.assertEqual(respuesta.content, esperado)

    def test_descarga_el_puente_de_ejemplo(self):
        self._login()
        respuesta = self.client.get(reverse("ingreso:archivo_instalador", args=["puente"]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('filename="puente_ingreso_ejemplo.py"', respuesta["Content-Disposition"])

    def test_un_nombre_fuera_de_la_lista_da_404(self):
        """La URL elige de una lista cerrada, nunca arma una ruta de disco."""
        self._login()
        for nombre in ("settings", "env", "manage"):
            respuesta = self.client.get(reverse("ingreso:archivo_instalador", args=[nombre]))
            self.assertEqual(respuesta.status_code, 404)

    def test_alumno_recibe_403_y_anonimo_va_al_login(self):
        url = reverse("ingreso:archivo_instalador", args=["guia"])
        self.assertEqual(self.client.get(url).status_code, 302)
        self._login("alumno-a")
        self.assertEqual(self.client.get(url).status_code, 403)
