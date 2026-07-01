"""
Tests de Fase 1 para `rutinas`.

El test más importante de todo este archivo (y probablemente de toda la
Fase 1) es `RutinaAsignadaSnapshotTests.test_editar_la_plantilla_no_afecta_la_asignacion_existente`:
verifica que `RutinaAsignada.crear_desde_plantilla` produce una copia
realmente congelada, no una referencia viva a la plantilla.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from alumnos.models import Alumno
from ejercicios.models import Ejercicio
from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)
from tenants.models import Gimnasio


class RutinasTestCase(TestCase):
    """Base con el fixture común: un gimnasio, un alumno y dos ejercicios."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central", slug="gimnasio-central"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.press_banca = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
            url_video="https://youtube.com/watch?v=press",
        )
        self.sentadilla = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
            url_video="https://youtube.com/watch?v=sentadilla",
        )

    def crear_plantilla_con_items(self):
        plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio,
            nombre="Full body principiante",
            objetivo="Hipertrofia",
            nivel=RutinaPlantilla.Nivel.PRINCIPIANTE,
            dias_por_semana=3,
        )
        item1 = RutinaPlantillaItem.objects.create(
            rutina=plantilla,
            ejercicio=self.press_banca,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8-12",
            descanso="90s",
        )
        item2 = RutinaPlantillaItem.objects.create(
            rutina=plantilla,
            ejercicio=self.sentadilla,
            dia=1,
            orden=2,
            series=3,
            repeticiones="10",
            descanso="60s",
        )
        return plantilla, item1, item2


class ModeloBasicoTests(RutinasTestCase):
    """Creación y `__str__` básicos de los cuatro modelos."""

    def test_rutina_plantilla_creacion_y_str(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        self.assertEqual(str(plantilla), "Full body principiante")
        self.assertTrue(plantilla.activa)

    def test_rutina_plantilla_item_creacion_y_str(self):
        _, item1, _ = self.crear_plantilla_con_items()
        self.assertEqual(str(item1), "Día 1 · Press de banca")

    def test_rutina_asignada_creacion_y_str(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        self.assertEqual(
            str(asignada), "Pérez, Ana · Full body principiante desde 2026-01-01"
        )
        self.assertTrue(asignada.activa)

    def test_rutina_asignada_item_creacion_y_str(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        item = asignada.items.first()
        self.assertEqual(str(item), "Día 1 · Press de banca")


class CrearDesdePlantillaTests(RutinasTestCase):
    """`crear_desde_plantilla` copia lo que corresponde, y solo eso."""

    def test_copia_la_cantidad_correcta_de_items(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        self.assertEqual(asignada.items.count(), 2)

    def test_copia_los_valores_correctos(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 3, 15),
        )

        self.assertEqual(asignada.nombre_snapshot, plantilla.nombre)
        self.assertEqual(asignada.objetivo_snapshot, plantilla.objetivo)
        self.assertEqual(asignada.fecha_inicio, date(2026, 3, 15))
        self.assertEqual(asignada.alumno, self.alumno)
        self.assertEqual(asignada.gimnasio, self.gimnasio)

        copiado1 = asignada.items.get(orden=1)
        self.assertEqual(copiado1.ejercicio_nombre_snapshot, item1.ejercicio.nombre)
        self.assertEqual(copiado1.ejercicio_video_snapshot, item1.ejercicio.url_video)
        self.assertEqual(copiado1.dia, item1.dia)
        self.assertEqual(copiado1.series, item1.series)
        self.assertEqual(copiado1.repeticiones, item1.repeticiones)
        self.assertEqual(copiado1.descanso, item1.descanso)

        copiado2 = asignada.items.get(orden=2)
        self.assertEqual(copiado2.ejercicio_nombre_snapshot, item2.ejercicio.nombre)

    def test_falla_si_la_plantilla_es_de_otro_gimnasio(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym")
        plantilla, _, _ = self.crear_plantilla_con_items()

        with self.assertRaises(ValidationError):
            RutinaAsignada.crear_desde_plantilla(
                gimnasio=otro_gimnasio,
                alumno=self.alumno,
                plantilla=plantilla,
                fecha_inicio=date(2026, 1, 1),
            )

    def test_falla_si_el_alumno_es_de_otro_gimnasio(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym")
        otro_alumno = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Bruno", apellido="Gómez"
        )
        plantilla, _, _ = self.crear_plantilla_con_items()

        with self.assertRaises(ValidationError):
            RutinaAsignada.crear_desde_plantilla(
                gimnasio=self.gimnasio,
                alumno=otro_alumno,
                plantilla=plantilla,
                fecha_inicio=date(2026, 1, 1),
            )

        # Ninguna RutinaAsignada debe haber quedado creada a medio camino.
        self.assertEqual(RutinaAsignada.objects.count(), 0)


class RutinaAsignadaSnapshotTests(RutinasTestCase):
    """El invariante clave: la asignación es un snapshot congelado."""

    def test_editar_la_plantilla_no_afecta_la_asignacion_existente(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()

        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        # Snapshot de los valores ANTES de mutar la plantilla, para comparar.
        nombre_original = asignada.nombre_snapshot
        objetivo_original = asignada.objetivo_snapshot
        item1_original = asignada.items.get(orden=1)
        nombre_ejercicio_original = item1_original.ejercicio_nombre_snapshot
        video_original = item1_original.ejercicio_video_snapshot
        series_original = item1_original.series

        # Mutamos la plantilla original de todas las formas posibles.
        plantilla.nombre = "Full body AVANZADO (renombrada)"
        plantilla.objetivo = "Fuerza"
        plantilla.save()

        item1.ejercicio = self.sentadilla  # reemplaza el ejercicio del item
        item1.series = 99
        item1.repeticiones = "1"
        item1.save()

        item2.delete()  # incluso borrar un item de la plantilla

        # La RutinaAsignada y sus items deben seguir mostrando los valores
        # ORIGINALES, sin ningún cambio.
        asignada.refresh_from_db()
        self.assertEqual(asignada.nombre_snapshot, nombre_original)
        self.assertEqual(asignada.objetivo_snapshot, objetivo_original)
        self.assertEqual(asignada.nombre_snapshot, "Full body principiante")
        self.assertEqual(asignada.objetivo_snapshot, "Hipertrofia")

        item1_asignado = asignada.items.get(orden=1)
        item1_asignado.refresh_from_db()
        self.assertEqual(
            item1_asignado.ejercicio_nombre_snapshot, nombre_ejercicio_original
        )
        self.assertEqual(item1_asignado.ejercicio_nombre_snapshot, "Press de banca")
        self.assertEqual(item1_asignado.ejercicio_video_snapshot, video_original)
        self.assertEqual(item1_asignado.series, series_original)
        self.assertEqual(item1_asignado.series, 4)

        # El item2 de la asignación sigue existiendo aunque el de la
        # plantilla se haya borrado.
        self.assertEqual(asignada.items.count(), 2)
        self.assertTrue(asignada.items.filter(orden=2).exists())

    def test_borrar_el_ejercicio_original_no_afecta_la_asignacion(self):
        """El punto de no tener FK viva a Ejercicio en el item asignado."""
        plantilla, item1, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        # Para borrar el Ejercicio primero hay que quitar la referencia PROTECT
        # de la plantilla (comportamiento esperado y correcto de PROTECT).
        item1.delete()
        self.press_banca.delete()

        item_asignado = asignada.items.get(ejercicio_nombre_snapshot="Press de banca")
        self.assertEqual(item_asignado.ejercicio_nombre_snapshot, "Press de banca")


class DuplicarPlantillaTests(RutinasTestCase):
    """`RutinaPlantilla.duplicar()` crea una copia independiente."""

    def test_duplicar_crea_copia_con_nombre_sufijado_y_mismos_items(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()

        copia = plantilla.duplicar()

        self.assertNotEqual(copia.pk, plantilla.pk)
        self.assertEqual(copia.nombre, "Full body principiante (copia)")
        self.assertEqual(copia.objetivo, plantilla.objetivo)
        self.assertEqual(copia.nivel, plantilla.nivel)
        self.assertEqual(copia.dias_por_semana, plantilla.dias_por_semana)
        self.assertEqual(copia.gimnasio, plantilla.gimnasio)
        self.assertEqual(copia.items.count(), 2)

    def test_mutar_los_items_de_la_copia_no_afecta_al_original(self):
        plantilla, item1, _ = self.crear_plantilla_con_items()
        copia = plantilla.duplicar()

        item_copiado = copia.items.get(orden=1)
        item_copiado.series = 999
        item_copiado.save()

        item1.refresh_from_db()
        self.assertEqual(item1.series, 4)
        self.assertNotEqual(item1.series, item_copiado.series)

    def test_mutar_los_items_del_original_no_afecta_a_la_copia(self):
        plantilla, item1, _ = self.crear_plantilla_con_items()
        copia = plantilla.duplicar()

        item1.series = 1
        item1.save()

        item_copiado = copia.items.get(orden=1)
        self.assertEqual(item_copiado.series, 4)


class AislamientoTenantTests(RutinasTestCase):
    """`for_gimnasio` no debe mezclar datos entre gimnasios."""

    def test_rutina_plantilla_for_gimnasio(self):
        plantilla_propia, _, _ = self.crear_plantilla_con_items()
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym")
        RutinaPlantilla.objects.create(
            gimnasio=otro_gimnasio,
            nombre="Rutina de otro gym",
            objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.AVANZADO,
            dias_por_semana=5,
        )

        propias = RutinaPlantilla.objects.for_gimnasio(self.gimnasio)
        self.assertEqual(list(propias), [plantilla_propia])

    def test_rutina_asignada_for_gimnasio(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada_propia = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        otro_gimnasio = Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym")
        otro_alumno = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Carla", apellido="Ruiz"
        )
        otra_plantilla = RutinaPlantilla.objects.create(
            gimnasio=otro_gimnasio,
            nombre="Rutina de otro gym",
            objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.AVANZADO,
            dias_por_semana=5,
        )
        RutinaAsignada.crear_desde_plantilla(
            gimnasio=otro_gimnasio,
            alumno=otro_alumno,
            plantilla=otra_plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        propias = RutinaAsignada.objects.for_gimnasio(self.gimnasio)
        self.assertEqual(list(propias), [asignada_propia])
