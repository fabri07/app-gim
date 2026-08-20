"""Tests de `tenants/google_login.py` (Frente C: login con Google para staff).

Mismo criterio que `calendario/tests.py`: `GOOGLE_STAFF_LOGIN_ENABLED` y las
credenciales se fijan por `override_settings`. El armado de la URL de
autorización es puramente local (sin red) y se testea real, mismo criterio
que el test PKCE de `calendario/tests.py`. El intercambio de code y la
verificación del id_token SÍ pegan a la red real de Google, así que se
mockean en la costura (`intercambiar_code`/`_decodificar_id_token`), no la
librería de más abajo -- mismo espíritu que `calendario/tests.py` mockeando
`calendario.services.intercambiar_code`.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tenants import google_login
from tenants.models import Gimnasio, Perfil

GOOGLE_LOGIN_ON = dict(
    GOOGLE_STAFF_LOGIN_ENABLED=True,
    GOOGLE_OAUTH_CLIENT_ID="cid",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_LOGIN_REDIRECT_URI="https://app.example.com/accounts/google/callback/",
)


class DisponibleTests(TestCase):
    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=True)
    def test_true_cuando_flag_prendido(self):
        self.assertTrue(google_login.disponible())

    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=False)
    def test_false_cuando_flag_apagado(self):
        self.assertFalse(google_login.disponible())


@override_settings(**GOOGLE_LOGIN_ON)
class BuildAuthorizationUrlTests(TestCase):
    """Regresión PKCE, mismo motivo que calendario/tests.py: si el
    code_verifier no persiste entre esta llamada y el callback, Google
    rechaza el intercambio con invalid_grant."""

    def test_devuelve_url_state_y_code_verifier(self):
        url, state, verifier = google_login.build_authorization_url()

        self.assertIn("code_challenge=", url)
        self.assertIn("accounts.google.com", url)
        self.assertTrue(state)
        self.assertTrue(verifier)


@override_settings(**GOOGLE_LOGIN_ON)
class VerificarIdentidadTests(TestCase):
    @patch("tenants.google_login.intercambiar_code")
    @patch("tenants.google_login._decodificar_id_token")
    def test_devuelve_email_cuando_esta_verificado(self, mock_decodificar, mock_intercambiar):
        mock_intercambiar.return_value = MagicMock(id_token="jwt-falso")
        mock_decodificar.return_value = {"email": "dueno@ejemplo.com", "email_verified": True}

        email = google_login.verificar_identidad("code123", "state123", "verifier123")

        self.assertEqual(email, "dueno@ejemplo.com")
        mock_intercambiar.assert_called_once_with("code123", "state123", "verifier123")
        mock_decodificar.assert_called_once_with("jwt-falso")

    @patch("tenants.google_login.intercambiar_code")
    @patch("tenants.google_login._decodificar_id_token")
    def test_rechaza_email_no_verificado(self, mock_decodificar, mock_intercambiar):
        mock_intercambiar.return_value = MagicMock(id_token="jwt-falso")
        mock_decodificar.return_value = {"email": "raro@ejemplo.com", "email_verified": False}

        with self.assertRaises(google_login.IdentidadNoVerificada):
            google_login.verificar_identidad("code123", "state123", "verifier123")


class GoogleLoginRedirectViewTests(TestCase):
    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=False)
    def test_flag_apagado_redirige_al_login_con_aviso(self):
        response = self.client.get(reverse("login_google"))
        self.assertRedirects(response, reverse("login"))

    @override_settings(**GOOGLE_LOGIN_ON)
    @patch(
        "tenants.google_login.build_authorization_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?x=1", "st_123", "verif_123"),
    )
    def test_redirige_a_google_y_guarda_state_en_sesion(self, _mock):
        response = self.client.get(reverse("login_google"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        self.assertEqual(self.client.session["google_login_state"], "st_123")
        self.assertEqual(self.client.session["google_login_verifier"], "verif_123")

    @override_settings(**GOOGLE_LOGIN_ON)
    @patch(
        "tenants.google_login.build_authorization_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?x=1", "st_123", "verif_123"),
    )
    def test_guarda_next_valido_en_sesion(self, _mock):
        self.client.get(reverse("login_google"), {"next": "/pagos/"})
        self.assertEqual(self.client.session["google_login_next"], "/pagos/")

    @override_settings(**GOOGLE_LOGIN_ON)
    @patch(
        "tenants.google_login.build_authorization_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?x=1", "st_123", "verif_123"),
    )
    def test_next_inseguro_se_descarta(self, _mock):
        self.client.get(reverse("login_google"), {"next": "https://evil.com/robar"})
        self.assertEqual(self.client.session.get("google_login_next"), "")


@override_settings(**GOOGLE_LOGIN_ON)
class GoogleLoginCallbackViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.dueno = User.objects.create_user("dueno@ejemplo.com")
        Perfil.objects.create(usuario=self.dueno, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def _sembrar_state(self, state="st_ok", next_url=""):
        session = self.client.session
        session["google_login_state"] = state
        session["google_login_state_ts"] = timezone.now().isoformat()
        session["google_login_verifier"] = "verif_ok"
        session["google_login_next"] = next_url
        session.save()

    @patch("tenants.google_login.verificar_identidad", return_value="dueno@ejemplo.com")
    def test_login_exitoso_con_email_de_staff(self, _mock):
        self._sembrar_state()
        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})
        self.assertRedirects(response, reverse("home"))
        self.assertIn("_auth_user_id", self.client.session)

    @patch("tenants.google_login.verificar_identidad", return_value="dueno@ejemplo.com")
    def test_login_exitoso_setea_cookie_gimnasio_preferido(self, _mock):
        self._sembrar_state()
        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})
        self.assertEqual(response.cookies["gimnasio_preferido"].value, "a")

    @patch("tenants.google_login.verificar_identidad", return_value="dueno@ejemplo.com")
    def test_login_exitoso_respeta_next(self, _mock):
        self._sembrar_state(next_url="/pagos/")
        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})
        self.assertRedirects(response, "/pagos/")

    def test_state_invalido_no_loguea(self):
        self._sembrar_state("st_ok")
        response = self.client.get(
            reverse("login_google_callback"), {"code": "c", "state": "st_MALO"}
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_error_de_google_no_loguea(self):
        self._sembrar_state("st_ok")
        response = self.client.get(
            reverse("login_google_callback"), {"error": "access_denied", "state": "st_ok"}
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("tenants.google_login.verificar_identidad", return_value="nadie@ejemplo.com")
    def test_email_sin_cuenta_no_loguea(self, _mock):
        self._sembrar_state()
        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("tenants.google_login.verificar_identidad", return_value="alumno@ejemplo.com")
    def test_email_de_alumno_no_loguea(self, _mock):
        alumno_user = User.objects.create_user("alumno@ejemplo.com")
        Perfil.objects.create(usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self._sembrar_state()

        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("tenants.google_login.verificar_identidad", return_value="dueno-inactivo@ejemplo.com")
    def test_staff_inactivo_no_loguea(self, _mock):
        inactivo = User.objects.create_user("dueno-inactivo@ejemplo.com", is_active=False)
        Perfil.objects.create(usuario=inactivo, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self._sembrar_state()

        response = self.client.get(reverse("login_google_callback"), {"code": "c", "state": "st_ok"})

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class LoginTemplateGoogleButtonTests(TestCase):
    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=True)
    def test_boton_aparece_si_esta_disponible(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Iniciar sesión con Google")

    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=False)
    def test_boton_no_aparece_si_no_esta_disponible(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "Iniciar sesión con Google")

    @override_settings(GOOGLE_STAFF_LOGIN_ENABLED=True)
    def test_boton_lleva_hx_boost_false(self):
        # Sin loguearse, la página ya trae 2 hx-boost="false" (el link de
        # marca del topbar y el form de contraseña) -- si el botón de Google
        # no lo llevara, este count seguiría dando 2 en vez de 3 y el test
        # lo detectaría (a diferencia de un assertContains simple, que
        # pasaría igual con cualquiera de los otros dos).
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'hx-boost="false"', count=3)
