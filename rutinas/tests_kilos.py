from decimal import Decimal

from django.test import SimpleTestCase

from rutinas.kilos import MAXIMO_KILOS, parsear_kilos


class ParsearKilosTests(SimpleTestCase):
    def test_acepta_y_devuelve_el_valor(self):
        casos = [
            ("20", "20"),
            ("20.0", "20"),
            ("20,5", "20.5"),
            ("20 kg", "20"),
            ("20KG", "20"),
            (" 20kg ", "20"),
            ("15kg c/u", "15"),
            ("10-12kg", "12"),
            ("10 - 12 kg", "12"),
            ("0,5kg", "0.5"),
        ]
        for texto, esperado in casos:
            with self.subTest(texto=texto):
                resultado = parsear_kilos(texto)
                self.assertIsInstance(resultado, Decimal)
                self.assertEqual(resultado, Decimal(esperado))

    def test_descarta_lo_que_no_es_un_peso(self):
        casos = [
            "", None, "corporal", "60s", "2 min", "50%", "+10kg", "0", "0kg",
            "2000", "20 libras", "RPE 8", "20kg 30kg",
        ]
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertIsNone(parsear_kilos(texto))

    def test_el_maximo_es_inclusivo(self):
        self.assertEqual(parsear_kilos("500"), MAXIMO_KILOS)
        self.assertIsNone(parsear_kilos("500,5"))
