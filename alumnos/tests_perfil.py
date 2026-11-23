"""Pieza 1 de reconocimientos: foto del alumno y su vista de servicio."""

import io

from django import forms
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from alumnos.fotos import guardar_foto, quitar_foto
from alumnos.models import Alumno
from tenants.models import Gimnasio
from tenants.terminos import VERSION_TERMINOS
from tenants.tests_terminos import _alumno, _gimnasio, _staff


def _imagen(lado=400, formato="PNG", nombre="foto.png"):
    salida = io.BytesIO()
    Image.new("RGB", (lado, lado), (200, 30, 30)).save(salida, formato)
    return SimpleUploadedFile(nombre, salida.getvalue(), content_type="image/png")


def _otro_gimnasio(**extra):
    return Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym", **extra)


def _archivos_de_fotos():
    """El storage en memoria se comparte entre tests: se compara antes/después."""
    try:
        return set(default_storage.listdir("alumnos/fotos/")[1])
    except FileNotFoundError:
        return set()


def _con_terminos(alumno):
    """Con la bandera prendida, un alumno sin aceptar ve el cartel de términos
    en cualquier ruta: acá interesa la foto, no el cartel."""
    perfil = alumno.perfil
    perfil.terminos_version = VERSION_TERMINOS
    perfil.terminos_aceptados_en = timezone.now()
    perfil.save(update_fields=["terminos_version", "terminos_aceptados_en"])


class FotoAlumnoTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio(reconocimientos_habilitado=True)
        self.alumno, self.usuario = _alumno(self.gimnasio)
        _con_terminos(self.alumno)

    def _con_foto(self, alumno=None):
        alumno = alumno or self.alumno
        guardar_foto(alumno, _imagen())
        alumno.refresh_from_db()
        return alumno

    def _url(self, alumno=None):
        return reverse("alumnos:foto_alumno", args=[(alumno or self.alumno).pk])

    def _assert_404_no_store(self, respuesta):
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta["Cache-Control"], "no-store")

    # --- guardar / quitar ------------------------------------------------

    def test_guardar_foto_recodifica_a_jpeg_con_nombre_uuid(self):
        self._con_foto()

        self.assertTrue(self.alumno.foto.name.startswith("alumnos/fotos/"))
        self.assertTrue(self.alumno.foto.name.endswith(".jpg"))
        self.assertNotIn("foto", self.alumno.foto.name.removeprefix("alumnos/fotos/"))
        self.assertTrue(default_storage.exists(self.alumno.foto.name))
        with default_storage.open(self.alumno.foto.name, "rb") as archivo:
            self.assertEqual(Image.open(archivo).format, "JPEG")

    def test_rechaza_archivo_que_no_es_imagen_y_muy_chica_y_muy_pesada(self):
        pesado = SimpleUploadedFile(
            "pesada.png", b"\x89PNG" + b"0" * (8 * 1024 * 1024 + 1),
            content_type="image/png",
        )
        # El mensaje de cada caso fija QUÉ chequeo lo rechazó: sin él, el
        # archivo pesado (bytes basura) se rechazaría igual por no ser imagen
        # y el test pasaría sin el chequeo de tamaño.
        casos = {
            "texto": (
                SimpleUploadedFile("nota.png", b"esto no es una imagen"),
                "El archivo no es una imagen válida.",
            ),
            "chica": (
                _imagen(lado=100),
                "La foto mide 100×100px y ningún lado puede ser menor a 300px.",
            ),
            "pesada": (pesado, "La foto pesa más de 8 MB."),
        }
        antes = _archivos_de_fotos()
        for caso, (archivo, mensaje) in casos.items():
            with self.subTest(caso):
                with self.assertRaises(forms.ValidationError) as error:
                    guardar_foto(self.alumno, archivo)
                self.assertEqual(error.exception.messages, [mensaje])
                self.alumno.refresh_from_db()
                self.assertEqual(self.alumno.foto.name, "")
                self.assertEqual(_archivos_de_fotos(), antes)

    def test_reemplazar_borra_la_anterior(self):
        self._con_foto()
        anterior = self.alumno.foto.name

        with self.captureOnCommitCallbacks(execute=True):
            guardar_foto(self.alumno, _imagen())

        self.alumno.refresh_from_db()
        self.assertNotEqual(self.alumno.foto.name, anterior)
        self.assertTrue(default_storage.exists(self.alumno.foto.name))
        self.assertFalse(default_storage.exists(anterior))

    def test_quitar_foto_borra_el_archivo(self):
        self._con_foto()
        nombre = self.alumno.foto.name

        with self.captureOnCommitCallbacks(execute=True):
            quitar_foto(self.alumno)

        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.foto.name, "")
        self.assertFalse(default_storage.exists(nombre))

    def test_borrar_el_alumno_borra_el_archivo(self):
        self._con_foto()
        nombre = self.alumno.foto.name

        with self.captureOnCommitCallbacks(execute=True):
            self.alumno.delete()

        self.assertFalse(default_storage.exists(nombre))

    def test_borrar_el_alumno_sin_archivo_en_el_storage_no_falla(self):
        self._con_foto()
        default_storage.delete(self.alumno.foto.name)

        pk = self.alumno.pk

        with self.captureOnCommitCallbacks(execute=True):
            self.alumno.delete()

        self.assertFalse(Alumno.objects.filter(pk=pk).exists())

    def test_si_la_transaccion_se_revierte_el_archivo_no_se_borra(self):
        self._con_foto()
        nombre = self.alumno.foto.name
        pk = self.alumno.pk

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.alumno.delete()
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass

        self.assertEqual(callbacks, [])
        self.assertTrue(Alumno.objects.filter(pk=pk).exists())
        self.assertTrue(default_storage.exists(nombre))

    def test_foto_url_vacia_sin_foto_y_versionada_con_foto(self):
        self.assertEqual(self.alumno.foto_url, "")

        self._con_foto()

        self.assertEqual(
            self.alumno.foto_url,
            f"{self._url()}?v={self.alumno.foto.name.rsplit('/', 1)[-1]}",
        )

    # --- vista -----------------------------------------------------------

    def test_staff_del_mismo_gimnasio_la_ve(self):
        self._con_foto()
        self.client.force_login(_staff(self.gimnasio))

        respuesta = self.client.get(self._url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "image/jpeg")
        self.assertEqual(respuesta["Cache-Control"], "private, max-age=3600")
        self.assertNotIn("immutable", respuesta["Cache-Control"])
        self.assertTrue(respuesta.content.startswith(b"\xff\xd8"))

    def test_staff_la_ve_aunque_la_bandera_este_apagada(self):
        self._con_foto()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            reconocimientos_habilitado=False
        )
        Alumno.objects.filter(pk=self.alumno.pk).update(aparece_en_ranking=False)
        self.client.force_login(_staff(self.gimnasio))

        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_el_alumno_ve_su_propia_foto_siempre(self):
        self._con_foto()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            reconocimientos_habilitado=False
        )
        Alumno.objects.filter(pk=self.alumno.pk).update(aparece_en_ranking=False)
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self._url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Cache-Control"], "private, max-age=3600")

    def _companero(self):
        companero, usuario = _alumno(self.gimnasio, username="companero")
        _con_terminos(companero)
        self.client.force_login(usuario)

    def test_un_companero_la_ve_con_bandera_y_ranking(self):
        self._con_foto()
        self._companero()

        respuesta = self.client.get(self._url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "image/jpeg")

    def test_la_foto_de_un_menor_no_la_ve_un_companero_pero_si_el_mismo_y_el_staff(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(
            fecha_nacimiento=timezone.localdate()
        )
        self._con_foto()
        self._companero()
        self._assert_404_no_store(self.client.get(self._url()))
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self._url()).status_code, 200)
        self.client.force_login(_staff(self.gimnasio))
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_la_foto_sin_fecha_de_nacimiento_no_la_ve_un_companero(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(fecha_nacimiento=None)
        self._con_foto()
        self._companero()
        self._assert_404_no_store(self.client.get(self._url()))

    def test_un_companero_no_la_ve_si_el_duenio_no_aparece_en_el_ranking(self):
        self._con_foto()
        Alumno.objects.filter(pk=self.alumno.pk).update(aparece_en_ranking=False)
        self._companero()

        self._assert_404_no_store(self.client.get(self._url()))

    def test_un_companero_no_la_ve_con_la_bandera_apagada(self):
        self._con_foto()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            reconocimientos_habilitado=False
        )
        self._companero()

        self._assert_404_no_store(self.client.get(self._url()))

    def test_alumno_de_otro_gimnasio_recibe_404_no_store(self):
        self._con_foto()
        otro = _otro_gimnasio(reconocimientos_habilitado=True)
        ajeno, usuario = _alumno(otro, username="ajeno")
        _con_terminos(ajeno)
        self.client.force_login(usuario)

        self._assert_404_no_store(self.client.get(self._url()))

    def test_staff_de_otro_gimnasio_recibe_404_no_store(self):
        self._con_foto()
        otro = _otro_gimnasio()
        self.client.force_login(_staff(otro, username="otro-duenio"))

        self._assert_404_no_store(self.client.get(self._url()))

    def test_usuario_sin_perfil_recibe_404_no_store(self):
        self._con_foto()
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_user("suelto", password="x"))

        self._assert_404_no_store(self.client.get(self._url()))

    def test_anonimo_va_al_login(self):
        self._con_foto()

        respuesta = self.client.get(self._url())

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def test_alumno_sin_foto_da_404_no_store(self):
        self.client.force_login(_staff(self.gimnasio))

        self._assert_404_no_store(self.client.get(self._url()))

    def test_pk_inexistente_da_404_no_store(self):
        self.client.force_login(_staff(self.gimnasio))

        self._assert_404_no_store(
            self.client.get(reverse("alumnos:foto_alumno", args=[999999]))
        )

    def test_archivo_que_ya_no_esta_en_el_storage_da_404_no_store(self):
        """La fila nombra un archivo que se borró del bucket (a mano, o un
        borrado que confirmó antes que la fila): 404 como cualquier foto que
        no existe, nunca 500."""
        self._con_foto()
        default_storage.delete(self.alumno.foto.name)
        self.client.force_login(_staff(self.gimnasio))

        self._assert_404_no_store(self.client.get(self._url()))

    def test_con_la_cuenta_congelada_responde_el_cartel_de_plataforma(self):
        self._con_foto()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
            estado_cuenta_desde=timezone.now(),
        )
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self._url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertNotEqual(respuesta["Content-Type"], "image/jpeg")


class MiPerfilTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio(reconocimientos_habilitado=True)
        self.alumno, self.usuario = _alumno(self.gimnasio)
        _con_terminos(self.alumno)
        self.client.force_login(self.usuario)
        self.url = reverse("alumnos:mi_perfil")

    def _con_foto(self):
        guardar_foto(self.alumno, _imagen())
        self.alumno.refresh_from_db()

    def test_get_muestra_el_formulario_con_el_estado_actual(self):
        self._con_foto()

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "alumnos/mi_perfil.html")
        self.assertContains(respuesta, f'src="{self.alumno.foto_url}"')
        self.assertContains(respuesta, 'alt="Tu foto actual"')
        self.assertContains(respuesta, "tu nombre y la inicial de tu apellido")
        self.assertContains(respuesta, 'accept="image/jpeg,image/png"')
        self.assertContains(respuesta, 'name="quitar_foto"')
        self.assertIs(respuesta.context["form"].initial["aparece_en_ranking"], True)
        self.assertContains(
            respuesta,
            '<input type="checkbox" name="aparece_en_ranking" '
            'aria-describedby="id_aparece_en_ranking_helptext" '
            'id="id_aparece_en_ranking" checked>',
            html=True,
        )

    def test_get_sin_foto_no_muestra_imagen_ni_el_quitar(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(aparece_en_ranking=False)

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'alt="Tu foto actual"')
        self.assertNotContains(respuesta, 'name="quitar_foto"')
        self.assertIs(respuesta.context["form"].initial["aparece_en_ranking"], False)

    def test_post_cambia_aparece_en_ranking_y_sube_foto(self):
        respuesta = self.client.post(self.url, {"foto": _imagen()})

        self.assertRedirects(respuesta, self.url, fetch_redirect_response=False)
        self.alumno.refresh_from_db()
        self.assertIs(self.alumno.aparece_en_ranking, False)
        self.assertTrue(self.alumno.foto.name.startswith("alumnos/fotos/"))
        self.assertTrue(default_storage.exists(self.alumno.foto.name))

    def test_post_sin_foto_conserva_la_actual(self):
        self._con_foto()
        anterior = self.alumno.foto.name

        respuesta = self.client.post(self.url, {"aparece_en_ranking": "on"})

        self.assertRedirects(respuesta, self.url, fetch_redirect_response=False)
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.foto.name, anterior)
        self.assertIs(self.alumno.aparece_en_ranking, True)

    def test_quitar_foto(self):
        self._con_foto()
        nombre = self.alumno.foto.name

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                self.url, {"quitar_foto": "on", "aparece_en_ranking": "on"}
            )

        self.assertRedirects(respuesta, self.url, fetch_redirect_response=False)
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.foto.name, "")
        self.assertFalse(default_storage.exists(nombre))

    def test_si_falla_la_segunda_escritura_la_foto_no_cambia(self):
        """La foto y `aparece_en_ranking` se guardan juntas o no se guarda
        ninguna: si la segunda escritura falla, la columna de la foto sigue
        apuntando a la anterior y el archivo anterior no se borra."""
        from unittest.mock import patch

        from django.db.models.query import QuerySet

        self._con_foto()
        anterior = self.alumno.foto.name
        update_original = QuerySet.update

        def update_que_falla_en_el_ranking(queryset, **campos):
            if "aparece_en_ranking" in campos:
                raise RuntimeError("falla simulada")
            return update_original(queryset, **campos)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with patch.object(
                QuerySet, "update", autospec=True,
                side_effect=update_que_falla_en_el_ranking,
            ):
                with self.assertRaises(RuntimeError):
                    self.client.post(
                        self.url, {"foto": _imagen(), "aparece_en_ranking": "on"}
                    )

        self.assertEqual(callbacks, [])
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.foto.name, anterior)
        self.assertTrue(default_storage.exists(anterior))

    def test_foto_invalida_responde_200_con_el_error_en_el_campo(self):
        self._con_foto()
        anterior = self.alumno.foto.name
        casos = {
            "texto": (
                SimpleUploadedFile("nota.jpg", b"esto no es una imagen"),
                "El archivo no es una imagen válida.",
            ),
            "chica": (
                _imagen(lado=100),
                "La foto mide 100×100px y ningún lado puede ser menor a 300px.",
            ),
        }
        for caso, (archivo, mensaje) in casos.items():
            with self.subTest(caso):
                respuesta = self.client.post(
                    self.url, {"foto": archivo, "aparece_en_ranking": "on"}
                )

                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(
                    respuesta, f'<p class="config-error">{mensaje}</p>', html=True
                )
                self.assertIn(mensaje, respuesta.context["form"].errors["foto"])
                self.alumno.refresh_from_db()
                self.assertEqual(self.alumno.foto.name, anterior)
                self.assertTrue(default_storage.exists(anterior))

    def test_perfil_sin_ficha_get_vacio_y_post_403(self):
        from django.contrib.auth.models import User

        from tenants.models import Perfil

        usuario = User.objects.create_user("sin-ficha", password="x")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO,
            terminos_version=VERSION_TERMINOS, terminos_aceptados_en=timezone.now(),
        )
        self.client.force_login(usuario)

        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'name="foto"')

        respuesta = self.client.post(self.url, {"aparece_en_ranking": "on"})
        self.assertEqual(respuesta.status_code, 403)

    def test_bandera_apagada_da_404(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            reconocimientos_habilitado=False
        )

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}).status_code, 404)
        self.alumno.refresh_from_db()
        self.assertIs(self.alumno.aparece_en_ranking, True)

    def test_staff_da_403(self):
        self.client.force_login(_staff(self.gimnasio))

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)

    def test_anonimo_va_al_login(self):
        self.client.logout()

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def _suplantar(self):
        self.client.force_login(_staff(self.gimnasio))
        respuesta = self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        self.assertEqual(respuesta.status_code, 302)

    def test_suplantando_el_post_no_cambia_nada(self):
        """La foto y `aparece_en_ranking` son decisiones del alumno: el staff
        que ve su cuenta no puede tomarlas por él."""
        self._con_foto()
        Alumno.objects.filter(pk=self.alumno.pk).update(aparece_en_ranking=False)
        anterior = self.alumno.foto.name
        archivos_antes = _archivos_de_fotos()
        self._suplantar()

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                self.url,
                {"foto": _imagen(), "quitar_foto": "on", "aparece_en_ranking": "on"},
            )

        self.assertEqual(respuesta.status_code, 403)
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.foto.name, anterior)
        self.assertIs(self.alumno.aparece_en_ranking, False)
        self.assertEqual(_archivos_de_fotos(), archivos_antes)
        self.assertTrue(default_storage.exists(anterior))

    def test_suplantando_el_get_muestra_la_pantalla_sin_guardar(self):
        self._suplantar()

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Estás viendo la cuenta del alumno")
        self.assertNotContains(respuesta, ">Guardar</button>")

    def test_el_formulario_sube_archivos_sin_hx_boost(self):
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertContains(respuesta, 'hx-boost="false"')
