"""El logo del gimnasio se sirve por una vista propia, con URL versionada.

Por qué existe esta vista, si `Gimnasio.logo` ya tiene `.url`: en producción el
bucket de R2 es privado y `AWS_QUERYSTRING_AUTH` firma cada URL con una
expiración de 1 h, **recalculada en cada render**. Medido en producción el
2026-09-08: dos cargas seguidas de la misma página devolvieron
`X-Amz-Signature=0fa66438…` y `…=a09ff825…`. Con la URL cambiando siempre, el
navegador nunca puede reusar su copia, así que se bajaba el logo entero (62 KB,
~458 ms) **en cada navegación** -- y el `<img>` vive en el topbar de
`base.html`, o sea en todas las páginas autenticadas, con `hx-boost` global
reemplazando el `<body>` en cada click.

Es exactamente el problema que `notificaciones/icons.py` ya había resuelto para
el ícono de la PWA (una URL firmada tampoco sirve en un manifest), y esta vista
reusa esa solución: URL versionada por `gimnasio.modificado` + respuesta
`immutable`. Cambiar el logo cambia la URL, así que una URL nueva nunca choca
con un cache viejo y no hace falta invalidar nada.
"""

from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from tenants.models import Gimnasio, Perfil


def _logo(color="#123456", formato="PNG", nombre="logo.png"):
    buffer = BytesIO()
    Image.new("RGB", (400, 400), color).save(buffer, format=formato)
    return SimpleUploadedFile(nombre, buffer.getvalue())


class LogoUrlVersionadaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Vida Plena", slug="vida-plena", logo=_logo()
        )

    def test_la_url_apunta_a_la_vista_propia_y_no_al_storage(self):
        url = self.gimnasio.logo_url_cacheable
        self.assertIn(reverse("logo_gimnasio", args=[self.gimnasio.slug]), url)

    def test_la_url_lleva_la_version_del_gimnasio(self):
        """Sin `?v=`, la respuesta no podría ser `immutable`: cambiar el logo
        no cambiaría la URL y el navegador seguiría mostrando el viejo."""
        self.assertIn("?v=", self.gimnasio.logo_url_cacheable)

    def test_la_url_cambia_al_guardar_el_gimnasio_de_nuevo(self):
        """`modificado` es `auto_now`, así que subir un logo nuevo (o cualquier
        edición) invalida la URL sola. Es el mecanismo que hace segura la
        respuesta `immutable`.

        El reloj se congela un minuto adelante en vez de confiar en que los dos
        `save()` caigan en milisegundos distintos: `version_media` tiene
        resolución de milisegundos y en la suite los dos guardados caen seguido
        en el mismo. Sin esto el test fallaba 2 de cada 8 corridas -- flaky, y
        por una razón que no tiene nada que ver con lo que prueba."""
        antes = self.gimnasio.logo_url_cacheable
        un_minuto_despues = self.gimnasio.modificado + timedelta(minutes=1)
        with patch("django.utils.timezone.now", return_value=un_minuto_despues):
            self.gimnasio.nombre = "Vida Plena Centro"
            self.gimnasio.save()
        self.assertNotEqual(antes, self.gimnasio.logo_url_cacheable)

    def test_un_gimnasio_sin_logo_no_expone_ninguna_url(self):
        """Los templates preguntan `{% if gimnasio.logo %}` antes de usarla,
        pero devolver una URL a un archivo inexistente sería una imagen rota."""
        sin_logo = Gimnasio.objects.create(nombre="Sin Logo", slug="sin-logo")
        self.assertEqual(sin_logo.logo_url_cacheable, "")


class LogoGimnasioViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Vida Plena", slug="vida-plena", logo=_logo()
        )
        self.url = reverse("logo_gimnasio", args=[self.gimnasio.slug])

    def test_sirve_los_bytes_de_la_imagen(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta["Content-Type"].startswith("image/"))
        self.assertGreater(len(respuesta.content), 0)

    def test_la_respuesta_es_cacheable_para_siempre(self):
        """Es el punto de todo el cambio: sin esto seguimos pagando la
        descarga en cada navegación."""
        respuesta = self.client.get(self.url)
        self.assertIn("immutable", respuesta["Cache-Control"])
        self.assertIn("max-age=31536000", respuesta["Cache-Control"])

    def test_es_publica_porque_el_login_muestra_el_logo_antes_de_entrar(self):
        """`g/<slug>/login/` y la landing pintan el logo sin sesión."""
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)

    def test_un_gimnasio_sin_logo_da_404(self):
        Gimnasio.objects.create(nombre="Sin Logo", slug="sin-logo")
        respuesta = self.client.get(reverse("logo_gimnasio", args=["sin-logo"]))
        self.assertEqual(respuesta.status_code, 404)

    def test_un_slug_inexistente_da_404(self):
        respuesta = self.client.get(reverse("logo_gimnasio", args=["no-existe"]))
        self.assertEqual(respuesta.status_code, 404)

    def test_un_gimnasio_inactivo_da_404(self):
        """Mismo criterio que la landing y el login por slug: un gimnasio
        desactivado y un slug inexistente son indistinguibles desde afuera."""
        self.gimnasio.activo = False
        self.gimnasio.save()
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 404)


class LogoEnLasPantallasTests(TestCase):
    """Las tres pantallas que muestran el logo tienen que usar la URL
    versionada. El topbar es la que importa: está en todas las páginas."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Vida Plena", slug="vida-plena", logo=_logo()
        )
        self.ruta_logo = reverse("logo_gimnasio", args=[self.gimnasio.slug])

    def _staff(self):
        usuario = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        return usuario

    def test_el_topbar_usa_la_url_versionada(self):
        self.client.force_login(self._staff())
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn(f'{self.ruta_logo}?v=', html)

    def test_la_landing_usa_la_url_versionada(self):
        html = self.client.get(
            reverse("landing_gimnasio", args=[self.gimnasio.slug])
        ).content.decode()
        self.assertIn(f'{self.ruta_logo}?v=', html)

    def test_el_login_por_gimnasio_usa_la_url_versionada(self):
        html = self.client.get(
            reverse("login_gimnasio", args=[self.gimnasio.slug])
        ).content.decode()
        self.assertIn(f'{self.ruta_logo}?v=', html)
