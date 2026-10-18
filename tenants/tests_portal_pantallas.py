"""Otras pantallas del alumno adaptadas a la compu: comprobante de cuota,
`mis_turnos`, la política de privacidad y el medio de cobro compartido."""

import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pagos.models import Cuota, MedioCobro
from pagos.testing import crear_cuota
from tenants.models import Gimnasio
from tenants.tests_nav_alumno import _alumno, _gimnasio
from turnos.models import Reserva


COLUMNA = '\n    <div>\n    <div class="tarjeta">'


def _html(respuesta):
    return respuesta.content.decode()


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.alumno, self.usuario = _alumno(self.gimnasio)
        self.client.force_login(self.usuario)

    def _cuota(self, monto=0):
        return crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=2),
            estado=Cuota.Estado.PENDIENTE, monto=monto,
        )

    def _medio(self, gimnasio=None, **extra):
        datos = {"alias": "mi.alias.gym", "titular": "Juan Pérez", "entidad": "Banco X"}
        datos.update(extra)
        return MedioCobro.objects.create(gimnasio=gimnasio or self.gimnasio, **datos)


class MediosCobroCompartidosTests(_Base):
    def test_el_inicio_sigue_mostrando_los_medios_y_el_boton_copiar(self):
        self._cuota()
        self._medio()
        html = _html(self.client.get(reverse("home")))
        self.assertIn("Podés transferir a:", html)
        self.assertIn("<strong>mi.alias.gym</strong>", html)
        self.assertIn("· Juan Pérez", html)
        self.assertIn('data-copiar-alias="mi.alias.gym"', html)

    def test_el_inicio_sin_medios_muestra_el_texto_de_respaldo(self):
        self._cuota()
        html = _html(self.client.get(reverse("home")))
        self.assertIn("El gimnasio todavía no cargó un alias", html)


class ComprobanteAlumnoTests(_Base):
    def _get(self, cuota):
        return self.client.get(reverse("pagos:comprobante_subir", args=[cuota.pk]))

    def test_muestra_alias_y_titular(self):
        self._medio()
        html = _html(self._get(self._cuota()))
        self.assertIn("mi.alias.gym", html)
        self.assertIn("Juan Pérez", html)
        self.assertIn("boton-copiar", html)

    def test_sin_medios_muestra_el_respaldo_con_el_contacto(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(contacto="11-5555-0000")
        html = _html(self._get(self._cuota()))
        self.assertIn("El gimnasio todavía no cargó un alias", html)
        self.assertIn("Escribile a 11-5555-0000", html)

    def test_monto_cero_no_se_muestra_y_mayor_a_cero_si(self):
        self.assertNotIn("Monto:", _html(self._get(self._cuota(monto=0))))
        otra = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=40),
            estado=Cuota.Estado.VENCIDO, monto=Decimal("15000"),
        )
        self.assertIn("Monto:", _html(self._get(otra)))

    def test_el_form_con_error_sigue_mostrando_los_medios(self):
        self._medio()
        cuota = self._cuota()
        respuesta = self.client.post(
            reverse("pagos:comprobante_subir", args=[cuota.pk]),
            {"comprobante": SimpleUploadedFile("x.exe", b"x")},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("mi.alias.gym", _html(respuesta))

    def test_no_muestra_medios_inactivos_ni_de_otro_gimnasio(self):
        self._medio(alias="inactivo.alias", activo=False)
        otro = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self._medio(gimnasio=otro, alias="ajeno.alias")
        html = _html(self._get(self._cuota()))
        self.assertNotIn("inactivo.alias", html)
        self.assertNotIn("ajeno.alias", html)

    def test_contenedor_y_orden_del_form(self):
        html = _html(self._get(self._cuota()))
        self.assertIn('<main class="contenido--alumno">', html)
        self.assertEqual(html.count('class="tarjeta"'), 1)
        lateral = html.index('class="con-lateral"')
        self.assertLess(html.index("<h1>Subir comprobante"), lateral)
        self.assertLess(lateral, html.index('<form method="post" enctype'))
        self.assertIn('hx-boost="false"', html)
        self.assertIn("multipart/form-data", html)


class MisTurnosColumnasTests(_Base):
    def _html(self, calendar=False, reservas=False):
        if reservas:
            Reserva.objects.create(
                gimnasio=self.gimnasio, alumno=self.alumno,
                fecha=timezone.localdate() + timedelta(days=1),
                hora_inicio="10:00",
            )
        with mock.patch(
            "calendario.services.integracion_activa", return_value=calendar
        ):
            return _html(self.client.get(reverse("turnos:mis_turnos")))

    def _bloque(self, html):
        m = re.search(r'<div class="columnas">(.*?)\n  </div>\n', html, re.S)
        self.assertIsNotNone(m, "no hay .columnas")
        return m.group(1)

    def test_las_dos_tarjetas_van_en_dos_columnas(self):
        html = self._html(calendar=True, reservas=True)
        self.assertEqual(html.count('class="columnas"'), 1)
        bloque = self._bloque(html)
        self.assertIn("Google Calendar", bloque)
        self.assertIn("Mis reservas", bloque)
        self.assertEqual(bloque.count('<div class="tarjeta">'), 2)
        self.assertEqual(bloque.count(COLUMNA), 2)

    def test_con_solo_reservas_hay_una_columna(self):
        solo_reservas = self._html(reservas=True)
        self.assertEqual(self._bloque(solo_reservas).count(COLUMNA), 1)
        self.assertNotIn("Google Calendar</h2>", solo_reservas)

    def test_con_solo_calendar_hay_una_columna(self):
        solo_calendar = self._html(calendar=True)
        self.assertEqual(self._bloque(solo_calendar).count(COLUMNA), 1)
        self.assertNotIn("Mis reservas", solo_calendar)

    def test_sin_ninguna_no_hay_columnas(self):
        self.assertNotIn('class="columnas"', self._html())


class PrivacidadLecturaTests(TestCase):
    def test_usa_el_contenedor_de_lectura(self):
        html = _html(self.client.get(reverse("politica_privacidad")))
        self.assertIn('<main class="contenido--lectura">', html)
