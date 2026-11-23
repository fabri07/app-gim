"""Mayoría de edad: los menores de 18 no participan en ningún ranking."""

from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from alumnos.edad import es_mayor_de_edad, fecha_corte_mayoria
from alumnos.models import Alumno


class EdadTests(SimpleTestCase):
    def test_corte_es_hoy_menos_18_anios(self):
        self.assertEqual(fecha_corte_mayoria(date(2026, 11, 21)), date(2008, 11, 21))

    def test_el_dia_del_cumple_18_es_mayor(self):
        self.assertTrue(es_mayor_de_edad(date(2008, 11, 21), date(2026, 11, 21)))

    def test_el_dia_anterior_al_cumple_18_es_menor(self):
        self.assertFalse(es_mayor_de_edad(date(2008, 11, 22), date(2026, 11, 21)))

    def test_sin_fecha_no_es_mayor(self):
        self.assertFalse(es_mayor_de_edad(None, date(2026, 11, 21)))

    def test_nacido_un_29_feb_cumple_el_1_mar(self):
        nacimiento = date(2004, 2, 29)
        self.assertFalse(es_mayor_de_edad(nacimiento, date(2022, 2, 28)))
        self.assertTrue(es_mayor_de_edad(nacimiento, date(2022, 3, 1)))
        # Con hoy = 29-feb el corte es el 28-feb de 18 años atrás.
        self.assertFalse(es_mayor_de_edad(date(2006, 3, 1), date(2024, 2, 29)))
        self.assertEqual(fecha_corte_mayoria(date(2024, 2, 29)), date(2006, 2, 28))
        self.assertTrue(es_mayor_de_edad(date(2006, 2, 28), date(2024, 2, 29)))

    def test_el_29_feb_bisiesto_a_los_18_es_mayor(self):
        self.assertTrue(es_mayor_de_edad(date(2008, 2, 29), date(2026, 3, 1)))
        self.assertTrue(es_mayor_de_edad(date(2004, 2, 29), date(2022, 3, 1)))

    def test_usa_la_fecha_local_no_la_utc(self):
        # 22-nov 01:30 UTC = 21-nov 22:30 en Argentina (UTC-3).
        reloj = datetime(2026, 11, 22, 1, 30, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", return_value=reloj):
            self.assertEqual(fecha_corte_mayoria(), date(2008, 11, 21))
            self.assertTrue(es_mayor_de_edad(date(2008, 11, 21)))
            self.assertFalse(es_mayor_de_edad(date(2008, 11, 22)))

    def test_propiedad_del_modelo(self):
        self.assertFalse(Alumno(fecha_nacimiento=None).es_mayor_de_edad)
        self.assertTrue(Alumno(fecha_nacimiento=date(1990, 1, 1)).es_mayor_de_edad)
        self.assertFalse(Alumno(fecha_nacimiento=date.today()).es_mayor_de_edad)
