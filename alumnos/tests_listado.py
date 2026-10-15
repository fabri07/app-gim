"""Tests del listado de alumnos: buscador, filtro «Sin fecha de inicio»,
columna «Inicio del ciclo», aviso de activos sin fecha y barra de selección."""

import datetime
import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class ListadoBase(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.otro = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave12345")
        self.url = reverse("alumnos:listado")

    def crear(self, nombre, apellido, gimnasio=None, **kw):
        kw.setdefault("fecha_inicio_ciclo", datetime.date(2026, 9, 1))
        fecha = kw.pop("fecha_inicio_ciclo")
        alumno = Alumno.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, apellido=apellido, **kw
        )
        # El pre_save del alta ancla en hoy si viene None (así nace un alumno
        # del staff); un importado nace sin ancla, y se simula con update().
        Alumno.objects.filter(pk=alumno.pk).update(fecha_inicio_ciclo=fecha)
        alumno.refresh_from_db()
        return alumno

    def nombres(self, response):
        return {a.nombre for a in response.context["alumnos"]}


class BuscadorTests(ListadoBase):
    def test_busca_por_varias_palabras_en_nombre_y_apellido(self):
        self.crear("Juan", "Pérez")
        self.crear("Juan", "Gómez")
        self.crear("Ana", "Pérez")
        r = self.client.get(self.url, {"q": "juan pérez"})
        self.assertEqual(self.nombres(r), {"Juan"})
        self.assertEqual(len(r.context["alumnos"]), 1)

    def test_busca_por_email(self):
        self.crear("Juan", "Pérez", email="juan@ejemplo.com")
        self.crear("Ana", "Gómez", email="ana@otro.com")
        r = self.client.get(self.url, {"q": "ejemplo.com"})
        self.assertEqual(self.nombres(r), {"Juan"})

    def test_busca_por_telefono(self):
        self.crear("Juan", "Pérez", telefono="+5491155550000")
        self.crear("Ana", "Gómez", telefono="+5491166661111")
        r = self.client.get(self.url, {"q": "5555"})
        self.assertEqual(self.nombres(r), {"Juan"})

    def test_no_ve_alumnos_de_otro_gimnasio(self):
        self.crear("Juan", "Pérez", gimnasio=self.otro)
        r = self.client.get(self.url, {"q": "juan"})
        self.assertEqual(len(r.context["alumnos"]), 0)

    def test_el_form_mantiene_q(self):
        r = self.client.get(self.url, {"q": "juan pérez"})
        self.assertContains(r, 'name="q"')
        self.assertContains(r, 'value="juan pérez"')


class SinFechaTests(ListadoBase):
    def test_sin_fecha_filtra_y_se_combina_con_estado(self):
        self.crear("A", "Uno", fecha_inicio_ciclo=None)
        self.crear("B", "Dos", fecha_inicio_ciclo=None, estado=Alumno.Estado.INACTIVO)
        self.crear("C", "Tres")
        r = self.client.get(self.url, {"sin_fecha": "1"})
        self.assertEqual(self.nombres(r), {"A", "B"})
        r = self.client.get(self.url, {"sin_fecha": "1", "estado": "activo"})
        self.assertEqual(self.nombres(r), {"A"})

    def test_columna_inicio_del_ciclo(self):
        self.crear("A", "Uno", fecha_inicio_ciclo=datetime.date(2026, 9, 1))
        self.crear("B", "Dos", fecha_inicio_ciclo=None)
        r = self.client.get(self.url)
        self.assertContains(r, "Inicio del ciclo")
        self.assertContains(r, "01/09/2026")
        self.assertContains(r, "Sin fecha de inicio")

    def test_aviso_cuenta_solo_activos_de_todo_el_gimnasio(self):
        self.crear("A", "Uno", fecha_inicio_ciclo=None)
        self.crear("B", "Dos", fecha_inicio_ciclo=None)
        self.crear("I", "Inactivo", fecha_inicio_ciclo=None, estado=Alumno.Estado.INACTIVO)
        self.crear("X", "Ajeno", fecha_inicio_ciclo=None, gimnasio=self.otro)
        # El filtro de la pantalla no cambia el conteo.
        r = self.client.get(self.url, {"q": "zzz"})
        self.assertEqual(r.context["activos_sin_inicio"], 2)
        self.assertContains(r, "2 alumnos activos sin fecha de inicio")
        self.assertContains(r, "sin_fecha=1&amp;estado=activo")

    def test_sin_aviso_si_no_hay_activos_sin_fecha(self):
        self.crear("A", "Uno")
        r = self.client.get(self.url)
        self.assertEqual(r.context["activos_sin_inicio"], 0)
        self.assertNotContains(r, "activos sin fecha de inicio")

    def test_filtros_qs_solo_con_claves_permitidas(self):
        r = self.client.get(self.url, {"q": "a b", "estado": "activo", "x": "1"})
        qs = r.context["filtros_qs"]
        self.assertIn("q=a+b", qs)
        self.assertIn("estado=activo", qs)
        self.assertNotIn("x=", qs)


class BarraDeSeleccionTests(ListadoBase):
    def test_markup_de_la_barra(self):
        a = self.crear("A", "Uno")
        r = self.client.get(self.url)
        html = r.content.decode()
        self.assertIn(reverse("alumnos:lote"), html)
        self.assertRegex(html, r'class="lote-acciones"[^>]*\bhidden\b')
        self.assertIn('id="lote-todos"', html)
        self.assertIn('id="lote-conteo"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('name="ids"', html)
        self.assertIn('name="filtros"', html)
        for accion in ("fijar_fecha", "dar_de_baja", "reactivar", "generar_codigos"):
            self.assertIn(f'name="accion" value="{accion}"', html)
        checks = re.findall(r"<input[^>]*js-lote[^>]*>", html)
        self.assertEqual(len(checks), 1)
        self.assertNotIn("name=", checks[0])
        self.assertIn(f'data-id="{a.pk}"', checks[0])

    def test_ids_se_sincronizan_dentro_de_actualizar(self):
        """`ids` no puede depender del orden de los listeners de submit: lo
        escribe `actualizar()`, que corre en cada cambio y al arrancar."""
        self.crear("A", "Uno")
        html = self.client.get(self.url).content.decode()
        cuerpo = html.split("function actualizar()")[1].split("checks.forEach")[0]
        self.assertIn("ids.value =", cuerpo)
        # El listener de submit ya no escribe `ids`.
        submit = html.split('addEventListener("submit"')[1]
        self.assertNotIn("ids.value", submit.split("barra.hidden")[0])

    def test_links_a_la_ficha_conservan_hx_boost_false(self):
        a = self.crear("A", "Uno")
        r = self.client.get(self.url)
        self.assertContains(
            r, f'<a hx-boost="false" href="{reverse("alumnos:detalle", args=[a.pk])}">'
        )

    def test_lote_redirige_al_listado(self):
        url = reverse("alumnos:lote")
        for metodo in (self.client.get, self.client.post):
            r = metodo(url)
            self.assertRedirects(r, self.url, fetch_redirect_response=False)

    def test_lote_exige_staff(self):
        self.client.logout()
        r = self.client.post(reverse("alumnos:lote"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r.url)


class EscalaTests(ListadoBase):
    def _queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url, {"q": "al"})
        return len(ctx)

    def test_mismas_queries_con_5_y_con_50_alumnos(self):
        for i in range(5):
            self.crear(f"Al{i}", "X", email=f"a{i}@x.com")
        self._queries()  # calienta cachés (ContentType, etc.) que no dependen del tamaño
        chico = self._queries()
        for i in range(5, 50):
            self.crear(f"Al{i}", "X", email=f"a{i}@x.com", fecha_inicio_ciclo=None)
        self.assertEqual(self._queries(), chico)
