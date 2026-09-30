"""`manage.py reconciliar_cobros`. Sin red: se parchea el servicio."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class ReconciliarCobrosTests(SimpleTestCase):
    def test_llama_al_servicio_con_el_limite_e_informa_cuantos(self):
        out = StringIO()
        with patch("suscripcion.services.integracion_activa", return_value=True), patch(
            "suscripcion.services.reconciliar_pendientes", return_value=3
        ) as recon:
            call_command("reconciliar_cobros", "--limite", "7", stdout=out)
        recon.assert_called_once_with(limite=7)
        self.assertIn("3", out.getvalue())

    def test_limite_por_defecto_es_50(self):
        with patch("suscripcion.services.integracion_activa", return_value=True), patch(
            "suscripcion.services.reconciliar_pendientes", return_value=0
        ) as recon:
            call_command("reconciliar_cobros", stdout=StringIO())
        recon.assert_called_once_with(limite=50)

    def test_sin_integracion_falla_y_no_consulta_nada(self):
        with patch("suscripcion.services.integracion_activa", return_value=False), patch(
            "suscripcion.services.reconciliar_pendientes"
        ) as recon:
            with self.assertRaises(CommandError):
                call_command("reconciliar_cobros", stdout=StringIO())
        recon.assert_not_called()
