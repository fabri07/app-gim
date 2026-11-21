"""Pieza 1 de reconocimientos: bandera, T&C, foto y «Mi perfil»."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil

CLAVE = "clave-123456"


def _gimnasio(**extra):
    return Gimnasio.objects.create(nombre="Vida Plena", slug="vida-plena", **extra)


def _staff(gimnasio, username="duenio"):
    usuario = User.objects.create_user(username, password=CLAVE)
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return usuario


def _alumno(gimnasio, username="alumno"):
    usuario = User.objects.create_user(username, password=CLAVE)
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno = Alumno.objects.create(
        gimnasio=gimnasio, nombre="Ana", apellido="Pérez", perfil=perfil,
        estado=Alumno.Estado.ACTIVO,
    )
    return alumno, usuario


class BanderaReconocimientosTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        _staff(self.gimnasio)
        self.client.login(username="duenio", password=CLAVE)

    def _post(self, **extra):
        datos = {
            "nombre": "Vida Plena",
            "tipo_publico": Gimnasio.TipoPublico.MIXTO,
            "paleta": Gimnasio.Paleta.BOSQUE,
            "tipografia": Gimnasio.Tipografia.PLUS_JAKARTA,
            "fondo_tipo": Gimnasio.FondoTipo.COLOR,
        }
        datos.update(extra)
        return self.client.post(reverse("gimnasio_editar"), datos)

    def test_default_apagada(self):
        self.assertIs(self.gimnasio.reconocimientos_habilitado, False)

    def test_el_dueño_la_prende_desde_mi_gimnasio(self):
        self._post(reconocimientos_habilitado="on")

        self.gimnasio.refresh_from_db()
        self.assertIs(self.gimnasio.reconocimientos_habilitado, True)

    def test_un_post_sin_el_campo_la_apaga_como_cualquier_checkbox(self):
        self._post(reconocimientos_habilitado="on")
        self._post()

        self.gimnasio.refresh_from_db()
        self.assertIs(self.gimnasio.reconocimientos_habilitado, False)


class DebeAceptarTerminosTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio(reconocimientos_habilitado=True)

    def _perfil_alumno(self):
        _alumno(self.gimnasio)
        return Perfil.objects.get(usuario__username="alumno")

    def test_alumno_con_bandera_prendida_y_sin_aceptar_debe_aceptar(self):
        from tenants.terminos import debe_aceptar_terminos

        perfil = self._perfil_alumno()
        perfil.refresh_from_db()

        self.assertEqual(perfil.terminos_version, 0)
        self.assertIsNone(perfil.terminos_aceptados_en)
        self.assertIs(debe_aceptar_terminos(perfil), True)

    def test_no_debe_con_la_bandera_apagada(self):
        from tenants.terminos import debe_aceptar_terminos

        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        perfil = self._perfil_alumno()

        self.assertIs(debe_aceptar_terminos(perfil), False)

    def test_no_debe_el_staff(self):
        from tenants.terminos import debe_aceptar_terminos

        _staff(self.gimnasio)
        perfil = Perfil.objects.get(usuario__username="duenio")

        self.assertIs(debe_aceptar_terminos(perfil), False)

    def test_no_debe_sin_perfil(self):
        from tenants.terminos import debe_aceptar_terminos

        self.assertIs(debe_aceptar_terminos(None), False)

    def test_no_debe_con_la_version_vigente(self):
        from tenants.terminos import VERSION_TERMINOS, debe_aceptar_terminos

        perfil = self._perfil_alumno()
        perfil.terminos_version = VERSION_TERMINOS
        perfil.save()

        self.assertIs(debe_aceptar_terminos(perfil), False)

    def test_vuelve_a_deber_si_sube_la_version(self):
        from unittest.mock import patch

        from tenants.terminos import VERSION_TERMINOS, debe_aceptar_terminos

        perfil = self._perfil_alumno()
        perfil.terminos_version = VERSION_TERMINOS
        perfil.save()

        with patch("tenants.terminos.VERSION_TERMINOS", VERSION_TERMINOS + 1):
            self.assertIs(debe_aceptar_terminos(perfil), True)

    def test_no_hace_queries(self):
        from tenants.terminos import debe_aceptar_terminos

        perfil = Perfil.objects.select_related("gimnasio").get(
            pk=self._perfil_alumno().pk
        )

        with self.assertNumQueries(0):
            debe_aceptar_terminos(perfil)


CARTEL = "Términos y condiciones"
NAV = 'id="nav-alumno"'
HAMBURGUESA = 'aria-controls="nav-alumno"'


class CartelTerminosTests(TestCase):
    """El alumno de un gimnasio con reconocimientos prendidos acepta los
    términos UNA vez antes de seguir usando la app."""

    def setUp(self):
        self.gimnasio = _gimnasio(reconocimientos_habilitado=True)
        self.alumno, self.usuario = _alumno(self.gimnasio)

    def _perfil(self):
        return Perfil.objects.get(usuario=self.usuario)

    def _login_alumno(self):
        self.client.force_login(self.usuario)

    def test_alumno_sin_aceptar_ve_el_cartel_en_cualquier_pantalla(self):
        self._login_alumno()

        for nombre, normal in (
            ("home", "Hola, Ana"),
            ("turnos:mis_turnos", "Mis turnos ·"),
        ):
            with self.subTest(nombre):
                respuesta = self.client.get(reverse(nombre))

                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, CARTEL)
                self.assertNotContains(respuesta, normal)
                self.assertIn("no-store", respuesta["Cache-Control"])

    def test_bandera_apagada_no_muestra_nada(self):
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        self._login_alumno()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Hola, Ana")
        self.assertNotContains(respuesta, CARTEL)

    def test_staff_nunca_ve_el_cartel(self):
        self.client.force_login(_staff(self.gimnasio))

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, CARTEL)

    def test_superusuario_nunca_ve_el_cartel(self):
        usuario = User.objects.create_superuser("jefe", password=CLAVE)
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.force_login(usuario)

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, CARTEL)

    def test_aceptar_guarda_fecha_y_version_y_no_vuelve_a_aparecer(self):
        from tenants.terminos import VERSION_TERMINOS

        self._login_alumno()

        respuesta = self.client.post(reverse("terminos_aceptar"), {"acepto": "on"})

        self.assertRedirects(
            respuesta, reverse("home"), fetch_redirect_response=False
        )
        perfil = self._perfil()
        self.assertIsNotNone(perfil.terminos_aceptados_en)
        self.assertEqual(perfil.terminos_version, VERSION_TERMINOS)
        siguiente = self.client.get(reverse("home"))
        self.assertEqual(siguiente.status_code, 200)
        self.assertContains(siguiente, "Hola, Ana")
        self.assertNotContains(siguiente, CARTEL)

    def test_sin_tildar_responde_200_con_el_error_en_el_campo(self):
        self._login_alumno()

        respuesta = self.client.post(reverse("terminos_aceptar"), {})

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tenés que aceptar los términos para seguir.")
        self.assertContains(respuesta, 'class="config-error"')
        perfil = self._perfil()
        self.assertIsNone(perfil.terminos_aceptados_en)
        self.assertEqual(perfil.terminos_version, 0)

    def test_get_de_terminos_muestra_el_cartel_si_falta_aceptar(self):
        self._login_alumno()

        respuesta = self.client.get(reverse("terminos_aceptar"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, CARTEL)
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertNotContains(respuesta, NAV)

    def test_get_de_terminos_sin_nada_que_aceptar_redirige_a_home(self):
        from tenants.terminos import VERSION_TERMINOS

        perfil = self._perfil()
        perfil.terminos_version = VERSION_TERMINOS
        perfil.save()
        staff = _staff(self.gimnasio)

        for usuario in (self.usuario, staff):
            with self.subTest(usuario.username):
                self.client.force_login(usuario)

                respuesta = self.client.get(reverse("terminos_aceptar"))

                self.assertRedirects(
                    respuesta, reverse("home"), fetch_redirect_response=False
                )

    def test_la_pantalla_de_login_y_la_privacidad_no_se_bloquean(self):
        anonimo = self.client.get(reverse("politica_privacidad"))
        self.assertContains(anonimo, "Política de privacidad")

        self._login_alumno()
        logueado = self.client.get(reverse("politica_privacidad"))
        self.assertContains(logueado, "Política de privacidad")
        self.assertNotContains(logueado, CARTEL)
        login = self.client.get(reverse("login"))
        self.assertNotContains(login, CARTEL, status_code=login.status_code)

    def test_la_privacidad_cuenta_la_foto_y_el_ranking(self):
        respuesta = self.client.get(reverse("politica_privacidad"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "foto")
        self.assertContains(respuesta, "ranking")
        self.assertContains(respuesta, "Mi perfil")

    def test_suplantando_no_muestra_el_cartel_ni_deja_aceptar(self):
        self.client.force_login(_staff(self.gimnasio))
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        self.assertIn("suplantacion", self.client.session)

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Hola, Ana")
        self.assertNotContains(respuesta, CARTEL)
        self.client.post(reverse("terminos_aceptar"), {"acepto": "on"})
        perfil = self._perfil()
        self.assertIsNone(perfil.terminos_aceptados_en)
        self.assertEqual(perfil.terminos_version, 0)

    def test_cuenta_congelada_gana_el_cartel_de_bloqueo(self):
        from django.utils import timezone

        gimnasio = Gimnasio.objects.create(
            nombre="Congelado",
            slug="congelado",
            reconocimientos_habilitado=True,
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
            estado_cuenta_desde=timezone.now(),
        )
        _, usuario = _alumno(gimnasio, username="alumno-congelado")
        self.client.force_login(usuario)

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertTemplateNotUsed(respuesta, "tenants/terminos.html")
        self.assertNotContains(respuesta, CARTEL)

    def test_el_cartel_no_trae_el_menu_del_alumno(self):
        self._login_alumno()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, CARTEL)
        self.assertNotContains(respuesta, NAV)
        self.assertNotContains(respuesta, HAMBURGUESA)

    def test_perfil_alumno_sin_ficha_puede_aceptar(self):
        from tenants.terminos import VERSION_TERMINOS

        usuario = User.objects.create_user("sin-ficha", password=CLAVE)
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.force_login(usuario)

        respuesta = self.client.post(reverse("terminos_aceptar"), {"acepto": "on"})

        self.assertRedirects(
            respuesta, reverse("home"), fetch_redirect_response=False
        )
        perfil.refresh_from_db()
        self.assertIsNotNone(perfil.terminos_aceptados_en)
        self.assertEqual(perfil.terminos_version, VERSION_TERMINOS)

    def test_un_post_a_otra_ruta_con_terminos_pendientes_redirige_a_home(self):
        self._login_alumno()

        respuesta = self.client.post(reverse("turnos:mis_turnos"), {"acepto": "on"})

        self.assertRedirects(
            respuesta, reverse("home"), fetch_redirect_response=False
        )
        perfil = self._perfil()
        self.assertIsNone(perfil.terminos_aceptados_en)
        self.assertEqual(perfil.terminos_version, 0)

    def test_los_webhooks_y_la_api_sin_sesion_pasan(self):
        # Sin MERCADOPAGO_* en la suite el webhook responde 503 y lo loguea:
        # `assertLogs` lo ataja para no ensuciar la salida.
        with self.assertLogs("django.request", level="ERROR"):
            webhook = self.client.post(
                reverse("suscripcion:webhook"), {}, content_type="application/json"
            )
        api = self.client.post(
            reverse("ingreso:api_eventos"),
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer token-invalido",
        )

        for nombre, respuesta in (("webhook", webhook), ("api", api)):
            with self.subTest(nombre):
                self.assertNotEqual(respuesta.status_code, 302)
                self.assertNotIn(CARTEL, respuesta.content.decode())
        self.assertEqual(api.status_code, 401)

    def test_decidir_el_cartel_no_agrega_queries(self):
        """El middleware reusa el `Perfil` (con su gimnasio) que ya resolvió
        `PlataformaMiddleware`: decidir no cuesta ninguna query propia."""
        from unittest.mock import patch

        from django.contrib.sessions.backends.base import SessionBase
        from django.http import HttpResponse
        from django.test import RequestFactory
        from django.urls import resolve

        from plataforma.middleware import perfil_de
        from plataforma.terminos import TerminosMiddleware
        from tenants.terminos import VERSION_TERMINOS

        middleware = TerminosMiddleware(lambda request: HttpResponse())

        def _request():
            request = RequestFactory().get(reverse("home"))
            request.user = User.objects.get(pk=self.usuario.pk)
            request.session = SessionBase()
            request.resolver_match = resolve(reverse("home"))
            # Lo que ya pagó `PlataformaMiddleware` en el mismo request.
            perfil_de(request).gimnasio
            return request

        pendiente = _request()
        with patch("tenants.terminos.render", return_value=HttpResponse()):
            with self.assertNumQueries(0):
                respuesta = middleware.process_view(pendiente, None, (), {})
        self.assertIsNotNone(respuesta)

        perfil = self._perfil()
        perfil.terminos_version = VERSION_TERMINOS
        perfil.save()
        aceptado = _request()
        with self.assertNumQueries(0):
            self.assertIsNone(middleware.process_view(aceptado, None, (), {}))
