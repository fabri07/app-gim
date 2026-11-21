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
