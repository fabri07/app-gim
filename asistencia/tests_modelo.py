"""Modelo `Asistencia`: unicidad por día, cascadas, aislamiento y los tres
lugares que exige CLAUDE.md a todo `TenantOwnedModel` nuevo."""

from datetime import date, time

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from alumnos.models import Alumno
from asistencia.models import Asistencia
from tenants.models import Gimnasio, Perfil


class AsistenciaModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Paz"
        )
        self.alumno_otro = Alumno.objects.create(
            gimnasio=self.otro, nombre="Beto", apellido="Gil"
        )

    def _marca(self, **extra):
        datos = {
            "gimnasio": self.gimnasio, "alumno": self.alumno,
            "fecha": date(2026, 11, 10),
        }
        datos.update(extra)
        return Asistencia.objects.create(**datos)

    def test_crear_y_recuperar(self):
        marca = self._marca(hora=time(9))
        marca = Asistencia.objects.get(pk=marca.pk)
        self.assertEqual(marca.fecha, date(2026, 11, 10))
        self.assertEqual(marca.hora, time(9))
        self.assertEqual(marca.origen, Asistencia.Origen.MANUAL)
        self.assertIsNone(marca.registrada_por)
        self.assertIsNotNone(marca.creado)
        self.assertEqual(list(self.alumno.asistencias.all()), [marca])

    def test_hora_es_opcional_y_origen_turno_existe(self):
        marca = self._marca(origen=Asistencia.Origen.TURNO)
        self.assertIsNone(marca.hora)
        self.assertEqual(marca.origen, "turno")

    def test_unicidad_rechaza_mismo_alumno_dia_y_gimnasio(self):
        self._marca()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._marca()

    def test_unicidad_permite_otro_dia_y_otro_gimnasio(self):
        self._marca()
        self._marca(fecha=date(2026, 11, 11))
        Asistencia.objects.create(
            gimnasio=self.otro, alumno=self.alumno_otro, fecha=date(2026, 11, 10)
        )
        self.assertEqual(Asistencia.objects.count(), 3)

    def test_borrar_el_alumno_borra_sus_marcas(self):
        self._marca()
        self.alumno.delete()
        self.assertFalse(Asistencia.objects.exists())

    def test_borrar_el_perfil_deja_la_marca_sin_registrada_por(self):
        usuario = User.objects.create_user("staff-a", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        marca = self._marca(registrada_por=perfil)
        perfil.delete()
        marca.refresh_from_db()
        self.assertIsNone(marca.registrada_por)

    def test_cada_gimnasio_ve_solo_sus_marcas(self):
        mia = self._marca()
        Asistencia.objects.create(
            gimnasio=self.otro, alumno=self.alumno_otro, fecha=date(2026, 11, 10)
        )
        self.assertEqual(list(Asistencia.objects.for_gimnasio(self.gimnasio)), [mia])

    def test_vaciar_gimnasio_la_borra(self):
        from tenants.demo import vaciar_gimnasio

        demo = Gimnasio.objects.create(nombre="Demo", slug="demo", es_demo=True)
        alumno = Alumno.objects.create(gimnasio=demo, nombre="D", apellido="D")
        Asistencia.objects.create(gimnasio=demo, alumno=alumno, fecha=date(2026, 11, 10))
        vaciar_gimnasio(gimnasio=demo)
        self.assertFalse(Asistencia.objects.for_gimnasio(demo).exists())

    def test_la_exportacion_incluye_la_hoja_asistencias(self):
        from tenants.exportacion import HOJAS

        self.assertIn("asistencias", [h.nombre for h in HOJAS])
