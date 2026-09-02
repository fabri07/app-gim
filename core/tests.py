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
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
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


class BorradoTests(TestCase):
    """`core.borrado`: qué impide borrar y qué se lleva puesto.

    Decisión de producto (2026-09-02): el botón Eliminar borra de verdad lo
    que no tiene historial (cargado por error, pruebas), y cuando NO se puede
    lo explica en castellano en vez de tirar un `ProtectedError`. Nunca borra
    historial de cobros en cascada.
    """

    def setUp(self):
        from ejercicios.models import CategoriaEjercicio, Ejercicio
        from rutinas.models import RutinaPlantilla, RutinaPlantillaItem

        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Paz"
        )
        self.categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Core"
        )
        self.ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Plancha", categoria=self.categoria
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Plan", objetivo="fuerza",
            dias_por_semana=3,
        )
        self._RutinaPlantillaItem = RutinaPlantillaItem

    def test_un_alumno_sin_historial_se_puede_borrar(self):
        from core.borrado import bloqueos_de_borrado

        self.assertEqual(bloqueos_de_borrado(self.alumno), [])

    def test_un_alumno_con_pagos_queda_bloqueado(self):
        from core.borrado import bloqueos_de_borrado, frase
        from pagos.models import PagoMensual

        PagoMensual.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, mes=1, anio=2026, monto=100
        )

        bloqueos = bloqueos_de_borrado(self.alumno)
        self.assertEqual(len(bloqueos), 1)
        self.assertEqual(bloqueos[0][1], 1)
        self.assertIn("pago", frase(bloqueos))

    def test_un_ejercicio_usado_en_una_plantilla_queda_bloqueado(self):
        from core.borrado import bloqueos_de_borrado

        self.assertEqual(bloqueos_de_borrado(self.ejercicio), [])
        self._RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.ejercicio,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        self.assertNotEqual(bloqueos_de_borrado(self.ejercicio), [])

    def test_una_plantilla_con_ejercicios_se_puede_borrar_y_los_arrastra(self):
        """`RutinaAsignada` es un snapshot SIN FK viva a la plantilla, así que
        borrarla no toca ninguna rutina ya entregada a un alumno. Lo único
        que cuelga son sus propios items, en CASCADE."""
        from core.borrado import arrastres_de_borrado, bloqueos_de_borrado

        self._RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.ejercicio,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        self.assertEqual(bloqueos_de_borrado(self.plantilla), [])
        self.assertEqual(
            [cantidad for _, cantidad in arrastres_de_borrado(self.plantilla)], [1]
        )

    def test_borrar_una_plantilla_no_toca_la_rutina_ya_asignada_del_alumno(self):
        """La garantía que hace seguro este botón: si borrar la plantilla le
        sacara la rutina al alumno, sería un desastre silencioso."""
        from rutinas.models import RutinaAsignada

        self._RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.ejercicio,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        cuantos = asignada.items.count()

        self.plantilla.delete()

        asignada.refresh_from_db()
        self.assertEqual(asignada.items.count(), cuantos)

    def test_frase_arma_una_enumeracion_legible(self):
        from core.borrado import frase

        self.assertEqual(frase([]), "")
        self.assertEqual(frase([("pagos", 8)]), "8 pagos")
        self.assertEqual(
            frase([("pagos", 8), ("rutinas", 2)]), "8 pagos y 2 rutinas"
        )
        self.assertEqual(
            frase([("pagos", 8), ("rutinas", 2), ("reservas", 3)]),
            "8 pagos, 2 rutinas y 3 reservas",
        )


class BorrarConExplicacionViewTests(TestCase):
    """Las tres pantallas de borrado (plantilla, ejercicio, alumno) sobre
    `core.views.BorrarConExplicacionView`."""

    def setUp(self):
        from ejercicios.models import CategoriaEjercicio, Ejercicio
        from rutinas.models import RutinaPlantilla

        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        usuario = User.objects.create_user("staff", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff", password="clave-123456")

        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Paz"
        )
        self.categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Core"
        )
        self.ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Plancha", categoria=self.categoria
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Plan", objetivo="fuerza",
            dias_por_semana=3,
        )
        self.alumno_ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Otro", apellido="Gimnasio"
        )

    def test_borra_una_plantilla(self):
        from rutinas.models import RutinaPlantilla

        response = self.client.post(
            reverse("rutinas:plantilla_eliminar", args=[self.plantilla.pk])
        )
        self.assertRedirects(response, reverse("rutinas:plantilla_listado"))
        self.assertFalse(RutinaPlantilla.objects.filter(pk=self.plantilla.pk).exists())

    def test_borra_un_alumno_sin_historial(self):
        response = self.client.post(
            reverse("alumnos:eliminar", args=[self.alumno.pk])
        )
        self.assertRedirects(response, reverse("alumnos:listado"))
        self.assertFalse(Alumno.objects.filter(pk=self.alumno.pk).exists())

    def test_un_alumno_con_pagos_no_se_borra_y_se_explica(self):
        """El caso que hace segura esta feature: los pagos son el registro de
        lo que el gimnasio facturó. El botón no puede destruirlo."""
        from pagos.models import PagoMensual

        PagoMensual.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, mes=1, anio=2026, monto=100
        )
        url = reverse("alumnos:eliminar", args=[self.alumno.pk])

        respuesta_get = self.client.get(url)
        self.assertContains(respuesta_get, "No se puede eliminar")
        self.assertContains(respuesta_get, "Inactivar alumno")
        # Sin botón de borrar: la pantalla no ofrece una acción imposible.
        self.assertNotContains(respuesta_get, "Sí, eliminar")

        # Y aunque se postee igual (el GET puede haber quedado viejo: el cron
        # de pagos genera filas solo), el alumno sigue ahí.
        self.client.post(url)
        self.assertTrue(Alumno.objects.filter(pk=self.alumno.pk).exists())

    def test_un_ejercicio_usado_en_una_plantilla_no_se_borra_y_se_explica(self):
        from ejercicios.models import Ejercicio
        from rutinas.models import RutinaPlantillaItem

        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.ejercicio,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        url = reverse("ejercicios:eliminar", args=[self.ejercicio.pk])

        self.assertContains(self.client.get(url), "No se puede eliminar")
        self.client.post(url)
        self.assertTrue(Ejercicio.objects.filter(pk=self.ejercicio.pk).exists())

    def test_la_confirmacion_avisa_lo_que_se_arrastra(self):
        """Borrar una plantilla se lleva sus ejercicios; la pantalla lo dice
        antes, no después."""
        from rutinas.models import RutinaPlantillaItem

        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.ejercicio,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        response = self.client.get(
            reverse("rutinas:plantilla_eliminar", args=[self.plantilla.pk])
        )
        self.assertContains(response, "Se van a eliminar también")

    def test_no_se_puede_borrar_un_registro_de_otro_gimnasio(self):
        """El aislamiento por tenant tiene que valer también para el borrado:
        sin esto, adivinar un pk borraría datos de otro gimnasio."""
        url = reverse("alumnos:eliminar", args=[self.alumno_ajeno.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(Alumno.objects.filter(pk=self.alumno_ajeno.pk).exists())

    def test_las_tres_pantallas_ofrecen_el_boton_de_eliminar(self):
        """Sin el link, la feature existe pero nadie llega. Cada pantalla es
        el único camino a su borrado."""
        casos = [
            (reverse("alumnos:detalle", args=[self.alumno.pk]),
             reverse("alumnos:eliminar", args=[self.alumno.pk])),
            (reverse("rutinas:plantilla_detalle", args=[self.plantilla.pk]),
             reverse("rutinas:plantilla_eliminar", args=[self.plantilla.pk])),
            (reverse("ejercicios:listado"),
             reverse("ejercicios:eliminar", args=[self.ejercicio.pk])),
        ]
        for pantalla, destino in casos:
            with self.subTest(pantalla=pantalla):
                self.assertContains(self.client.get(pantalla), destino)

    def test_un_alumno_no_puede_entrar_a_las_pantallas_de_borrado(self):
        alumno_user = User.objects.create_user("alu", password="clave-123456")
        Perfil.objects.create(
            usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alu", password="clave-123456")
        for url in (
            reverse("alumnos:eliminar", args=[self.alumno.pk]),
            reverse("ejercicios:eliminar", args=[self.ejercicio.pk]),
            reverse("rutinas:plantilla_eliminar", args=[self.plantilla.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)
