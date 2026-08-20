"""Tests del reset de contraseña por email -- SOLO para cuentas de staff.

`PASSWORD_RESET_ENABLED` se fija por `override_settings` en los tests que
la necesitan; Django ya fuerza `EMAIL_BACKEND` a `locmem` durante los tests
(comportamiento built-in del test runner), así que los mails quedan
capturados en `django.core.mail.outbox` sin pegarle a la red real.
"""

import re

from django.contrib.auth.models import User
from django.core import mail
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from tenants.forms import ResetPasswordStaffForm
from tenants.models import Gimnasio, Perfil


class ResetPasswordStaffFormTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")

    def test_devuelve_al_staff_activo_con_ese_email(self):
        staff = User.objects.create_user(
            "dueno@ejemplo.com", email="dueno@ejemplo.com", password="clave-123456"
        )
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

        form = ResetPasswordStaffForm(data={"email": "dueno@ejemplo.com"})
        self.assertTrue(form.is_valid())
        usuarios = list(form.get_users("dueno@ejemplo.com"))

        self.assertEqual(usuarios, [staff])

    def test_no_devuelve_a_un_alumno_con_email_como_identificador(self):
        # Mismo patrón exacto que dispara el hallazgo del plan: un alumno
        # cuyo identificador es un email queda con User.email poblado,
        # igual que una cuenta de staff (alumnos/services.py::crear_acceso).
        alumno_user = User.objects.create_user(
            "alumno@ejemplo.com", email="alumno@ejemplo.com", password="clave-123456"
        )
        Perfil.objects.create(usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)

        form = ResetPasswordStaffForm(data={"email": "alumno@ejemplo.com"})
        self.assertTrue(form.is_valid())
        usuarios = list(form.get_users("alumno@ejemplo.com"))

        self.assertEqual(usuarios, [])

    def test_no_devuelve_staff_inactivo(self):
        staff = User.objects.create_user(
            "dueno2@ejemplo.com",
            email="dueno2@ejemplo.com",
            password="clave-123456",
            is_active=False,
        )
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

        form = ResetPasswordStaffForm(data={"email": "dueno2@ejemplo.com"})
        self.assertTrue(form.is_valid())
        usuarios = list(form.get_users("dueno2@ejemplo.com"))

        self.assertEqual(usuarios, [])

    def test_no_devuelve_staff_sin_contrasena_usable(self):
        staff = User.objects.create_user("dueno3@ejemplo.com", email="dueno3@ejemplo.com")
        staff.set_unusable_password()
        staff.save()
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

        form = ResetPasswordStaffForm(data={"email": "dueno3@ejemplo.com"})
        self.assertTrue(form.is_valid())
        usuarios = list(form.get_users("dueno3@ejemplo.com"))

        self.assertEqual(usuarios, [])

    def test_no_devuelve_nada_si_el_email_no_existe(self):
        form = ResetPasswordStaffForm(data={"email": "nadie@ejemplo.com"})
        self.assertTrue(form.is_valid())
        usuarios = list(form.get_users("nadie@ejemplo.com"))

        self.assertEqual(usuarios, [])


class PasswordResetFlowTests(TestCase):
    """`PASSWORD_RESET_ENABLED` no se simula acá a propósito: las 4 vistas
    están siempre registradas (ver plan) -- lo que esa bandera controla es
    solo la visibilidad del link en login.html (LoginTemplateLinkTests,
    abajo)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.staff = User.objects.create_user(
            "dueno@ejemplo.com", email="dueno@ejemplo.com", password="clave-vieja-123"
        )
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_pedir_reset_con_email_de_staff_manda_mail(self):
        response = self.client.post(reverse("password_reset"), {"email": "dueno@ejemplo.com"})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("dueno@ejemplo.com", mail.outbox[0].to)

    def test_pedir_reset_con_email_de_alumno_no_manda_mail(self):
        alumno_user = User.objects.create_user(
            "alumno@ejemplo.com", email="alumno@ejemplo.com", password="clave-123456"
        )
        Perfil.objects.create(usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)

        response = self.client.post(reverse("password_reset"), {"email": "alumno@ejemplo.com"})

        # Misma pantalla que si el email hubiera mandado el mail --
        # anti-enumeración: no hay forma de distinguir desde afuera.
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_flujo_completo_confirma_loguea_y_setea_cookie(self):
        self.client.post(reverse("password_reset"), {"email": "dueno@ejemplo.com"})
        self.assertEqual(len(mail.outbox), 1)

        match = re.search(r"https?://[^\s]+(/accounts/reset/\S+)", mail.outbox[0].body)
        self.assertIsNotNone(match, "el mail no trae un link de reset")
        link_path = match.group(1)

        # Primer GET con el token real: Django lo valida y redirige a la
        # misma URL con el token reemplazado por "set-password" (para que
        # el token real nunca quede en el historial/Referer del navegador).
        response = self.client.get(link_path, follow=True)
        self.assertEqual(response.status_code, 200)
        set_password_path = response.redirect_chain[-1][0]

        response2 = self.client.post(
            set_password_path,
            {"new_password1": "una-clave-nueva-999", "new_password2": "una-clave-nueva-999"},
        )

        self.assertRedirects(response2, reverse("password_reset_complete"))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("una-clave-nueva-999"))
        # post_reset_login=True: queda logueado sin volver a tipear nada.
        self.assertIn("_auth_user_id", self.client.session)
        # Mismo efecto secundario que el login por contraseña/Google.
        self.assertEqual(response2.cookies["gimnasio_preferido"].value, "a")

    def test_token_invalido_no_deja_confirmar(self):
        response = self.client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": "abc", "token": "token-invalido"})
        )
        self.assertFalse(response.context["validlink"])

    def test_staff_desactivado_despues_de_pedir_no_puede_confirmar(self):
        # El token de Django no incluye is_active en su hash -- desactivar
        # la cuenta DESPUÉS de pedir el reset no invalida un link ya
        # emitido, a menos que la vista lo revalide explícitamente (mismo
        # gotcha que tenants/suplantacion.py::iniciar y
        # GoogleLoginCallbackView ya cubren: login() de Django no valida
        # is_active por su cuenta).
        self.client.post(reverse("password_reset"), {"email": "dueno@ejemplo.com"})
        match = re.search(r"https?://[^\s]+(/accounts/reset/\S+)", mail.outbox[0].body)
        link_path = match.group(1)

        self.staff.is_active = False
        self.staff.save()

        response = self.client.get(link_path, follow=True)

        self.assertFalse(response.context["validlink"])
        self.assertNotIn("_auth_user_id", self.client.session)


class PasswordResetEmailTemplateTests(TestCase):
    def test_no_escapa_entidades_html_en_el_cuerpo_texto_plano(self):
        # DjangoTemplates autoescapa por default sin importar la extensión
        # del archivo -- el propio template que Django trae de fábrica para
        # esto envuelve el cuerpo en {% autoescape off %} por este motivo.
        # Un "&" suelto (válido en un local-part citado de un email) no
        # debería llegar como "&amp;" en un mensaje de texto plano.
        cuerpo = render_to_string(
            "registration/password_reset_email.html",
            {
                "email": "dueño & socio@ejemplo.com",
                "domain": "tugimapp.com",
                "protocol": "https",
                "uid": "uid123",
                "token": "token123",
            },
        )
        self.assertIn("dueño & socio@ejemplo.com", cuerpo)
        self.assertNotIn("&amp;", cuerpo)


class LoginTemplateLinkTests(TestCase):
    def test_link_aparece_si_esta_habilitado(self):
        with self.settings(PASSWORD_RESET_ENABLED=True):
            response = self.client.get(reverse("login"))
        self.assertContains(response, "Olvidaste tu contraseña")

    def test_link_lleva_hx_boost_false(self):
        # GOOGLE_STAFF_LOGIN_ENABLED explícito en False: si quedara en lo
        # que tenga el entorno (el .env local de este proyecto SÍ trae
        # login con Google configurado), el botón de Google metería un
        # hx-boost="false" de más y el test "pasaría" sin que este link lo
        # llevara -- pasó exactamente eso en la primera versión de este
        # test. Sin loguearse, la página trae 2 hx-boost="false" (el link
        # de marca del topbar y el form de contraseña); si este link no lo
        # llevara, el count seguiría dando 2 en vez de 3.
        with self.settings(PASSWORD_RESET_ENABLED=True, GOOGLE_STAFF_LOGIN_ENABLED=False):
            response = self.client.get(reverse("login"))
        self.assertContains(response, 'hx-boost="false"', count=3)

    def test_link_no_aparece_si_no_esta_habilitado(self):
        with self.settings(PASSWORD_RESET_ENABLED=False):
            response = self.client.get(reverse("login"))
        self.assertNotContains(response, "Olvidaste tu contraseña")
