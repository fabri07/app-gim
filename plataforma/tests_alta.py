"""
Tests del alta de un gimnasio desde el panel de plataforma.

Lo que se cubre acá es la pantalla, no el alta en sí: `crear_gimnasio` ya
tiene sus propios tests en `tenants`. Lo que puede romperse en esta capa es
otra cosa y es lo que se fija abajo -- que la contraseña se vea UNA vez y no
quede en la caché del navegador, que de verdad sirva para entrar, que un
email repetido no deje un gimnasio a medio crear, y que el panel siga siendo
del dueño del producto y de nadie más.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenants.models import Gimnasio, Perfil


def _superadmin():
    return User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")


def _datos(**extra):
    datos = {"nombre": "Gimnasio Central", "email": "duenio@ejemplo.com"}
    datos.update(extra)
    return datos


class AltaDeGimnasioTests(TestCase):
    """El camino feliz: un gimnasio nuevo con su cuenta de staff."""

    def setUp(self):
        _superadmin()
        self.client.login(username="jefe", password="clave-123456")
        self.url = reverse("plataforma:gimnasio_crear")

    def test_get_muestra_el_formulario(self):
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "name=\"nombre\"")
        self.assertContains(respuesta, "name=\"email\"")

    def test_crea_gimnasio_usuario_y_perfil_staff(self):
        respuesta = self.client.post(self.url, _datos())

        self.assertEqual(respuesta.status_code, 200)
        gimnasio = Gimnasio.objects.get(nombre="Gimnasio Central")
        self.assertEqual(gimnasio.slug, "gimnasio-central")
        perfil = Perfil.objects.get(gimnasio=gimnasio)
        self.assertEqual(perfil.rol, Perfil.Rol.STAFF)
        self.assertEqual(perfil.usuario.username, "duenio@ejemplo.com")

    def test_muestra_la_contrasenia_una_sola_vez_y_sin_cache(self):
        """La pantalla se abre en la computadora del dueño del producto y la
        contraseña se lee una única vez: sin `no-store` queda recuperable con
        el botón "atrás" del navegador.
        """
        respuesta = self.client.post(self.url, _datos())

        password = respuesta.context["password"]
        self.assertTrue(password)
        self.assertContains(respuesta, password)
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_la_contrasenia_que_muestra_sirve_para_entrar(self):
        """Lo único que prueba que la pantalla no miente: que ese texto, tal
        cual se imprimió, loguea al dueño del gimnasio recién creado.
        """
        respuesta = self.client.post(self.url, _datos())

        self.client.logout()
        self.assertTrue(
            self.client.login(
                username="duenio@ejemplo.com",
                password=respuesta.context["password"],
            )
        )

    def test_el_email_se_normaliza_a_minusculas(self):
        """`User.objects.get(username=...)` es case-sensitive en Postgres: sin
        normalizar, el dueño tipea su mail como siempre y no entra.
        """
        self.client.post(self.url, _datos(email="  Duenio@Ejemplo.COM "))

        self.assertTrue(User.objects.filter(username="duenio@ejemplo.com").exists())

    def test_slug_explicito_se_respeta(self):
        self.client.post(self.url, _datos(slug="vida-plena"))

        self.assertEqual(Gimnasio.objects.get().slug, "vida-plena")

    def test_es_demo_nace_exento_de_facturacion(self):
        """La cuenta de demostración no es de nadie: el monitor no puede
        contarla como un cliente que debe plata.
        """
        self.client.post(self.url, _datos(es_demo="on"))

        gimnasio = Gimnasio.objects.get()
        self.assertTrue(gimnasio.es_demo)
        self.assertTrue(gimnasio.facturacion_exenta)

    def test_sin_password_no_muestra_ninguna_contrasenia(self):
        """La cuenta entra solo con Google: inventar una contraseña para la
        pantalla sería mostrar un dato que no sirve para nada.
        """
        respuesta = self.client.post(self.url, _datos(sin_password="on"))

        self.assertIsNone(respuesta.context["password"])
        usuario = User.objects.get(username="duenio@ejemplo.com")
        self.assertFalse(usuario.has_usable_password())

    def test_el_gimnasio_nuevo_arranca_en_estado_normal(self):
        self.client.post(self.url, _datos())

        self.assertEqual(
            Gimnasio.objects.get().estado_cuenta, Gimnasio.EstadoCuenta.NORMAL
        )

    def test_la_pantalla_enlaza_la_ficha_y_el_login_del_gimnasio(self):
        respuesta = self.client.post(self.url, _datos())

        gimnasio = Gimnasio.objects.get()
        self.assertContains(
            respuesta,
            reverse("plataforma:gimnasio_detalle", args=[gimnasio.pk]),
        )
        self.assertContains(
            respuesta, reverse("login_gimnasio", args=[gimnasio.slug])
        )


class AltaDeGimnasioRechazosTests(TestCase):
    """Los dos choques posibles, cada uno con su mensaje en pantalla."""

    def setUp(self):
        _superadmin()
        self.client.login(username="jefe", password="clave-123456")
        self.url = reverse("plataforma:gimnasio_crear")

    def test_email_repetido_no_crea_nada_y_lo_explica(self):
        """Sin el `try/except` de la vista, el `ValidationError` del servicio
        sale como un 500 mudo. Y sin la atomicidad del servicio, quedaría un
        gimnasio huérfano por cada intento fallido.
        """
        User.objects.create_user("duenio@ejemplo.com", password="clave-123456")

        respuesta = self.client.post(self.url, _datos())

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "duenio@ejemplo.com")
        self.assertTrue(respuesta.context["form"].non_field_errors())
        self.assertFalse(Gimnasio.objects.exists())

    def test_slug_ya_tomado_es_un_error_del_campo(self):
        """Colgado del campo y no del formulario entero: es ese campo el que
        hay que corregir, y la `UniqueConstraint` sola daría un 500.
        """
        Gimnasio.objects.create(nombre="Vida Plena", slug="vida-plena")

        respuesta = self.client.post(self.url, _datos(slug="vida-plena"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("slug", respuesta.context["form"].errors)
        self.assertEqual(Gimnasio.objects.count(), 1)

    def test_email_invalido_es_un_error_del_campo(self):
        respuesta = self.client.post(self.url, _datos(email="no-es-un-mail"))

        self.assertIn("email", respuesta.context["form"].errors)
        self.assertFalse(Gimnasio.objects.exists())


class AltaDeGimnasioAccesoTests(TestCase):
    """Dar de alta un gimnasio es lo más caro que se puede hacer desde este
    panel: crea un tenant y una cuenta con rol staff. Mismo 403 que el resto
    del panel, y verificado también sobre el POST -- un mixin puede quedar
    cubriendo el GET y no la escritura.
    """

    def setUp(self):
        self.url = reverse("plataforma:gimnasio_crear")

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def test_staff_de_gimnasio_recibe_403(self):
        gimnasio = Gimnasio.objects.create(nombre="Vida Plena", slug="vida-plena")
        usuario = User.objects.create_user("duenio", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="duenio", password="clave-123456")

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, _datos()).status_code, 403)
        self.assertEqual(Gimnasio.objects.count(), 1)
