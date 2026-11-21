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
