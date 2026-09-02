"""
Tests de `TenantScopedMixin` (capa de vista) contra un `TenantOwnedModel` de
dominio real. Se agregan en Fase 1, apenas existe un modelo concreto para
ejercitarlos (`Alumno`) — ver REUSO.md, sección "Qué queda pendiente" y
`tenants/tests.py` para el motivo por el que no se escribieron en Fase 0.

Sigue el patrón de ~/gestor-pedidos/core/tests.py (Cliente -> Alumno,
Negocio -> Gimnasio): `RequestFactory` en vez de `self.client` porque lo que
se prueba es el mixin de vista de forma aislada, sin pasar por urls.py.
"""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.views.generic import ListView

from alumnos.models import Alumno
from core.mixins import TenantScopedMixin
from tenants.models import Gimnasio, Perfil


class _AlumnoListView(TenantScopedMixin, ListView):
    """Vista mínima de prueba; no se registra en urls."""

    model = Alumno


class TenantScopedMixinTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        self.user_a = User.objects.create_user("ana", password="x")
        Perfil.objects.create(usuario=self.user_a, gimnasio=self.gimnasio_a)
        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Alumno", apellido="A"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Alumno", apellido="B"
        )

    def _view_for(self, user):
        request = self.factory.get("/")
        request.user = user
        view = _AlumnoListView()
        view.setup(request)
        return view

    def test_gimnasio_resuelve_el_del_perfil(self):
        self.assertEqual(self._view_for(self.user_a).gimnasio, self.gimnasio_a)

    def test_get_queryset_scopea_al_gimnasio_del_usuario(self):
        view = self._view_for(self.user_a)
        self.assertEqual(list(view.get_queryset()), [self.alumno_a])
        self.assertNotIn(self.alumno_b, view.get_queryset())

    def test_usuario_sin_perfil_lanza_permission_denied(self):
        user = User.objects.create_user("sinperfil", password="x")
        with self.assertRaises(PermissionDenied):
            _ = self._view_for(user).gimnasio


class ErroresDeFormularioVisiblesTests(SimpleTestCase):
    """Ningún template puede renderizar un campo de formulario sin mostrar
    sus errores.

    Un formulario que rechaza sin que se vea que rechazó es indistinguible de
    uno que guarda: el usuario aprieta Guardar, la pantalla vuelve igual, se
    va, y el dato no está. Pasó TRES veces el mismo día (2026-09-02),
    reportado siempre como "no me guarda":

    - `tenants/gimnasio_form.html`: los tres links de redes no mostraban sus
      errores, y un `@usuario` invalidaba el form ENTERO sin decir nada.
    - `rutinas/item_form.html`: `{{ form.as_p }}` pintaba la `errorlist` de
      Django, que no tenía estilo -- salía en negro, del cuerpo de las ayudas
      grises, arriba de la etiqueta.
    - `alumnos/alumno_form.html`: 15 campos renderizados a mano, CERO bloques
      de error. La palabra "obligatorio" ni aparecía en el HTML.

    Barre TODOS los templates a propósito: el error es de forma de escribir
    un form en Django, no de una app en particular. Un campo se considera
    cubierto si el template muestra `form.<campo>.errors`, o si lo delega en
    `partials/campo_form.html` (que ya los muestra).
    """

    #: `{{ form.algo }}` -- no `{{ form.algo.label_tag }}` ni `.errors`, que
    #: no son el control en sí.
    _CAMPO = re.compile(r"\{\{\s*form\.(\w+)\s*\}\}")
    #: `{% include 'partials/campo_form.html' with campo=form.algo %}`
    _DELEGADO = re.compile(r"campo=form\.(\w+)")
    _NO_SON_CAMPOS = {
        "as_p", "as_table", "as_ul", "as_div", "media",
        "errors", "non_field_errors", "instance",
    }

    def test_todo_campo_renderizado_a_mano_muestra_sus_errores(self):
        raiz = Path(settings.BASE_DIR) / "templates"
        culpables = []
        for plantilla in raiz.rglob("*.html"):
            texto = plantilla.read_text()
            if "csrf_token" not in texto:
                continue
            renderizados = set(self._CAMPO.findall(texto)) - self._NO_SON_CAMPOS
            cubiertos = set(re.findall(r"form\.(\w+)\.errors", texto))
            cubiertos |= set(self._DELEGADO.findall(texto))
            for campo in sorted(renderizados - cubiertos):
                culpables.append(f"{plantilla.relative_to(raiz)}: form.{campo}")
        self.assertEqual(
            culpables,
            [],
            "Campos renderizados sin mostrar sus errores. El form rechaza y "
            "el usuario no se entera. Agregá "
            "`{% if form.<campo>.errors %}<p class=\"config-error\">...` o "
            "delegá en `partials/campo_form.html`.\n" + "\n".join(culpables),
        )

    def test_la_errorlist_de_django_tiene_estilo_propio(self):
        """La red de seguridad de los templates que usan `{{ form.as_p }}`
        (20 al escribir esto): ahí los errores los pinta Django con su
        `<ul class="errorlist">`, y sin una regla para esa clase salen en
        negro, indistinguibles de una ayuda. No alcanza con revisar los
        templates de a uno: mientras exista un solo `as_p`, esta clase tiene
        que estar definida."""
        fuente = (Path(settings.BASE_DIR) / "styles" / "input.css").read_text()
        # `.errorlist {` como REGLA, no como mención en un comentario.
        # `(?m)`: `assertRegex` usa `re.search` sin flags, así que sin esto
        # el `^` solo matchea el principio del archivo entero.
        self.assertRegex(
            fuente,
            r"(?m)^\s*\.errorlist[^{]*\{",
            "Falta la regla `.errorlist` en styles/input.css: los errores de "
            "todo template que use `{{ form.as_p }}` salen sin estilo.",
        )
        compilado = (
            Path(settings.BASE_DIR) / "static" / "css" / "app.css"
        ).read_text()
        self.assertIn(
            "errorlist",
            compilado,
            "`.errorlist` está en input.css pero no en app.css: falta correr "
            "`npm run build:css` y commitear el resultado.",
        )
