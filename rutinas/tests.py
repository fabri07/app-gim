"""
Tests de `rutinas`.

Fase 1 (arriba): el test más importante de todo este archivo (y
probablemente de toda la Fase 1) es
`RutinaAsignadaSnapshotTests.test_editar_la_plantilla_no_afecta_la_asignacion_existente`:
verifica que `RutinaAsignada.crear_desde_plantilla` produce una copia
realmente congelada, no una referencia viva a la plantilla.

Fase 2 (abajo, `RutinasViewsTests`): vistas de gestión -- acceso por rol,
aislamiento de tenant (incluido el caso especial de los items, que no son
`TenantOwnedModel` y se aíslan a través de su plantilla padre), el hueco de
FK-injection en el campo `ejercicio` del item, duplicar (POST-only) y el
flujo de asignación de punta a punta.
"""

from datetime import date, timedelta
from importlib import import_module
from pathlib import Path

from django.conf import settings

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ejercicios.models import CategoriaEjercicio, Ejercicio
from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaDiaCompletado,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)
from rutinas import progreso, services
from rutinas.agrupacion import listar_ejercicios_del_dia
from rutinas.pdf import (
    _celda_semana,
    _fila_ejercicio,
    generar_pdf_rutina_asignada,
)
from tenants.models import Gimnasio, Perfil


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


class SemanaItemTests(RutinasTestCase):
    """Campo `semana` (1-4) en los items de plantilla y de asignación."""

    def test_item_semana_default_es_1(self):
        _, item1, _ = self.crear_plantilla_con_items()
        self.assertEqual(item1.semana, 1)

    def test_item_semana_acepta_valor_explicito(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        item = RutinaPlantillaItem.objects.create(
            rutina=plantilla,
            ejercicio=self.press_banca,
            semana=3,
            dia=1,
            orden=3,
            series=3,
            repeticiones="10",
        )
        self.assertEqual(item.semana, 3)

    def test_item_semana_fuera_de_rango_falla_full_clean(self):
        _, item1, _ = self.crear_plantilla_con_items()
        item1.semana = 5
        with self.assertRaises(ValidationError):
            item1.full_clean()
        item1.semana = 0
        with self.assertRaises(ValidationError):
            item1.full_clean()

    def test_items_ordenados_por_semana_antes_que_dia_y_orden(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        # item1: dia=1, orden=1 · item2: dia=1, orden=2 (mismo día, ambos semana=1 por default)
        item1.semana = 2
        item1.save()
        items = list(plantilla.items.all())
        # item2 (semana=1) debe listar ANTES que item1 (semana=2), aunque
        # item1 tenga menor `orden` -- confirma que `semana` pesa más que `orden`.
        self.assertEqual(items[0], item2)
        self.assertEqual(items[1], item1)

    def test_rutina_asignada_item_semana_default_es_1_y_acepta_explicito(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        item = asignada.items.first()
        self.assertEqual(item.semana, 1)
        item.semana = 4
        item.full_clean()
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.semana, 4)


class SemanaActualTests(RutinasTestCase):
    """`RutinaAsignada.semana_actual`: calculada por fecha, clampeada en 4,
    sin loop automático."""

    def _asignada_con_inicio(self, fecha_inicio):
        plantilla, _, _ = self.crear_plantilla_con_items()
        return RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=fecha_inicio,
        )

    def test_semana_actual_es_1_el_dia_de_inicio(self):
        asignada = self._asignada_con_inicio(timezone.localdate())
        self.assertEqual(asignada.semana_actual, 1)

    def test_semana_actual_es_1_a_los_6_dias(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=6)
        )
        self.assertEqual(asignada.semana_actual, 1)

    def test_semana_actual_pasa_a_2_a_los_7_dias(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=7)
        )
        self.assertEqual(asignada.semana_actual, 2)

    def test_semana_actual_se_clampea_en_4(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=100)
        )
        self.assertEqual(asignada.semana_actual, 4)

    def test_semana_actual_es_1_si_fecha_inicio_es_futura(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() + timedelta(days=5)
        )
        self.assertEqual(asignada.semana_actual, 1)


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

    def test_crear_desde_plantilla_copia_la_semana_de_cada_item(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.semana = 3
        item1.save()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        copiado1 = asignada.items.get(orden=1)
        self.assertEqual(copiado1.semana, 3)
        copiado2 = asignada.items.get(orden=2)
        self.assertEqual(copiado2.semana, 1)

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


class RutinaPdfTests(RutinasTestCase):
    """`rutinas/pdf.py` es Django-free a propósito (no toca `django.http`,
    solo arma bytes) -- se testea llamando a la función directo, sin pasar
    por una vista."""

    def test_genera_un_pdf_valido_con_ejercicios(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        pdf_bytes = generar_pdf_rutina_asignada(asignada)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_genera_un_pdf_valido_con_kilos_y_multiples_dias(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.kilos = "20kg"
        item1.notas = "Cuidado con la zona lumbar"
        item1.save()
        item2.dia = 2  # fuerza un segundo día, además del grupo/semana
        item2.save()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        pdf_bytes = generar_pdf_rutina_asignada(asignada)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_celda_semana_incluye_descanso_y_notas_cuando_estan_cargados(self):
        """`_celda_semana` es una función pura -- se testea directo, sin
        pasar por fpdf2 (que comprime el contenido y no se puede grepear
        del PDF final)."""
        item = RutinaAsignadaItem(
            series=3, repeticiones="12", kilos="20kg",
            descanso="90s", notas="Cuidado con la zona lumbar",
            rpe=RutinaAsignadaItem.RPE.AL_LIMITE,
        )
        texto = _celda_semana(item)
        self.assertIn("Series: 3", texto)
        self.assertIn("Repeticiones: 12", texto)
        self.assertIn("Kilos: 20kg", texto)
        self.assertIn("Descanso: 90s", texto)
        self.assertIn("Calificación: Estoy al límite", texto)
        self.assertIn("Notas: Cuidado con la zona lumbar", texto)

    def test_celda_semana_sin_descanso_ni_notas_queda_compacta(self):
        item = RutinaAsignadaItem(series=3, repeticiones="12")
        texto = _celda_semana(item)
        self.assertEqual(texto, "Series: 3\nRepeticiones: 12")

    def test_celda_semana_trunca_notas_muy_largas(self):
        """`pdf.table()` de fpdf2 no puede partir una fila entre dos
        páginas: una celda con notas muy largas hace crashear la
        generación entera con `ValueError` (ver `_NOTAS_MAX_CARACTERES_EN_TABLA`
        en `rutinas/pdf.py`). La celda trunca; el texto completo va aparte
        en el apéndice -- se verifica en
        `test_genera_un_pdf_valido_con_notas_muy_largas` que nunca se pierde."""
        item = RutinaAsignadaItem(
            series=3, repeticiones="12", notas="x" * 500,
        )
        texto = _celda_semana(item)
        self.assertIn("(completa al final del PDF)", texto)
        self.assertNotIn("x" * 500, texto)

    def test_genera_un_pdf_valido_con_notas_muy_largas(self):
        """Regresión: antes de acotar `_celda_semana`, una nota larga (una
        indicación de seguridad real que un profesor podría escribir)
        hacía que `generar_pdf_rutina_asignada` reviente con `ValueError`
        en vez de generar el PDF -- justo el fallback que CLAUDE.md dice
        que "tiene que funcionar siempre". Usa una sola palabra sin
        espacios (peor caso para el word-wrap) para no depender de dónde
        fpdf2 decide cortar líneas."""
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.notas = "a" * 2000
        item1.save()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )

        pdf_bytes = generar_pdf_rutina_asignada(asignada)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_genera_un_pdf_valido_sin_ejercicios(self):
        """Borde: una asignación recién creada sin items no debe romper la
        generación (mismo caso que el `{% empty %}` del template HTML)."""
        asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Rutina vacía",
            objetivo_snapshot="Sin definir",
            fecha_inicio=date(2026, 1, 1),
        )

        pdf_bytes = generar_pdf_rutina_asignada(asignada)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))


class ListarEjerciciosDelDiaTests(RutinasTestCase):
    """`rutinas/agrupacion.py` -- Django-free, se testea con instancias
    armadas a mano (no hace queries)."""

    def crear_asignada_vacia(self):
        return RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Full Body",
            objetivo_snapshot="General",
            fecha_inicio=date(2026, 1, 1),
        )

    def test_devuelve_lista_plana_sin_subdivision_por_grupo(self):
        """No hay más nivel de agrupación por grupo muscular -- cada
        ejercicio del resultado conserva su propio `categoria_display`,
        pero el resultado es una única lista, no una lista de grupos."""
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Press de banca",
            categoria_snapshot="Pecho",
            semana=1,
            dia=1,
            orden=2,
            series=3,
            repeticiones="10",
        )

        ejercicios = listar_ejercicios_del_dia(asignada.items.filter(dia=1))

        self.assertEqual(
            [e["nombre"] for e in ejercicios], ["Sentadilla", "Press de banca"]
        )
        self.assertEqual(ejercicios[0]["categoria_display"], "Piernas")
        self.assertEqual(ejercicios[1]["categoria_display"], "Pecho")

    def test_item_sin_grupo_muscular_muestra_display_sin_grupo(self):
        """Simula una `RutinaAsignadaItem` creada antes de que existiera
        `categoria_snapshot` (queda "" por default) -- no se
        descarta, se muestra con el display "Sin categoría"."""
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Ejercicio viejo",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

        ejercicios = listar_ejercicios_del_dia(asignada.items.filter(dia=1))

        self.assertEqual(ejercicios[0]["nombre"], "Ejercicio viejo")
        self.assertEqual(ejercicios[0]["categoria_display"], "Sin categoría")

    def test_categoria_display_usa_la_semana_mas_baja_no_el_orden_de_iteracion(self):
        """Si el mismo ejercicio quedó snapshoteado con un grupo muscular
        distinto entre semanas (p. ej. se recategorizó en la biblioteca a
        mitad de una rutina de 4 semanas), `categoria_display` tiene
        que venir del item de la semana MÁS BAJA -- igual criterio que ya
        usa `orden` -- y no de cuál item llegó primero en `items` (la
        función documenta que acepta "cualquier orden")."""
        asignada = self.crear_asignada_vacia()
        item_semana_2 = RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Core",
            semana=2,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        item_semana_1 = RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

        # Iterable armado a mano, semana 2 ANTES que semana 1 -- si la
        # función leyera `categoria_display` del primer item iterado
        # (bug real, no solo hipotético) este test lo detecta.
        ejercicios = listar_ejercicios_del_dia([item_semana_2, item_semana_1])

        self.assertEqual(ejercicios[0]["categoria_display"], "Piernas")

    def test_mismo_ejercicio_a_traves_de_semanas_se_identifica_por_nombre(self):
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=2,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8",
        )
        # Semana 3 no tiene fila cargada para este ejercicio a propósito.
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=4,
            dia=1,
            orden=1,
            series=5,
            repeticiones="6",
        )

        ejercicios = listar_ejercicios_del_dia(asignada.items.filter(dia=1))

        self.assertEqual(len(ejercicios), 1)
        semanas = ejercicios[0]["semanas"]
        self.assertEqual([s["numero"] for s in semanas], [1, 2, 3, 4])
        self.assertEqual(semanas[0]["item"].series, 3)
        self.assertEqual(semanas[1]["item"].series, 4)
        self.assertIsNone(semanas[2]["item"])
        self.assertEqual(semanas[3]["item"].series, 5)

    def test_video_se_toma_del_primer_valor_no_vacio_entre_semanas(self):
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            ejercicio_video_snapshot="",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            ejercicio_video_snapshot="https://youtube.com/watch?v=sentadilla",
            categoria_snapshot="Piernas",
            semana=2,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8",
        )

        ejercicios = listar_ejercicios_del_dia(asignada.items.filter(dia=1))

        self.assertEqual(
            ejercicios[0]["video"],
            "https://youtube.com/watch?v=sentadilla",
        )

    def test_orden_usa_la_semana_mas_baja_disponible_no_el_minimo(self):
        """Si el `orden` varía entre semanas para el mismo ejercicio, el
        orden en la lista debe salir de la semana MÁS BAJA cargada para
        ese ejercicio, no del valor mínimo de `orden` entre todas sus
        filas (que podría no corresponder a ninguna semana realmente
        presente)."""
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=2,
            dia=1,
            orden=5,
            series=3,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=4,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

        ejercicios = listar_ejercicios_del_dia(asignada.items.filter(dia=1))

        # La semana más baja disponible es la 2 (orden=5), no la 4 (orden=1).
        self.assertEqual(ejercicios[0]["orden"], 5)

    def test_respeta_la_lista_de_semanas_pasada_explicita(self):
        """El caller (p. ej. `RutinaMiDiaDetailView`) puede pasar qué
        números de semana quiere como columnas -- no queda hardcodeado a
        1..SEMANAS_POR_CICLO, para poder compartir la misma lista que
        arma el header de la vista y evitar que las dos se desalineen."""
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

        ejercicios = listar_ejercicios_del_dia(
            asignada.items.filter(dia=1), semanas=[1, 2]
        )

        semanas = ejercicios[0]["semanas"]
        self.assertEqual([s["numero"] for s in semanas], [1, 2])

    def test_es_actual_marca_solo_la_semana_indicada(self):
        """`semana_actual` es opcional -- si no se pasa (caso del PDF,
        que no resalta nada), todas las celdas quedan `es_actual=False`
        sin romper."""
        asignada = self.crear_asignada_vacia()
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            categoria_snapshot="Piernas",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

        ejercicios = listar_ejercicios_del_dia(
            asignada.items.filter(dia=1), semanas=[1, 2, 3], semana_actual=2
        )
        semanas = ejercicios[0]["semanas"]
        self.assertEqual(
            [s["es_actual"] for s in semanas], [False, True, False]
        )

        ejercicios_sin_actual = listar_ejercicios_del_dia(
            asignada.items.filter(dia=1), semanas=[1, 2, 3]
        )
        semanas_sin_actual = ejercicios_sin_actual[0]["semanas"]
        self.assertEqual(
            [s["es_actual"] for s in semanas_sin_actual], [False, False, False]
        )


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

    def test_duplicar_copia_la_semana_de_cada_item(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.semana = 2
        item1.save()
        copia = plantilla.duplicar()
        copiado1 = copia.items.get(orden=1)
        self.assertEqual(copiado1.semana, 2)
        copiado2 = copia.items.get(orden=2)
        self.assertEqual(copiado2.semana, 1)


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


User = get_user_model()


class RutinasViewsTests(TestCase):
    """Vistas de gestión de rutinas (Fase 2): acceso por rol, aislamiento de
    tenant (plantillas y, a través de su padre, items), el cierre del hueco
    de FK-injection en `ejercicio`, duplicar (POST-only) y la asignación de
    punta a punta."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF
        )

        self.usuario_alumno = User.objects.create_user(
            username="usuario_alumno", password="clave12345"
        )
        Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.ejercicio_a = Ejercicio.objects.create(
            gimnasio=self.gimnasio_a,
            nombre="Sentadilla A",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        self.ejercicio_b = Ejercicio.objects.create(
            gimnasio=self.gimnasio_b,
            nombre="Sentadilla B",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Ana", apellido="Pérez"
        )
        self.alumno_a_inactivo = Alumno.objects.create(
            gimnasio=self.gimnasio_a,
            nombre="Inactivo",
            apellido="Alumno",
            estado=Alumno.Estado.INACTIVO,
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Bruno", apellido="Gómez"
        )

        self.plantilla_a = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio_a,
            nombre="Rutina A",
            objetivo="Hipertrofia",
            nivel=RutinaPlantilla.Nivel.PRINCIPIANTE,
            dias_por_semana=3,
        )
        self.plantilla_a_inactiva = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio_a,
            nombre="Rutina A inactiva",
            objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.AVANZADO,
            dias_por_semana=4,
            activa=False,
        )
        self.item_a = RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_a,
            ejercicio=self.ejercicio_a,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8-12",
            descanso="90s",
        )

        self.plantilla_b = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio_b,
            nombre="Rutina B",
            objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.AVANZADO,
            dias_por_semana=5,
        )
        self.item_b = RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_b,
            ejercicio=self.ejercicio_b,
            dia=1,
            orden=1,
            series=5,
            repeticiones="5",
            descanso="120s",
        )

        self.asignada_a = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            plantilla=self.plantilla_a,
            fecha_inicio=date(2026, 1, 1),
        )

    def _urls_get_staff(self):
        return [
            reverse("rutinas:plantilla_listado"),
            reverse("rutinas:plantilla_crear"),
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_a.pk]),
            reverse("rutinas:plantilla_editar", args=[self.plantilla_a.pk]),
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            reverse(
                "rutinas:item_editar", args=[self.plantilla_a.pk, self.item_a.pk]
            ),
            reverse("rutinas:asignar"),
            reverse("rutinas:asignada_detalle", args=[self.asignada_a.pk]),
            reverse("rutinas:asignada_pdf", args=[self.asignada_a.pk]),
        ]

    # 1. Anónimo -> redirect a login; rol ALUMNO -> 403 (ver
    # docstring de `alumnos/tests.py`: PermissionDenied vía self.client
    # resuelve en un 403 normal, no en una excepción de Python, porque
    # `response_for_exception` la convierte en respuesta antes de que exista
    # oportunidad de re-lanzarla).
    def test_anonimo_redirige_a_login_en_todas_las_vistas(self):
        for url in self._urls_get_staff():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse("login"), response.url)

    def test_perfil_alumno_recibe_403_en_todas_las_vistas(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        for url in self._urls_get_staff():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)

    # 2. CRUD de plantilla para staff de su propio gimnasio; 404 cross-tenant.
    def test_staff_puede_listar_crear_ver_y_editar_su_plantilla(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("rutinas:plantilla_listado"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rutina A")

        datos = {
            "nombre": "Rutina nueva",
            "objetivo": "Fuerza",
            "nivel": RutinaPlantilla.Nivel.INTERMEDIO,
            "dias_por_semana": 4,
            "activa": "on",
        }
        response = self.client.post(reverse("rutinas:plantilla_crear"), datos)
        self.assertEqual(response.status_code, 302)
        nueva = RutinaPlantilla.objects.get(nombre="Rutina nueva")
        self.assertEqual(nueva.gimnasio, self.gimnasio_a)

        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sentadilla A")

        datos_editados = {
            "nombre": "Rutina A editada",
            "objetivo": "Hipertrofia",
            "nivel": RutinaPlantilla.Nivel.PRINCIPIANTE,
            "dias_por_semana": 3,
            "activa": "on",
        }
        response = self.client.post(
            reverse("rutinas:plantilla_editar", args=[self.plantilla_a.pk]),
            datos_editados,
        )
        self.assertEqual(response.status_code, 302)
        self.plantilla_a.refresh_from_db()
        self.assertEqual(self.plantilla_a.nombre, "Rutina A editada")

    def test_aislamiento_de_tenant_en_plantilla_devuelve_404(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_b.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse("rutinas:plantilla_editar", args=[self.plantilla_b.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            reverse("rutinas:plantilla_editar", args=[self.plantilla_b.pk]),
            {
                "nombre": "hackeada",
                "objetivo": "x",
                "nivel": RutinaPlantilla.Nivel.AVANZADO,
                "dias_por_semana": 1,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.plantilla_b.refresh_from_db()
        self.assertEqual(self.plantilla_b.nombre, "Rutina B")

    # 3. Items: CRUD dentro de la plantilla correcta; 404 cross-tenant vía el
    # lookup del padre (ni siquiera llega a consultar el item).
    def test_item_crud_dentro_de_la_plantilla_correcta(self):
        self.client.login(username="staff_a", password="clave12345")

        datos = {
            "ejercicio": self.ejercicio_a.pk,
            "semana": 2,
            "dia": 2,
            "orden": 1,
            "series": 3,
            "repeticiones": "10",
            "descanso": "60s",
            "notas": "",
        }
        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]), datos
        )
        self.assertEqual(response.status_code, 302)
        nuevo_item = RutinaPlantillaItem.objects.get(rutina=self.plantilla_a, dia=2)
        self.assertEqual(nuevo_item.ejercicio, self.ejercicio_a)
        self.assertEqual(nuevo_item.semana, 2)

        response = self.client.get(
            reverse(
                "rutinas:item_editar", args=[self.plantilla_a.pk, nuevo_item.pk]
            )
        )
        self.assertEqual(response.status_code, 200)

        datos["series"] = 9
        datos["semana"] = 3
        response = self.client.post(
            reverse(
                "rutinas:item_editar", args=[self.plantilla_a.pk, nuevo_item.pk]
            ),
            datos,
        )
        self.assertEqual(response.status_code, 302)
        nuevo_item.refresh_from_db()
        self.assertEqual(nuevo_item.series, 9)
        self.assertEqual(nuevo_item.semana, 3)

        eliminar_url = reverse(
            "rutinas:item_eliminar", args=[self.plantilla_a.pk, nuevo_item.pk]
        )
        response = self.client.get(eliminar_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(eliminar_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            RutinaPlantillaItem.objects.filter(pk=nuevo_item.pk).exists()
        )

    def test_item_de_otro_gimnasio_no_es_accesible_desde_plantilla_ajena(self):
        self.client.login(username="staff_a", password="clave12345")

        # La plantilla del kwarg es de OTRO gimnasio: 404 antes de tocar el item.
        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_b.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse("rutinas:item_editar", args=[self.plantilla_b.pk, self.item_b.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            reverse(
                "rutinas:item_eliminar", args=[self.plantilla_b.pk, self.item_b.pk]
            )
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(RutinaPlantillaItem.objects.filter(pk=self.item_b.pk).exists())

    # --- Alta de items: qué puede quedar en blanco -----------------------
    #
    # Reporte real del primer cliente pago: armaba una plantilla desde cero,
    # dejaba casilleros vacíos, guardaba, y la plantilla quedaba SIEMPRE
    # vacía. El form devolvía "Este campo es obligatorio" para `dia` y
    # `orden`, pero `.errorlist` no tenía ningún estilo en el proyecto, así
    # que el mensaje salía en negro, del mismo tamaño que las ayudas grises y
    # ARRIBA de la etiqueta: se leía como una instrucción más.
    #
    # `orden` es un número administrativo que el sistema puede deducir --
    # `services.agregar_ejercicio_asignado` ya lo hacía así (`max + 1`) para
    # el otro flujo. `series`/`repeticiones` siguen obligatorios: son la
    # prescripción del entrenamiento y no hay valor sensato que inventar.

    def test_orden_en_blanco_se_asigna_al_final_del_dia(self):
        self.client.login(username="staff_a", password="clave12345")
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_a, ejercicio=self.ejercicio_a,
            semana=1, dia=1, orden=7, series=3, repeticiones="10",
        )

        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 1,
                "orden": "", "series": 4, "repeticiones": "12",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        creado = RutinaPlantillaItem.objects.filter(
            rutina=self.plantilla_a, dia=1, repeticiones="12"
        ).get()
        self.assertEqual(creado.orden, 8)

    def test_orden_en_blanco_en_un_dia_vacio_arranca_en_uno(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 5,
                "orden": "", "series": 3, "repeticiones": "10",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RutinaPlantillaItem.objects.get(rutina=self.plantilla_a, dia=5).orden, 1
        )

    def test_el_orden_se_cuenta_por_dia_no_por_plantilla(self):
        """Dos días distintos numeran desde 1 cada uno: `orden` es "orden
        dentro del día" (ver el help_text del modelo), no un contador global.
        Sin el filtro por día, el primer ejercicio del día 2 arrancaría en 8.
        """
        self.client.login(username="staff_a", password="clave12345")
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_a, ejercicio=self.ejercicio_a,
            semana=1, dia=1, orden=7, series=3, repeticiones="10",
        )

        self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 2,
                "orden": "", "series": 3, "repeticiones": "10",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        self.assertEqual(
            RutinaPlantillaItem.objects.get(rutina=self.plantilla_a, dia=2).orden, 1
        )

    def test_series_y_repeticiones_siguen_siendo_obligatorias(self):
        """Decisión de producto: son el contenido real del ejercicio. Un item
        sin ellas le llegaría al alumno como una fila vacía, en el portal y
        en el PDF."""
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 1,
                "orden": "", "series": "", "repeticiones": "",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("series", response.context["form"].errors)
        self.assertIn("repeticiones", response.context["form"].errors)

    def test_el_form_llega_precargado_con_el_proximo_dia_y_orden(self):
        """Que los casilleros nunca aparezcan vacíos es la mitad preventiva:
        cargar cinco ejercicios seguidos del día 2 no debería obligar a
        retipear el "2" cada vez."""
        self.client.login(username="staff_a", password="clave12345")
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_a, ejercicio=self.ejercicio_a,
            semana=1, dia=3, orden=2, series=3, repeticiones="10",
        )

        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk])
        )

        initial = response.context["form"].initial
        self.assertEqual(initial["dia"], 3)
        self.assertEqual(initial["orden"], 3)

    def test_el_form_de_una_plantilla_vacia_arranca_en_dia_uno(self):
        self.client.login(username="staff_a", password="clave12345")
        RutinaPlantillaItem.objects.filter(rutina=self.plantilla_a).delete()

        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk])
        )

        initial = response.context["form"].initial
        self.assertEqual(initial["dia"], 1)
        self.assertEqual(initial["orden"], 1)

    def test_dia_nombre_en_blanco_hereda_el_nombre_del_dia(self):
        """Mismo criterio que `services.agregar_ejercicio_asignado`:
        `dia_nombre` está denormalizado por item, y dejar el item nuevo como
        el único sin etiqueta rompe la regla de lectura de `agrupacion.py`
        ("gana la semana más baja")."""
        self.client.login(username="staff_a", password="clave12345")
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla_a, ejercicio=self.ejercicio_a,
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
            dia_nombre="Tren superior",
        )

        self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 1,
                "orden": "", "series": 3, "repeticiones": "15",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        creado = RutinaPlantillaItem.objects.get(
            rutina=self.plantilla_a, dia=1, repeticiones="15"
        )
        self.assertEqual(creado.dia_nombre, "Tren superior")

    def test_los_errores_del_form_de_item_se_ven_como_errores(self):
        """`{{ form.as_p }}` pintaba "Este campo es obligatorio" en negro,
        del mismo tamaño que las ayudas y arriba de la etiqueta -- se leía
        como una instrucción, y por eso el cliente creía haber guardado."""
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]),
            {
                "ejercicio": self.ejercicio_a.pk, "semana": 1, "dia": 1,
                "orden": "", "series": "", "repeticiones": "",
                "kilos": "", "descanso": "", "notas": "",
                "bloque": "", "dia_nombre": "",
            },
        )

        self.assertContains(response, "config-error")

    def test_el_form_de_item_no_queda_boosteado(self):
        """Los dos links que llevan acá ya tenían `hx-boost="false"` por el
        CSS de Tom Select que vive en <head>; al form le faltaba. Con el swap
        boosteado, el camino de ERROR (que vuelve a renderizar esta misma
        pantalla) inicializaba TomSelect dos veces y dejaba el <select> crudo
        visible al lado del buscador."""
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk])
        )
        self.assertContains(response, '<form method="post" novalidate hx-boost="false">')

    def test_la_pantalla_de_item_no_filtra_lenguaje_de_programador(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk])
        )
        self.assertNotContains(response, "dias_por_semana")

    def test_plantilla_detail_muestra_columna_semana(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_a.pk])
        )
        self.assertContains(response, "<th>Semana</th>", html=True)

    def test_asignada_detail_muestra_semana_actual(self):
        """La semana del ciclo se muestra SOLO si la rutina está vigente.

        Antes se rotulaba "Semana actual: N de 4" siempre, y con planes que
        conviven eso mentía en dos casos: "1 de 4" en uno que todavía no
        arrancó (`semana_actual` devuelve 1 cuando la fecha es futura) y
        "4 de 4" para siempre en uno de hace un año. Ahora aparece solo
        cuando hoy cae dentro de sus 4 semanas, y el estado se rotula aparte.
        """
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            plantilla=self.plantilla_a,
            fecha_inicio=timezone.localdate() - timedelta(days=7),
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[asignada.pk])
        )
        self.assertContains(response, "Semana 2 de 4")
        self.assertContains(response, "Vigente")

    def test_asignada_detail_no_muestra_semana_en_una_rutina_terminada(self):
        vieja = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio_a, alumno=self.alumno_a,
            nombre_snapshot="De hace un año", objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate() - timedelta(days=365),
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[vieja.pk])
        )
        self.assertNotContains(response, "Semana 4 de 4")
        self.assertContains(response, "Finalizada")

    def test_asignada_pdf_devuelve_un_pdf_descargable(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:asignada_pdf", args=[self.asignada_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_asignada_pdf_de_otro_gimnasio_da_404(self):
        asignada_b = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio_b,
            alumno=self.alumno_b,
            plantilla=self.plantilla_b,
            fecha_inicio=date(2026, 1, 1),
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:asignada_pdf", args=[asignada_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    # 4. El campo `ejercicio` del form de item solo ofrece ejercicios del
    # propio gimnasio -- el cierre del hueco de FK-injection.
    def test_ejercicio_del_form_de_item_esta_scopeado_al_gimnasio(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk])
        )
        queryset = response.context["form"].fields["ejercicio"].queryset
        self.assertIn(self.ejercicio_a, queryset)
        self.assertNotIn(self.ejercicio_b, queryset)

        # Postear directamente el id de un ejercicio de otro gimnasio: form
        # inválido, no un item creado a medio camino.
        datos = {
            "ejercicio": self.ejercicio_b.pk,
            "dia": 1,
            "orden": 9,
            "series": 3,
            "repeticiones": "10",
            "descanso": "",
            "notas": "",
        }
        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]), datos
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ejercicio", response.context["form"].errors)
        self.assertFalse(
            RutinaPlantillaItem.objects.filter(
                rutina=self.plantilla_a, ejercicio=self.ejercicio_b
            ).exists()
        )

    # 5. Duplicar (POST-only) crea una copia independiente y redirige a ella;
    # GET no está permitido.
    def test_duplicar_via_post_crea_copia_y_redirige(self):
        self.client.login(username="staff_a", password="clave12345")

        url = reverse("rutinas:plantilla_duplicar", args=[self.plantilla_a.pk])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        copia = RutinaPlantilla.objects.get(nombre="Rutina A (copia)")
        self.assertNotEqual(copia.pk, self.plantilla_a.pk)
        self.assertEqual(copia.gimnasio, self.gimnasio_a)
        self.assertEqual(copia.items.count(), 1)
        self.assertIn(
            reverse("rutinas:plantilla_detalle", args=[copia.pk]), response.url
        )

        # Independiente: tocar un item de la copia no afecta al original.
        item_copiado = copia.items.get()
        item_copiado.series = 999
        item_copiado.save()
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.series, 4)

    def test_duplicar_plantilla_de_otro_gimnasio_devuelve_404(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("rutinas:plantilla_duplicar", args=[self.plantilla_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    # 6. Asignación de punta a punta.
    def test_asignar_rutina_end_to_end(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("rutinas:asignar"))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn(self.alumno_a, form.fields["alumno"].queryset)
        self.assertNotIn(self.alumno_a_inactivo, form.fields["alumno"].queryset)
        self.assertNotIn(self.alumno_b, form.fields["alumno"].queryset)
        self.assertIn(self.plantilla_a, form.fields["plantilla"].queryset)
        self.assertNotIn(self.plantilla_a_inactiva, form.fields["plantilla"].queryset)
        self.assertNotIn(self.plantilla_b, form.fields["plantilla"].queryset)

        datos = {
            "alumno": self.alumno_a.pk,
            "plantilla": self.plantilla_a.pk,
            "fecha_inicio": "2026-04-01",
        }
        response = self.client.post(reverse("rutinas:asignar"), datos)
        self.assertEqual(response.status_code, 302)

        nueva_asignada = RutinaAsignada.objects.exclude(pk=self.asignada_a.pk).get(
            alumno=self.alumno_a, fecha_inicio=date(2026, 4, 1)
        )
        self.assertEqual(nueva_asignada.gimnasio, self.gimnasio_a)
        self.assertEqual(nueva_asignada.nombre_snapshot, self.plantilla_a.nombre)
        self.assertEqual(nueva_asignada.items.count(), 1)

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sentadilla A")
        self.assertEqual(response.context["asignada"], nueva_asignada)
        self.assertEqual(response.context["asignada"].fecha_inicio, date(2026, 4, 1))


class RutinaAsignadaItemCalificarViewTests(TestCase):
    """El alumno califica el RPE de un item de su rutina asignada ACTIVA
    (Fase 7: ficha ampliada + RPE). Solo POST, y solo contra items propios de
    una asignación activa -- mismo criterio de "no existe" (404, no 403) que
    `NovedadMarcarLeidaView`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")

        self.usuario_alumno = User.objects.create_user(
            "usuario_alumno", password="clave-123456"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.perfil_alumno = Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil_alumno
        self.alumno.save()

        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Gómez"
        )

        self.asignada_activa = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Rutina activa",
            objetivo_snapshot="Hipertrofia",
            fecha_inicio=date(2026, 1, 1),
            activa=True,
        )
        self.item = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada_activa,
            ejercicio_nombre_snapshot="Sentadilla",
            semana=1,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8-12",
        )

        self.asignada_inactiva = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Rutina vieja",
            objetivo_snapshot="Fuerza",
            fecha_inicio=date(2025, 1, 1),
            fecha_fin=date(2025, 12, 31),
            activa=False,
        )
        self.item_inactivo = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada_inactiva,
            ejercicio_nombre_snapshot="Peso muerto",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="5",
        )

        self.item_de_otro_alumno = RutinaAsignadaItem.objects.create(
            rutina_asignada=RutinaAsignada.objects.create(
                gimnasio=self.gimnasio,
                alumno=self.otro_alumno,
                nombre_snapshot="Rutina de Bruno",
                objetivo_snapshot="Fuerza",
                fecha_inicio=date(2026, 1, 1),
                activa=True,
            ),
            ejercicio_nombre_snapshot="Press banca",
            semana=1,
            dia=1,
            orden=1,
            series=4,
            repeticiones="10",
        )

    def _url(self, item):
        return reverse("rutinas:item_calificar", args=[item.pk])

    def test_anonimo_redirige_a_login(self):
        response = self.client.post(self._url(self.item), {"rpe": "al_limite"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_get_no_esta_permitido(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(self.item))
        self.assertEqual(response.status_code, 405)

    def test_alumno_califica_su_propio_item(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(self._url(self.item), {"rpe": "al_limite"})
        self.assertRedirects(
            response, reverse("rutinas:mi_dia_detalle", args=[self.item.dia])
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.rpe, RutinaAsignadaItem.RPE.AL_LIMITE)

    def test_valor_invalido_no_se_guarda(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(self._url(self.item), {"rpe": "no-es-una-opcion"})
        self.assertRedirects(
            response, reverse("rutinas:mi_dia_detalle", args=[self.item.dia])
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.rpe, "")

    def test_item_de_otro_alumno_da_404(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(
            self._url(self.item_de_otro_alumno), {"rpe": "al_limite"}
        )
        self.assertEqual(response.status_code, 404)

    def test_item_de_rutina_inactiva_da_404(self):
        """Calificar una rutina vieja/cerrada no tiene sentido: el staff ya
        no la está ajustando en base a ese feedback."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(
            self._url(self.item_inactivo), {"rpe": "al_limite"}
        )
        self.assertEqual(response.status_code, 404)


class RutinaMiDiaDetailViewTests(TestCase):
    """El alumno ve, para un día puntual de su rutina activa, las 4 semanas
    del ciclo lado a lado -- foco en agrupación correcta y aislamiento
    (ni de otro día, ni de otro alumno, ni de una rutina inactiva)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

        self.usuario_alumno = User.objects.create_user(
            "usuario_alumno", password="clave-123456"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.perfil_alumno = Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil_alumno
        self.alumno.save()

        # fecha_inicio hace 7 días -> semana_actual == 2.
        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Full Body",
            objetivo_snapshot="General",
            fecha_inicio=timezone.localdate() - timedelta(days=7),
            activa=True,
        )
        self.item_dia1_semana1 = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        self.item_dia1_semana2 = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot="Sentadilla con salto",
            semana=2,
            dia=1,
            orden=1,
            series=3,
            repeticiones="8",
        )
        self.item_dia2 = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot="Press banca",
            semana=1,
            dia=2,
            orden=1,
            series=4,
            repeticiones="8",
        )

    def _url(self, dia):
        return reverse("rutinas:mi_dia_detalle", args=[dia])

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(self._url(1))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_alumno_sin_ficha_vinculada_da_404(self):
        usuario_sin_ficha = User.objects.create_user(
            "sin_ficha", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=usuario_sin_ficha, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="sin_ficha", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertEqual(response.status_code, 404)

    def test_dia_que_no_existe_en_la_rutina_da_404(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(99))
        self.assertEqual(response.status_code, 404)

    def test_muestra_las_4_semanas(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Semana 1")
        self.assertContains(response, "Semana 2")
        self.assertContains(response, "Semana 3")
        self.assertContains(response, "Semana 4")
        self.assertContains(response, "Sentadilla")
        self.assertContains(response, "Sentadilla con salto")

    def test_no_muestra_ejercicios_de_otro_dia(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertNotContains(response, "Press banca")

    def test_semana_sin_fila_para_un_ejercicio_muestra_guion(self):
        """"Sentadilla" solo tiene fila en semana 1 -- las semanas 2-4 de
        esa fila deben mostrar el placeholder "—", no romper."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertContains(response, "—")

    def test_items_sin_categoria_snapshoteado_muestran_display_sin_grupo(self):
        """Los items de este fixture no tienen `categoria_snapshot`
        (creados sin ese valor, como una rutina asignada antes de que el
        campo existiera) -- deben mostrar "Sin categoría" como
        subtítulo en vez de romper la vista."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertContains(response, "Sin categoría")

    def test_muestra_el_grupo_muscular_como_subtitulo_del_ejercicio(self):
        """El grupo muscular ya no agrupa en secciones -- se muestra como
        subtítulo chico debajo de cada nombre de ejercicio."""
        self.item_dia1_semana1.categoria_snapshot = "Piernas"
        self.item_dia1_semana1.save()
        self.client.login(username="usuario_alumno", password="clave-123456")

        response = self.client.get(self._url(1))

        self.assertContains(response, "Piernas")

    def test_ejercicios_de_distinto_grupo_muscular_van_en_una_sola_tabla(self):
        """Regresión: hasta 2026-08-24 cada grupo muscular armaba su propia
        `<tarjeta>` con su propia `<table>` -- un cliente real lo encontró
        confuso y se sacó la subdivisión. "Sentadilla" (piernas) y
        "Sentadilla con salto" (pecho, a propósito para este test) deben
        aparecer juntos en una única tabla, no en dos tarjetas separadas."""
        self.item_dia1_semana1.categoria_snapshot = "Piernas"
        self.item_dia1_semana1.save()
        self.item_dia1_semana2.categoria_snapshot = "Pecho"
        self.item_dia1_semana2.save()
        self.client.login(username="usuario_alumno", password="clave-123456")

        response = self.client.get(self._url(1))

        self.assertContains(response, "Piernas")
        self.assertContains(response, "Pecho")
        self.assertContains(
            response, '<table class="tabla tabla--rutina-semanas">', count=1
        )

    def test_calificar_rpe_muestra_marca_de_hecho(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        self.item_dia1_semana1.rpe = RutinaAsignadaItem.RPE.SEGUIR_INTENSIDAD
        self.item_dia1_semana1.save()
        response = self.client.get(self._url(1))
        self.assertContains(response, "✓ Hecho")

    def test_marca_la_semana_actual(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertContains(response, "Actual")

    def test_no_ofrece_marcar_como_entrenada_una_semana_sin_ejercicios(self):
        """Día 1 solo tiene ejercicios en semana 1 y 2 (ver setUp) -- el
        botón de marcar entrenado no debe ofrecerse para semana 3 ni 4,
        aunque el día en general sí tenga ejercicios (en otras semanas)."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertNotContains(
            response, reverse("rutinas:dia_completado_toggle", args=[1, 3])
        )
        self.assertNotContains(
            response, reverse("rutinas:dia_completado_toggle", args=[1, 4])
        )
        self.assertContains(
            response, reverse("rutinas:dia_completado_toggle", args=[1, 1])
        )
        self.assertContains(response, "Sin ejercicios esta semana")

    def test_muestra_descanso_y_notas_cuando_estan_cargados(self):
        """Descanso es su propia columna (no texto "Descanso: X" apilado
        con lo demás); notas va en un `<details>` -- touch-accessible,
        no un tooltip por `title` que no se ve en celular."""
        self.item_dia1_semana1.descanso = "90s"
        self.item_dia1_semana1.notas = "Cuidado con la zona lumbar"
        self.item_dia1_semana1.save()
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertContains(response, "<td>90s</td>", html=True)
        self.assertContains(response, "<summary>Notas</summary>", html=True)
        self.assertContains(response, "Cuidado con la zona lumbar")
        self.assertNotContains(response, 'title="Cuidado con la zona lumbar"')

    def test_tabla_tiene_columna_propia_por_dato_repetida_por_semana(self):
        """Series/Repeticiones/Kilos/Descanso/Calificación son columnas
        separadas (header de dos niveles), no texto combinado en una
        sola celda -- pedido explícito tras ver la app real."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1))
        self.assertContains(response, "<th scope=\"col\">Series</th>", html=True)
        self.assertContains(response, "<th scope=\"col\">Reps</th>", html=True)
        self.assertContains(response, "<th scope=\"col\">Kilos</th>", html=True)
        self.assertContains(response, "<th scope=\"col\">Descanso</th>", html=True)
        self.assertContains(response, "Calificación")
        self.assertContains(response, "<td>3</td>", html=True)
        self.assertContains(response, "<td>10</td>", html=True)


class RutinaAsignadaDiaCompletadoToggleViewTests(TestCase):
    """El alumno marca/desmarca un día de una semana puntual como
    entrenado -- toggle idempotente, aislado por alumno."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

        self.usuario_alumno = User.objects.create_user(
            "usuario_alumno", password="clave-123456"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.perfil_alumno = Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil_alumno
        self.alumno.save()

        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Full Body",
            objetivo_snapshot="General",
            fecha_inicio=timezone.localdate(),
            activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

    def _url(self, dia, semana):
        return reverse("rutinas:dia_completado_toggle", args=[dia, semana])

    def test_anonimo_redirige_a_login(self):
        response = self.client.post(self._url(1, 1))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_get_no_esta_permitido(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.get(self._url(1, 1))
        self.assertEqual(response.status_code, 405)

    def test_primer_click_marca_como_completado(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(self._url(1, 1))
        self.assertRedirects(response, reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertTrue(
            RutinaAsignadaDiaCompletado.objects.filter(
                rutina_asignada=self.asignada, dia=1, semana=1
            ).exists()
        )

    def test_segundo_click_desmarca(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        self.client.post(self._url(1, 1))
        self.client.post(self._url(1, 1))
        self.assertFalse(
            RutinaAsignadaDiaCompletado.objects.filter(
                rutina_asignada=self.asignada, dia=1, semana=1
            ).exists()
        )

    def test_dia_que_no_existe_en_la_rutina_da_404(self):
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(self._url(99, 1))
        self.assertEqual(response.status_code, 404)

    def test_semana_sin_ejercicios_para_ese_dia_da_404(self):
        """El día 1 solo tiene ejercicios en semana 1 (ver setUp) -- no
        se puede marcar como entrenada la semana 2 de ese mismo día,
        aunque el día "exista" en otra semana."""
        self.client.login(username="usuario_alumno", password="clave-123456")
        response = self.client.post(self._url(1, 2))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            RutinaAsignadaDiaCompletado.objects.filter(
                rutina_asignada=self.asignada, dia=1, semana=2
            ).exists()
        )


# Los tests de `0006_backfill_grupo_muscular_snapshot` se retiraron el
# 2026-08-26, al renombrar el campo a `categoria_snapshot` en
# `0007_categoria_snapshot`.
#
# Ejercitaban la migración llamando a su función con el registro de modelos
# VIVO (`django.apps.apps`), no con el estado histórico. Ese atajo funciona
# mientras el esquema no se mueva: en cuanto el campo se renombra, la función
# sigue siendo correcta cuando corre de verdad (Django le pasa el estado de su
# propio punto en la historia, donde el campo todavía se llama
# `grupo_muscular_snapshot`) pero el test deja de poder invocarla.
#
# Reescribirlos contra `MigrationExecutor` era la alternativa; se descartó
# porque `0006` es historia congelada: ya corrió en producción y en una base
# nueva no encuentra nada que backfillear. Lo que sí se conserva es la
# cobertura de lo que hoy escribe el snapshot, en `CategoriaSnapshotTests`.


class CategoriaSnapshotTests(TestCase):
    """El snapshot pasa a guardar el NOMBRE VISIBLE de la categoría, no un
    slug.

    Antes guardaba `"cuerpo_completo"` y `agrupacion.py` lo traducía con un
    dict module-level armado desde `Ejercicio.GrupoMuscular.choices`. Con un
    catálogo por gimnasio ese dict global deja de ser correcto: dos gimnasios
    pueden tener categorías distintas con el mismo nombre, o nombres que no
    están en ninguna lista fija. Guardando el nombre ya renderizado no hace
    falta ningún lookup, y `agrupacion.py` vuelve a ser Django-free de verdad.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )
        self.ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=self.categoria
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio,
            nombre="Full Body",
            objetivo="General",
            dias_por_semana=1,
        )
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla,
            ejercicio=self.ejercicio,
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )

    def _asignar(self):
        return RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            plantilla=self.plantilla,
            alumno=self.alumno,
            fecha_inicio=timezone.localdate(),
        )

    def test_congela_el_nombre_de_la_categoria(self):
        asignada = self._asignar()

        item = asignada.items.get()
        self.assertEqual(item.categoria_snapshot, "EMPUJE")

    def test_renombrar_la_categoria_no_toca_rutinas_ya_asignadas(self):
        """Es el punto de ser un snapshot: la rutina que el alumno está
        haciendo no cambia porque el staff reordene su biblioteca."""
        asignada = self._asignar()

        self.categoria.nombre = "EMPUJE HORIZONTAL"
        self.categoria.save()

        item = asignada.items.get()
        self.assertEqual(item.categoria_snapshot, "EMPUJE")

    def test_ejercicio_sin_categoria_deja_el_snapshot_vacio(self):
        self.ejercicio.categoria = None
        self.ejercicio.save()

        asignada = self._asignar()

        self.assertEqual(asignada.items.get().categoria_snapshot, "")

    def test_admite_nombres_largos_de_categoria(self):
        """El campo viejo era `max_length=20` porque solo guardaba slugs de
        un catálogo cerrado. Una categoría propia puede ser mucho más larga."""
        self.categoria.nombre = "Movilidad y trabajo de cadera profunda"
        self.categoria.save()

        asignada = self._asignar()

        self.assertEqual(
            asignada.items.get().categoria_snapshot,
            "Movilidad y trabajo de cadera profunda",
        )

    def test_asignar_no_dispara_una_query_por_ejercicio(self):
        """`crear_desde_plantilla` leía `item.ejercicio` dentro del
        `bulk_create` sin `select_related`: un N+1 que ya existía antes de
        esta feature y que leer además `categoria` habría duplicado.

        Se compara el costo de dos tamaños de plantilla en vez de fijar un
        `assertNumQueries` absoluto -- ese número depende de detalles internos
        de Django y se rompe sin que haya ninguna regresión real (mismo
        criterio que el test de `select_related` del panel de accesos).
        """
        with CaptureQueriesContext(connection) as con_un_item:
            self._asignar()

        for i in range(2, 12):
            RutinaPlantillaItem.objects.create(
                rutina=self.plantilla,
                ejercicio=self.ejercicio,
                semana=1,
                dia=1,
                orden=i,
                series=3,
                repeticiones="10",
            )

        with CaptureQueriesContext(connection) as con_once_items:
            self._asignar()

        self.assertEqual(len(con_once_items), len(con_un_item))


class ConversionSnapshotSlugANombreTests(TestCase):
    """`0007_categoria_snapshot`: los snapshots ya guardados traen el valor
    del catálogo viejo (`"cuerpo_completo"`) y tienen que pasar al nombre
    visible (`"Cuerpo completo"`).

    Es la parte de la migración con datos reales de por medio: si falla, las
    rutinas que los alumnos están haciendo hoy pierden el subtítulo de
    categoría en el portal y en el PDF.

    A diferencia de los tests retirados de `0006`, estos SÍ pueden llamar la
    función con el registro vivo: opera sobre `categoria_snapshot`, que es el
    nombre que el campo tiene después de esta misma migración.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez"
        )
        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Full Body",
            objetivo_snapshot="General",
            fecha_inicio=timezone.localdate(),
        )
        self.migracion = self._modulo()

    @staticmethod
    def _modulo():
        import importlib

        return importlib.import_module("rutinas.migrations.0007_categoria_snapshot")

    def _item(self, snapshot, orden=1):
        return RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot=f"Ejercicio {orden}",
            categoria_snapshot=snapshot,
            semana=1,
            dia=1,
            orden=orden,
            series=3,
        )

    def test_convierte_los_ocho_valores_del_catalogo_viejo(self):
        from django.apps import apps

        esperado = {
            "pecho": "Pecho",
            "espalda": "Espalda",
            "piernas": "Piernas",
            "hombros": "Hombros",
            "brazos": "Brazos",
            "core": "Core",
            "cardio": "Cardio",
            "cuerpo_completo": "Cuerpo completo",
        }
        items = {
            slug: self._item(slug, orden=i)
            for i, slug in enumerate(esperado, start=1)
        }

        self.migracion.slug_a_nombre(apps, None)

        for slug, item in items.items():
            item.refresh_from_db()
            self.assertEqual(item.categoria_snapshot, esperado[slug])

    def test_deja_en_paz_el_snapshot_vacio(self):
        from django.apps import apps

        item = self._item("")

        self.migracion.slug_a_nombre(apps, None)

        item.refresh_from_db()
        self.assertEqual(item.categoria_snapshot, "")

    def test_la_vuelta_atras_reconstruye_los_slugs(self):
        from django.apps import apps

        item = self._item("cuerpo_completo")
        self.migracion.slug_a_nombre(apps, None)

        self.migracion.nombre_a_slug(apps, None)

        item.refresh_from_db()
        self.assertEqual(item.categoria_snapshot, "cuerpo_completo")

    def test_la_vuelta_atras_vacia_las_categorias_propias(self):
        """"EMPUJE" no existe en el catálogo viejo: no hay slug al cual
        mapearla. Se deja en blanco, que es un valor que `agrupacion.py` ya
        sabe bucketear, en vez de dejar basura que no valida."""
        from django.apps import apps

        item = self._item("EMPUJE")

        self.migracion.nombre_a_slug(apps, None)

        item.refresh_from_db()
        self.assertEqual(item.categoria_snapshot, "")

    def test_es_idempotente(self):
        from django.apps import apps

        item = self._item("piernas")

        self.migracion.slug_a_nombre(apps, None)
        self.migracion.slug_a_nombre(apps, None)

        item.refresh_from_db()
        self.assertEqual(item.categoria_snapshot, "Piernas")


class BloqueYNombreDeDiaTests(RutinasTestCase):
    """Los dos campos que trae el importador desde la planilla del entrenador:
    el código de superserie (A1, B2 -- los ejercicios del mismo bloque se hacen
    juntos) y el nombre del día ("Tren superior · Core").

    Van denormalizados por item, igual que `categoria_snapshot`, y se resuelven
    al leer con la regla "gana la semana más baja".
    """

    def _plantilla_con_bloques(self):
        plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Full body", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        for semana in (1, 2):
            RutinaPlantillaItem.objects.create(
                rutina=plantilla, ejercicio=self.press_banca, semana=semana,
                dia=1, orden=1, series=4, repeticiones="10",
                bloque="A1", dia_nombre="Tren superior · Core",
            )
            RutinaPlantillaItem.objects.create(
                rutina=plantilla, ejercicio=self.sentadilla, semana=semana,
                dia=1, orden=2, series=4, repeticiones="12",
                bloque="A2", dia_nombre="Tren superior · Core",
            )
        return plantilla

    def test_sobreviven_a_crear_desde_plantilla(self):
        plantilla = self._plantilla_con_bloques()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        item = asignada.items.get(semana=1, orden=1)
        self.assertEqual(item.bloque, "A1")
        self.assertEqual(item.dia_nombre, "Tren superior · Core")

    def test_sobreviven_a_duplicar(self):
        copia = self._plantilla_con_bloques().duplicar()
        item = copia.items.get(semana=1, orden=2)
        self.assertEqual(item.bloque, "A2")
        self.assertEqual(item.dia_nombre, "Tren superior · Core")

    def test_agrupacion_los_expone_por_la_semana_mas_baja(self):
        plantilla = self._plantilla_con_bloques()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        # Si el valor se tomara de cualquier semana, este cambio en la 2 se
        # colaría; tiene que ganar la más baja, igual que `categoria_display`.
        asignada.items.filter(semana=2, orden=1).update(bloque="ZZ")

        ejercicios = listar_ejercicios_del_dia(list(asignada.items.all()))
        primero = ejercicios[0]
        self.assertEqual(primero["bloque"], "A1")
        self.assertEqual(primero["dia_nombre"], "Tren superior · Core")

    def test_un_item_sin_bloque_no_rompe_nada(self):
        """La carga manual y todas las rutinas anteriores a esta feature no
        tienen bloque: el campo es opcional."""
        plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Manual", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        RutinaPlantillaItem.objects.create(
            rutina=plantilla, ejercicio=self.press_banca, semana=1, dia=1,
            orden=1, series=4, repeticiones="10",
        )
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        ejercicios = listar_ejercicios_del_dia(list(asignada.items.all()))
        self.assertEqual(ejercicios[0]["bloque"], "")
        self.assertEqual(ejercicios[0]["dia_nombre"], "")

    def test_el_orden_no_cambia_por_el_bloque(self):
        """`Meta.ordering` sigue siendo semana/día/orden: el importador ya
        asigna `orden` en el orden del archivo, así que A1, A2, B1 salen
        agrupados solos. Meter `bloque` en el ordering mandaría los items
        manuales (bloque vacío) al principio."""
        self.assertEqual(
            RutinaPlantillaItem._meta.ordering, ["semana", "dia", "orden"]
        )


class BloqueYNombreDeDiaEnLaUITests(RutinasTestCase):
    """Que los campos existan en el modelo no sirve si no se ven. Estos tests
    son el guardarraíl contra agregar una columna al `<thead>` y olvidarse del
    `<tbody>` (o al revés)."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Full body", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.press_banca, semana=1, dia=1,
            orden=1, series=4, repeticiones="10",
            bloque="A1", dia_nombre="Tren superior · Core",
        )

    def test_la_tabla_de_la_plantilla_los_muestra(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla.pk])
        )
        self.assertContains(response, "<th>Bloque</th>", html=False)
        self.assertContains(response, "A1")
        self.assertContains(response, "Tren superior")

    def test_la_tabla_de_la_rutina_asignada_tambien(self):
        """Antes exigía una columna `<th>Bloque</th>`, que era como se veía en
        la tabla PLANA de la rutina asignada (una fila por ejercicio y por
        semana).

        Esa tabla se reagrupó el 2026-08-31 al sumarle la edición: ahora es un
        día por tarjeta y un ejercicio por fila, con las 4 semanas en
        columnas, igual que la que ya veía el alumno. El bloque pasó a ser un
        badge al lado del nombre -- mismo tratamiento que `mi_dia_detalle.html`
        --, así que la columna dejó de existir a propósito. Lo que este test
        tiene que seguir garantizando es que el dato SE VE, no en qué elemento
        vive; la tabla de PLANTILLA no cambió y su test sigue exigiendo la
        columna.
        """
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[asignada.pk])
        )
        self.assertContains(response, '<span class="badge">A1</span>', html=False)
        self.assertContains(response, "Tren superior")

    def test_el_portal_del_alumno_titula_el_dia(self):
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        usuario = User.objects.create_user("alu", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = perfil
        self.alumno.save()
        self.client.login(username="alu", password="clave-123456")

        response = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertEqual(response.context["dia_nombre"], "Tren superior · Core")
        self.assertContains(response, "Tren superior · Core")
        self.assertContains(response, "A1")
        self.assertEqual(asignada.items.count(), 1)

    def test_el_pdf_lleva_el_bloque_y_el_nombre_del_dia(self):
        """Regla de sincronía de CLAUDE.md: el papel y la pantalla tienen que
        decir lo mismo."""
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        ejercicios = listar_ejercicios_del_dia(list(asignada.items.all()))
        fila = _fila_ejercicio(ejercicios[0])

        self.assertTrue(fila[0].startswith("A1 · "))
        self.assertIn("Press de banca", fila[0])
        self.assertIsNotNone(generar_pdf_rutina_asignada(asignada))


class AnchoDeCamposSnapshotTests(SimpleTestCase):
    """El snapshot nunca puede ser más angosto que el campo que copia.

    `RutinaAsignada.crear_desde_plantilla` copia texto de `Ejercicio` y de
    `RutinaPlantilla[Item]` a columnas propias. Si el campo ORIGEN se
    ensancha y el snapshot no, el `bulk_create` de la asignación revienta
    con `DataError: value too long` en Postgres -- y **el test local no lo
    ve**: SQLite no valida el largo de un `varchar` (misma familia de
    trampa que `select_for_update()` siendo no-op, ver CLAUDE.md).

    Pasó de verdad: `Ejercicio.url_video` se ensanchó a 500 el 2026-08-27
    (`ejercicios/0004`, por links de 306 caracteres del Excel de un
    cliente) y `ejercicio_video_snapshot` quedó en el default de 200 de
    `URLField` -- asignarle a un alumno un plan que usara uno de esos
    ejercicios daba 500 en producción.

    Por eso el test compara METADATOS y no comportamiento: es la única
    forma de que falle en la suite local.
    """

    # (modelo origen, campo origen) -> (modelo snapshot, campo snapshot)
    PARES = [
        ((Ejercicio, "nombre"), (RutinaAsignadaItem, "ejercicio_nombre_snapshot")),
        ((Ejercicio, "url_video"), (RutinaAsignadaItem, "ejercicio_video_snapshot")),
        ((CategoriaEjercicio, "nombre"), (RutinaAsignadaItem, "categoria_snapshot")),
        ((RutinaPlantilla, "nombre"), (RutinaAsignada, "nombre_snapshot")),
        ((RutinaPlantilla, "objetivo"), (RutinaAsignada, "objetivo_snapshot")),
        ((RutinaPlantillaItem, "repeticiones"), (RutinaAsignadaItem, "repeticiones")),
        ((RutinaPlantillaItem, "kilos"), (RutinaAsignadaItem, "kilos")),
        ((RutinaPlantillaItem, "descanso"), (RutinaAsignadaItem, "descanso")),
        ((RutinaPlantillaItem, "bloque"), (RutinaAsignadaItem, "bloque")),
        ((RutinaPlantillaItem, "dia_nombre"), (RutinaAsignadaItem, "dia_nombre")),
    ]

    def test_cada_campo_del_snapshot_entra_lo_que_copia(self):
        for (modelo_origen, campo_origen), (modelo_snap, campo_snap) in self.PARES:
            with self.subTest(origen=campo_origen, snapshot=campo_snap):
                largo_origen = modelo_origen._meta.get_field(campo_origen).max_length
                largo_snap = modelo_snap._meta.get_field(campo_snap).max_length

                self.assertGreaterEqual(
                    largo_snap,
                    largo_origen,
                    f"{modelo_snap.__name__}.{campo_snap} (max_length="
                    f"{largo_snap}) es más angosto que "
                    f"{modelo_origen.__name__}.{campo_origen} (max_length="
                    f"{largo_origen}): asignar una rutina con un valor largo "
                    f"va a dar DataError en Postgres.",
                )


class SenalDeCargaTests(SimpleTestCase):
    """El mapeo RPE -> señal de carga. Puro, sin base.

    Es lo que convierte el feedback del alumno en algo accionable para el
    entrenador. Sin cálculo ni inferencia: un dict de 4 entradas.
    """

    def test_las_cuatro_choices_tienen_senal(self):
        """Guardarraíl: si mañana se agrega un 5to nivel de RPE, este test
        falla en vez de renderizar un hueco silencioso en la pantalla."""
        for valor, _etiqueta in RutinaAsignadaItem.RPE.choices:
            with self.subTest(rpe=valor):
                self.assertIsNotNone(progreso.senal_de_carga(valor))

    def test_sin_calificar_no_tiene_senal(self):
        self.assertIsNone(progreso.senal_de_carga(""))

    def test_valor_fuera_de_catalogo_no_rompe(self):
        """Defensivo: un dato viejo o una choice eliminada no debe voltear la
        pantalla del staff."""
        self.assertIsNone(progreso.senal_de_carga("chirimbolo"))

    def test_mas_intenso_sube_y_bajar_intensidad_baja(self):
        subir = progreso.senal_de_carga(RutinaAsignadaItem.RPE.MAS_INTENSO)
        bajar = progreso.senal_de_carga(RutinaAsignadaItem.RPE.BAJAR_INTENSIDAD)
        self.assertEqual(subir.flecha, "↑")
        self.assertIn("Subir", subir.accion)
        self.assertEqual(bajar.flecha, "↓")
        self.assertIn("Bajar", bajar.accion)

    def test_al_limite_no_dice_subir(self):
        """Fija la decisión de producto: "Estoy al límite" describe haber
        llegado al tope buscado, no haberse pasado -> mantener, no bajar, y
        nunca subir."""
        senal = progreso.senal_de_carga(RutinaAsignadaItem.RPE.AL_LIMITE)
        self.assertEqual(senal.flecha, "=")
        self.assertNotIn("Subir", senal.accion)

    def test_la_flecha_no_es_el_unico_portador_del_significado(self):
        """Accesibilidad: nada codificado solo por color ni solo por símbolo.
        Cada señal trae también su texto."""
        for valor, _ in RutinaAsignadaItem.RPE.choices:
            with self.subTest(rpe=valor):
                self.assertTrue(progreso.senal_de_carga(valor).accion.strip())

    def test_anotar_senales_tolera_semanas_sin_item(self):
        ejercicios = [{"semanas": [{"numero": 1, "item": None}]}]
        anotados = progreso.anotar_senales(ejercicios)
        self.assertIsNone(anotados[0]["semanas"][0]["senal"])


class AdherenciaTests(RutinasTestCase):
    """Cuántas sesiones entrenó el alumno sobre las que le tocaban.

    `RutinaAsignadaDiaCompletado` existía desde antes pero NO se leía en
    ninguna vista de staff: el alumno marcaba cada día como entrenado y ese
    dato no llegaba a ningún lado.
    """

    def setUp(self):
        super().setUp()
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Full body", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )

    def _asignada_con(self, pares, fecha_inicio=None):
        """`pares`: [(dia, semana), ...] a materializar como items."""
        asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="Full body", objetivo_snapshot="Fuerza",
            fecha_inicio=fecha_inicio or timezone.localdate(),
        )
        RutinaAsignadaItem.objects.bulk_create([
            RutinaAsignadaItem(
                rutina_asignada=asignada, ejercicio_nombre_snapshot="Press",
                semana=semana, dia=dia, orden=1, series=3, repeticiones="10",
            )
            for dia, semana in pares
        ])
        return asignada

    def test_una_sesion_es_un_dia_de_una_semana_no_un_ejercicio(self):
        asignada = self._asignada_con([(1, 1)])
        RutinaAsignadaItem.objects.bulk_create([
            RutinaAsignadaItem(
                rutina_asignada=asignada, ejercicio_nombre_snapshot=f"Otro {n}",
                semana=1, dia=1, orden=n + 2, series=3, repeticiones="10",
            )
            for n in range(4)
        ])
        self.assertEqual(progreso.adherencia_de_rutina(asignada).previstas, 1)

    def test_solo_cuenta_los_dias_semana_que_tienen_items(self):
        asignada = self._asignada_con([(1, 1), (1, 2)])
        self.assertEqual(progreso.adherencia_de_rutina(asignada).previstas, 2)

    def test_un_dia_completado_sin_items_no_infla_la_adherencia(self):
        """Pasa cuando el staff quita el último ejercicio de un día: la fila
        de "entrenado" queda a propósito (el alumno sí entrenó), pero no debe
        contar sobre una sesión que ya no existe."""
        asignada = self._asignada_con([(1, 1)])
        RutinaAsignadaDiaCompletado.objects.create(
            rutina_asignada=asignada, dia=9, semana=1
        )
        adherencia = progreso.adherencia_de_rutina(asignada)
        self.assertEqual(adherencia.previstas, 1)
        self.assertEqual(adherencia.entrenadas, 0)

    def test_sin_sesiones_previstas_no_divide_por_cero(self):
        asignada = self._asignada_con([])
        self.assertEqual(progreso.adherencia_de_rutina(asignada).porcentaje, 0)

    def test_hasta_hoy_se_acota_a_la_semana_actual(self):
        """En la semana 2 de 4, la adherencia sobre el ciclo completo no puede
        pasar del 50% aunque el alumno haya venido a todo -- leerla así sería
        acusarlo de algo que todavía no pasó."""
        asignada = self._asignada_con(
            [(1, 1), (1, 2), (1, 3), (1, 4)],
            fecha_inicio=timezone.localdate() - timedelta(days=7),
        )
        self.assertEqual(asignada.semana_actual, 2)
        for semana in (1, 2):
            RutinaAsignadaDiaCompletado.objects.create(
                rutina_asignada=asignada, dia=1, semana=semana
            )
        adherencia = progreso.adherencia_de_rutina(asignada)
        self.assertEqual(adherencia.porcentaje_hasta_hoy, 100)
        self.assertEqual(adherencia.porcentaje, 50)

    def test_por_semana_siempre_tiene_cuatro_entradas(self):
        asignada = self._asignada_con([(1, 1)])
        adherencia = progreso.adherencia_de_rutina(asignada)
        self.assertEqual(len(adherencia.por_semana), 4)
        self.assertTrue(adherencia.por_semana[0].es_actual)

    def test_no_hace_una_query_por_item(self):
        chica = self._asignada_con([(1, 1)])
        grande = self._asignada_con([(d, s) for d in range(1, 5) for s in range(1, 5)])
        with CaptureQueriesContext(connection) as pocas:
            progreso.adherencia_de_rutina(chica)
        with CaptureQueriesContext(connection) as muchas:
            progreso.adherencia_de_rutina(grande)
        self.assertEqual(len(pocas), len(muchas))

    def test_no_mezcla_los_dias_completados_de_otra_rutina(self):
        """Garantía de que solo se lee vía `asignada.items` /
        `asignada.dias_completados`, nunca por el manager global."""
        propia = self._asignada_con([(1, 1)])
        ajena = self._asignada_con([(1, 1)])
        RutinaAsignadaDiaCompletado.objects.create(
            rutina_asignada=ajena, dia=1, semana=1
        )
        self.assertEqual(progreso.adherencia_de_rutina(propia).entrenadas, 0)
        self.assertEqual(progreso.adherencia_de_rutina(ajena).entrenadas, 1)


class EditarRutinaAsignadaServiceTests(RutinasTestCase):
    """La regla de propagación entre semanas, que es el corazón de
    `rutinas/services.py`.

    Nombre y video van a las 4 semanas; series/kilos/etc. solo a la editada.
    El nombre propaga por INTEGRIDAD: `agrupacion.py` identifica "el mismo
    ejercicio entre semanas" por `ejercicio_nombre_snapshot`, así que
    renombrar una sola semana partiría el ejercicio en dos filas distintas en
    el portal del alumno y en el PDF.
    """

    def setUp(self):
        super().setUp()
        # Un tercero, con categoría, para probar que "agregar" copia bien el
        # snapshot (el fixture base solo trae dos y sin categoría).
        self.remo = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Remo con barra",
            categoria=CategoriaEjercicio.objects.create(
                gimnasio=self.gimnasio, nombre="Tracción"
            ),
            url_video="https://youtube.com/watch?v=remo",
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Full body", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=2,
        )
        for dia in (1, 2):
            for semana in range(1, 5):
                for orden, ejercicio in enumerate(
                    (self.press_banca, self.sentadilla), start=1
                ):
                    RutinaPlantillaItem.objects.create(
                        rutina=self.plantilla, ejercicio=ejercicio, semana=semana,
                        dia=dia, orden=orden, series=3, repeticiones="10",
                        kilos="20kg", dia_nombre="Tren superior",
                    )
        self.asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=timezone.localdate(),
        )
        self.item = self.asignada.items.get(
            dia=1, semana=2, ejercicio_nombre_snapshot=self.press_banca.nombre
        )

    def _editar(self, **overrides):
        datos = {
            "ejercicio_nombre": self.item.ejercicio_nombre_snapshot,
            "ejercicio_video": self.item.ejercicio_video_snapshot,
            "series": self.item.series,
            "repeticiones": self.item.repeticiones,
            "kilos": self.item.kilos,
            "descanso": self.item.descanso,
            "notas": self.item.notas,
            "bloque": self.item.bloque,
        }
        datos.update(overrides)
        return services.editar_ejercicio_asignado(
            asignada=self.asignada, item=self.item, **datos
        )

    def _nombres_del_dia(self, dia=1):
        return sorted(
            self.asignada.items.filter(dia=dia).values_list(
                "ejercicio_nombre_snapshot", flat=True
            )
        )

    # ---- el nombre y el video propagan a las 4 semanas ----

    def test_renombrar_afecta_las_cuatro_semanas(self):
        self._editar(ejercicio_nombre="Press inclinado")
        self.assertEqual(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot="Press inclinado"
            ).count(),
            4,
        )

    def test_renombrar_no_toca_el_otro_ejercicio_del_mismo_dia(self):
        self._editar(ejercicio_nombre="Press inclinado")
        self.assertEqual(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.sentadilla.nombre
            ).count(),
            4,
        )

    def test_renombrar_no_toca_el_mismo_ejercicio_en_otro_dia(self):
        """`dia` es parte de la clave de hermanos: sin él, renombrar en el día
        1 renombraría también el día 2."""
        self._editar(ejercicio_nombre="Press inclinado")
        self.assertEqual(
            self.asignada.items.filter(
                dia=2, ejercicio_nombre_snapshot=self.press_banca.nombre
            ).count(),
            4,
        )

    def test_renombrar_no_toca_otra_rutina_del_mismo_alumno(self):
        """`rutina_asignada` es parte de la clave: sin él, editar la rutina
        nueva reescribiría el historial del alumno."""
        otra = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=timezone.localdate(),
        )
        self._editar(ejercicio_nombre="Press inclinado")
        self.assertEqual(
            otra.items.filter(
                ejercicio_nombre_snapshot=self.press_banca.nombre
            ).count(),
            8,
        )

    def test_el_video_tambien_propaga_a_las_cuatro_semanas(self):
        self._editar(ejercicio_video="https://youtube.com/watch?v=nuevo")
        videos = set(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre
            ).values_list("ejercicio_video_snapshot", flat=True)
        )
        self.assertEqual(videos, {"https://youtube.com/watch?v=nuevo"})

    def test_vaciar_el_video_lo_vacia_en_las_cuatro_semanas(self):
        self._editar(ejercicio_video="")
        videos = set(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre
            ).values_list("ejercicio_video_snapshot", flat=True)
        )
        self.assertEqual(videos, {""})

    def test_renombrar_no_toca_el_rpe_que_cargo_el_alumno(self):
        hermano = self.asignada.items.get(
            dia=1, semana=3, ejercicio_nombre_snapshot=self.press_banca.nombre
        )
        hermano.rpe = RutinaAsignadaItem.RPE.AL_LIMITE
        hermano.save(update_fields=["rpe"])
        self._editar(ejercicio_nombre="Press inclinado")
        hermano.refresh_from_db()
        self.assertEqual(hermano.rpe, RutinaAsignadaItem.RPE.AL_LIMITE)

    def test_renombrar_no_toca_la_plantilla_original(self):
        """El snapshot sigue siendo snapshot: editar la copia del alumno nunca
        vuelve hacia el molde."""
        self._editar(ejercicio_nombre="Press inclinado")
        self.assertTrue(
            self.plantilla.items.filter(ejercicio=self.press_banca).exists()
        )

    def test_modificado_se_actualiza_pese_al_queryset_update(self):
        """`QuerySet.update()` NO dispara `auto_now`: sin pasarlo explícito, el
        campo de auditoría de `TimeStampedModel` quedaría mintiendo."""
        hermano = self.asignada.items.get(
            dia=1, semana=4, ejercicio_nombre_snapshot=self.press_banca.nombre
        )
        antes = hermano.modificado
        self._editar(ejercicio_nombre="Press inclinado")
        hermano.refresh_from_db()
        self.assertGreater(hermano.modificado, antes)

    # ---- los campos de la semana NO propagan ----

    def test_series_y_kilos_cambian_solo_en_la_semana_editada(self):
        self._editar(series=9, kilos="99kg")
        editado = self.asignada.items.get(pk=self.item.pk)
        self.assertEqual(editado.series, 9)
        self.assertEqual(editado.kilos, "99kg")
        otras = self.asignada.items.filter(
            dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre
        ).exclude(pk=self.item.pk)
        self.assertEqual(set(otras.values_list("series", flat=True)), {3})
        self.assertEqual(set(otras.values_list("kilos", flat=True)), {"20kg"})

    def test_notas_y_bloque_cambian_solo_en_la_semana_editada(self):
        self._editar(notas="cuidar el hombro", bloque="B2")
        otras = self.asignada.items.filter(
            dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre
        ).exclude(pk=self.item.pk)
        self.assertEqual(set(otras.values_list("notas", flat=True)), {""})
        self.assertEqual(set(otras.values_list("bloque", flat=True)), {""})

    # ---- duplicados ----

    def test_renombrar_a_un_nombre_ya_usado_en_el_dia_se_rechaza(self):
        with self.assertRaises(services.NombreDuplicadoEnElDia):
            self._editar(ejercicio_nombre=self.sentadilla.nombre)

    def test_renombrar_a_un_nombre_usado_en_otro_dia_esta_permitido(self):
        """Los días son independientes: el mismo ejercicio puede estar en
        varios días."""
        otro = self.asignada.items.get(
            dia=2, semana=1, ejercicio_nombre_snapshot=self.sentadilla.nombre
        )
        services.editar_ejercicio_asignado(
            asignada=self.asignada, item=otro,
            ejercicio_nombre="Peso muerto", ejercicio_video="",
            series=3, repeticiones="10",
        )
        self.assertIn("Peso muerto", self._nombres_del_dia(dia=2))

    def test_guardar_sin_cambiar_el_nombre_no_es_duplicado(self):
        self._editar(series=5)
        self.assertEqual(self.asignada.items.get(pk=self.item.pk).series, 5)

    def test_cambiar_solo_mayusculas_del_propio_nombre_esta_permitido(self):
        self._editar(ejercicio_nombre=self.press_banca.nombre.upper())
        self.assertEqual(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre.upper()
            ).count(),
            4,
        )

    def test_un_nombre_que_solo_difiere_en_mayusculas_de_otro_se_rechaza(self):
        """`iexact`: no se fusionarían en `agrupacion.py` (que compara exacto),
        pero son el mismo error del entrenador y dan dos filas casi idénticas
        en el portal del alumno."""
        with self.assertRaises(services.NombreDuplicadoEnElDia):
            self._editar(ejercicio_nombre=self.sentadilla.nombre.upper())

    def test_el_duplicado_no_deja_nada_escrito(self):
        with self.assertRaises(services.NombreDuplicadoEnElDia):
            self._editar(ejercicio_nombre=self.sentadilla.nombre, series=99)
        self.item.refresh_from_db()
        self.assertEqual(self.item.series, 3)
        self.assertEqual(self.item.ejercicio_nombre_snapshot, self.press_banca.nombre)

    # ---- agregar ----

    def test_agregar_crea_una_fila_por_semana_del_dia(self):
        creados = services.agregar_ejercicio_asignado(
            asignada=self.asignada, dia=1, ejercicio=self.remo,
            series=3, repeticiones="12",
        )
        self.assertEqual(len(creados), 4)
        self.assertEqual(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.remo.nombre
            ).count(),
            4,
        )

    def test_agregar_copia_nombre_video_y_categoria_al_snapshot(self):
        services.agregar_ejercicio_asignado(
            asignada=self.asignada, dia=1, ejercicio=self.remo,
            series=3, repeticiones="12",
        )
        nuevo = self.asignada.items.filter(
            dia=1, ejercicio_nombre_snapshot=self.remo.nombre
        ).first()
        self.assertEqual(nuevo.ejercicio_video_snapshot, self.remo.url_video)
        self.assertEqual(
            nuevo.categoria_snapshot,
            self.remo.categoria.nombre if self.remo.categoria_id else "",
        )

    def test_agregar_usa_el_mismo_orden_en_todas_las_semanas(self):
        """`agrupacion.py` toma el `orden` de la semana más baja: órdenes
        distintos entre semanas darían una posición que no corresponde a
        ninguna semana en particular."""
        services.agregar_ejercicio_asignado(
            asignada=self.asignada, dia=1, ejercicio=self.remo,
            series=3, repeticiones="12",
        )
        ordenes = set(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.remo.nombre
            ).values_list("orden", flat=True)
        )
        self.assertEqual(ordenes, {3})

    def test_agregar_hereda_el_dia_nombre_del_dia(self):
        services.agregar_ejercicio_asignado(
            asignada=self.asignada, dia=1, ejercicio=self.remo,
            series=3, repeticiones="12",
        )
        nuevo = self.asignada.items.filter(
            dia=1, ejercicio_nombre_snapshot=self.remo.nombre
        ).first()
        self.assertEqual(nuevo.dia_nombre, "Tren superior")

    def test_agregar_en_un_dia_de_tres_semanas_no_inventa_la_cuarta(self):
        """Crear la semana que la planilla no tenía inventaría una sesión y
        ensuciaría el denominador de la adherencia."""
        self.asignada.items.filter(dia=1, semana=4).delete()
        creados = services.agregar_ejercicio_asignado(
            asignada=self.asignada, dia=1, ejercicio=self.remo,
            series=3, repeticiones="12",
        )
        self.assertEqual(len(creados), 3)
        self.assertFalse(self.asignada.items.filter(dia=1, semana=4).exists())

    def test_agregar_un_nombre_ya_presente_en_el_dia_se_rechaza(self):
        with self.assertRaises(services.NombreDuplicadoEnElDia):
            services.agregar_ejercicio_asignado(
                asignada=self.asignada, dia=1, ejercicio=self.press_banca,
                series=3, repeticiones="12",
            )

    def test_agregar_en_un_dia_sin_items_levanta_dia_inexistente(self):
        with self.assertRaises(services.DiaInexistente):
            services.agregar_ejercicio_asignado(
                asignada=self.asignada, dia=9, ejercicio=self.remo,
                series=3, repeticiones="12",
            )

    def test_agregar_no_dispara_una_query_por_semana(self):
        self.asignada.items.filter(dia=2).exclude(semana=1).delete()
        with CaptureQueriesContext(connection) as una_semana:
            services.agregar_ejercicio_asignado(
                asignada=self.asignada, dia=2, ejercicio=self.remo,
                series=3, repeticiones="12",
            )
        with CaptureQueriesContext(connection) as cuatro_semanas:
            services.agregar_ejercicio_asignado(
                asignada=self.asignada, dia=1, ejercicio=self.remo,
                series=3, repeticiones="12",
            )
        self.assertEqual(len(una_semana), len(cuatro_semanas))

    # ---- quitar ----

    def test_quitar_borra_las_cuatro_semanas(self):
        borradas = services.quitar_ejercicio_asignado(
            asignada=self.asignada, item=self.item
        )
        self.assertEqual(borradas, 4)
        self.assertFalse(
            self.asignada.items.filter(
                dia=1, ejercicio_nombre_snapshot=self.press_banca.nombre
            ).exists()
        )

    def test_quitar_no_toca_el_mismo_ejercicio_en_otro_dia(self):
        services.quitar_ejercicio_asignado(asignada=self.asignada, item=self.item)
        self.assertEqual(
            self.asignada.items.filter(
                dia=2, ejercicio_nombre_snapshot=self.press_banca.nombre
            ).count(),
            4,
        )

    # ---- integración con lo que lee aguas abajo ----

    def test_renombrar_no_parte_el_ejercicio_en_dos_filas(self):
        """El motivo entero de que el nombre propague."""
        antes = len(listar_ejercicios_del_dia(self.asignada.items.filter(dia=1)))
        self._editar(ejercicio_nombre="Press inclinado")
        despues = len(listar_ejercicios_del_dia(self.asignada.items.filter(dia=1)))
        self.assertEqual(antes, despues)

    def test_el_pdf_sigue_saliendo_despues_de_editar(self):
        self._editar(ejercicio_nombre="Press inclinado", kilos="40kg")
        self.assertIsNotNone(generar_pdf_rutina_asignada(self.asignada))


class RutinaAsignadaItemViewsTests(RutinasTestCase):
    """Las tres vistas de edición del snapshot: permisos, aislamiento de
    tenant y flujo de punta a punta.

    El aislamiento no lo da `TenantScopedMixin` (los items no son
    `TenantOwnedModel`): lo da `ItemAsignadaMixin` al resolver primero la
    `RutinaAsignada` padre con `for_gimnasio()`. Mismo mecanismo que
    `ItemPlantillaMixin`, y por eso se testea el mismo par de casos.
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Full body", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        for semana in range(1, 5):
            RutinaPlantillaItem.objects.create(
                rutina=self.plantilla, ejercicio=self.press_banca, semana=semana,
                dia=1, orden=1, series=3, repeticiones="10",
            )
        self.asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=timezone.localdate(),
        )
        self.item = self.asignada.items.get(dia=1, semana=1)

        # Un segundo gimnasio completo, para los dos casos de aislamiento.
        self.otro_gim = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.otro_gim, nombre="Beto", apellido="Gómez"
        )
        self.otra_asignada = RutinaAsignada.objects.create(
            gimnasio=self.otro_gim, alumno=self.otro_alumno,
            nombre_snapshot="Ajena", objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate(),
        )
        self.item_ajeno = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.otra_asignada,
            ejercicio_nombre_snapshot="Ajeno", semana=1, dia=1, orden=1,
            series=3, repeticiones="10",
        )

    def _urls(self):
        return {
            "editar": reverse(
                "rutinas:asignada_item_editar", args=[self.asignada.pk, self.item.pk]
            ),
            "eliminar": reverse(
                "rutinas:asignada_item_eliminar", args=[self.asignada.pk, self.item.pk]
            ),
            "crear": reverse(
                "rutinas:asignada_item_crear", args=[self.asignada.pk, 1]
            ),
        }

    def _datos_edicion(self, **overrides):
        datos = {
            "ejercicio_nombre_snapshot": self.item.ejercicio_nombre_snapshot,
            "ejercicio_video_snapshot": "",
            "series": 3,
            "repeticiones": "10",
            "kilos": "",
            "descanso": "",
            "notas": "",
            "bloque": "",
        }
        datos.update(overrides)
        return datos

    # ---- permisos ----

    def test_anonimo_no_entra(self):
        for nombre, url in self._urls().items():
            with self.subTest(vista=nombre):
                self.assertEqual(self.client.post(url, {}).status_code, 302)

    def test_el_alumno_recibe_403(self):
        usuario = User.objects.create_user("alu", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = perfil
        self.alumno.save()
        self.client.login(username="alu", password="clave-123456")
        for nombre, url in self._urls().items():
            with self.subTest(vista=nombre):
                self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_eliminar_no_acepta_get(self):
        self.client.login(username="staff-a", password="clave-123456")
        self.assertEqual(self.client.get(self._urls()["eliminar"]).status_code, 405)

    # ---- aislamiento de tenant ----

    def test_staff_de_otro_gimnasio_recibe_404(self):
        self.client.login(username="staff-a", password="clave-123456")
        url = reverse(
            "rutinas:asignada_item_editar",
            args=[self.otra_asignada.pk, self.item_ajeno.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_item_de_otra_rutina_no_es_accesible_desde_una_asignada_propia(self):
        """El caso cruzado: `asignada_pk` legítima + `pk` de un item ajeno.
        Da 404 porque el item no está en `asignada.items`. Espejo de
        `test_item_de_otro_gimnasio_no_es_accesible_desde_plantilla_ajena`."""
        self.client.login(username="staff-a", password="clave-123456")
        url = reverse(
            "rutinas:asignada_item_editar",
            args=[self.asignada.pk, self.item_ajeno.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_no_se_puede_borrar_un_item_ajeno_desde_una_asignada_propia(self):
        self.client.login(username="staff-a", password="clave-123456")
        url = reverse(
            "rutinas:asignada_item_eliminar",
            args=[self.asignada.pk, self.item_ajeno.pk],
        )
        self.assertEqual(self.client.post(url, {}).status_code, 404)
        self.assertTrue(
            RutinaAsignadaItem.objects.filter(pk=self.item_ajeno.pk).exists()
        )

    def test_agregar_en_un_dia_que_la_rutina_no_tiene_da_404(self):
        self.client.login(username="staff-a", password="clave-123456")
        url = reverse("rutinas:asignada_item_crear", args=[self.asignada.pk, 9])
        self.assertEqual(self.client.get(url).status_code, 404)

    # ---- flujo de punta a punta ----

    def test_editar_propaga_el_nombre_y_redirige(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            self._urls()["editar"],
            self._datos_edicion(
                ejercicio_nombre_snapshot="Press inclinado", kilos="40kg"
            ),
        )
        self.assertRedirects(
            response, reverse("rutinas:asignada_detalle", args=[self.asignada.pk])
        )
        self.assertEqual(
            self.asignada.items.filter(
                ejercicio_nombre_snapshot="Press inclinado"
            ).count(),
            4,
        )
        # Los kilos, en cambio, solo en la semana editada.
        self.assertEqual(
            self.asignada.items.filter(kilos="40kg").count(), 1
        )

    def test_agregar_crea_las_cuatro_filas(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            self._urls()["crear"],
            {
                "ejercicio": self.sentadilla.pk,
                "series": 3,
                "repeticiones": "12",
                "kilos": "",
                "descanso": "",
                "notas": "",
                "bloque": "",
            },
        )
        self.assertRedirects(
            response, reverse("rutinas:asignada_detalle", args=[self.asignada.pk])
        )
        self.assertEqual(
            self.asignada.items.filter(
                ejercicio_nombre_snapshot=self.sentadilla.nombre
            ).count(),
            4,
        )

    def test_agregar_un_ejercicio_de_otro_gimnasio_es_invalido(self):
        """FK-injection: el queryset lo acota `TenantScopedModelForm`."""
        ajeno = Ejercicio.objects.create(gimnasio=self.otro_gim, nombre="Ajeno")
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            self._urls()["crear"],
            {"ejercicio": ajeno.pk, "series": 3, "repeticiones": "12"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            self.asignada.items.filter(ejercicio_nombre_snapshot="Ajeno").exists()
        )

    def test_quitar_borra_las_cuatro_filas(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(self._urls()["eliminar"], {})
        self.assertRedirects(
            response, reverse("rutinas:asignada_detalle", args=[self.asignada.pk])
        )
        self.assertEqual(self.asignada.items.count(), 0)

    def test_nombre_duplicado_no_escribe_y_muestra_el_error(self):
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=2, series=3, repeticiones="10",
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            self._urls()["editar"],
            self._datos_edicion(ejercicio_nombre_snapshot="Sentadilla"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.item.refresh_from_db()
        self.assertEqual(
            self.item.ejercicio_nombre_snapshot, self.press_banca.nombre
        )

    def test_el_staff_no_puede_pisar_el_rpe_del_alumno(self):
        """`rpe` no es campo del form: es dato del alumno y esta es una
        pantalla de staff."""
        self.item.rpe = RutinaAsignadaItem.RPE.AL_LIMITE
        self.item.save(update_fields=["rpe"])
        self.client.login(username="staff-a", password="clave-123456")
        self.client.post(
            self._urls()["editar"],
            self._datos_edicion(rpe=RutinaAsignadaItem.RPE.MAS_INTENSO),
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.rpe, RutinaAsignadaItem.RPE.AL_LIMITE)

    def test_el_form_de_edicion_no_ofrece_estructura_ni_rpe(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(self._urls()["editar"])
        self.assertEqual(
            set(response.context["form"].fields),
            {
                "ejercicio_nombre_snapshot", "ejercicio_video_snapshot",
                "series", "repeticiones", "kilos", "descanso", "notas", "bloque",
            },
        )

    def test_el_form_muestra_el_rpe_que_reporto_el_alumno(self):
        """El diferenciador, en el punto donde el entrenador decide."""
        hermano = self.asignada.items.get(dia=1, semana=2)
        hermano.rpe = RutinaAsignadaItem.RPE.MAS_INTENSO
        hermano.save(update_fields=["rpe"])
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(self._urls()["editar"])
        self.assertContains(response, "Subir la carga")
        self.assertContains(response, "Sin calificar")


class AsignadaDetailPanelTests(RutinasTestCase):
    """El panel «Cómo viene el alumno» y la tabla reagrupada por día."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="Full body", objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate(),
        )
        RutinaAsignadaItem.objects.bulk_create([
            RutinaAsignadaItem(
                rutina_asignada=self.asignada,
                ejercicio_nombre_snapshot=nombre,
                semana=semana, dia=dia, orden=orden, series=3, repeticiones="10",
            )
            for dia in (1, 2)
            for semana in range(1, 5)
            for orden, nombre in enumerate(("Press", "Sentadilla"), start=1)
        ])
        self.client.login(username="staff-a", password="clave-123456")

    def _get(self):
        return self.client.get(
            reverse("rutinas:asignada_detalle", args=[self.asignada.pk])
        )

    def test_agrupa_por_dia_y_no_repite_el_ejercicio_por_semana(self):
        """16 items -> 2 días de 2 filas, no 16 filas."""
        response = self._get()
        dias = response.context["dias"]
        self.assertEqual(len(dias), 2)
        self.assertEqual(len(dias[0]["ejercicios"]), 2)

    def test_muestra_la_adherencia(self):
        RutinaAsignadaDiaCompletado.objects.create(
            rutina_asignada=self.asignada, dia=1, semana=1
        )
        response = self._get()
        self.assertContains(response, "Cómo viene")
        self.assertEqual(response.context["adherencia"].entrenadas, 1)

    def test_marca_las_semanas_que_el_alumno_entreno(self):
        """Primera vez que `RutinaAsignadaDiaCompletado` aparece en una vista
        de staff: hasta ahora el alumno lo marcaba y nadie lo veía."""
        RutinaAsignadaDiaCompletado.objects.create(
            rutina_asignada=self.asignada, dia=1, semana=1
        )
        self.assertContains(self._get(), "✓ Entrenado")

    def test_muestra_la_senal_de_carga_del_rpe(self):
        item = self.asignada.items.filter(dia=1, semana=1).first()
        item.rpe = RutinaAsignadaItem.RPE.MAS_INTENSO
        item.save(update_fields=["rpe"])
        self.assertContains(self._get(), "Subir la carga")

    def test_un_ejercicio_sin_calificar_no_muestra_senal(self):
        self.assertContains(self._get(), "Sin calificar")

    def test_hay_un_boton_de_agregar_por_dia(self):
        response = self._get()
        for dia in (1, 2):
            self.assertContains(
                response,
                reverse("rutinas:asignada_item_crear", args=[self.asignada.pk, dia]),
            )

    def test_los_links_al_formulario_llevan_hx_boost_false(self):
        """Guardarraíl de la causa raíz recurrente #1 del proyecto: el form
        necesita `extra_style` (CSS de Tom Select), que vive en el <head> y
        htmx nunca swapea en una navegación boosteada."""
        response = self._get()
        contenido = response.content.decode()
        for url in (
            reverse("rutinas:asignada_item_crear", args=[self.asignada.pk, 1]),
            reverse(
                "rutinas:asignada_item_editar",
                args=[self.asignada.pk, self.asignada.items.first().pk],
            ),
        ):
            posicion = contenido.find(url)
            self.assertNotEqual(posicion, -1, url)
            self.assertIn('hx-boost="false"', contenido[posicion - 120:posicion])

    def test_avisa_si_no_es_la_rutina_que_ve_el_alumno(self):
        """Reemplaza al viejo aviso de "rutinas activas duplicadas". Esta
        pantalla es la de EDICIÓN: con planes que conviven, editar una que el
        alumno no está viendo es un error MÁS probable, no menos."""
        RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="La nueva", objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate(),
        )
        self.assertContains(self._get(), "no le llegan")

    def test_no_avisa_si_es_la_que_ve_el_alumno(self):
        self.assertNotContains(self._get(), "no le llegan")

    def test_el_portal_del_alumno_no_muestra_la_senal_de_carga(self):
        """La señal es una lectura de ENTRENADOR. Al alumno se le muestra la
        etiqueta que él mismo eligió, no una instrucción sobre su propio
        entrenamiento -- por eso `anotar_senales` vive en `progreso.py` y no
        dentro de `agrupacion.py`."""
        item = self.asignada.items.filter(dia=1, semana=1).first()
        item.rpe = RutinaAsignadaItem.RPE.MAS_INTENSO
        item.save(update_fields=["rpe"])
        usuario = User.objects.create_user("alu", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = perfil
        self.alumno.save()
        self.client.login(username="alu", password="clave-123456")
        response = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertNotContains(response, "Subir la carga")

    def test_no_dispara_una_query_por_item_ni_por_dia(self):
        chica = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="Chica", objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate(),
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=chica, ejercicio_nombre_snapshot="Press",
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("rutinas:asignada_detalle", args=[chica.pk]))
        with CaptureQueriesContext(connection) as muchas:
            self._get()
        self.assertEqual(len(pocas), len(muchas))


class ComentariosDeTemplateTests(SimpleTestCase):
    """Ningún template puede tener un `{# ... #}` abierto en una línea y
    cerrado en otra.

    El lexer de Django solo reconoce `{# ... #}` cuando abre y cierra en la
    MISMA línea; si hay un salto de por medio no lo trata como comentario y lo
    imprime tal cual en la pantalla del usuario. Para varias líneas hay que
    usar `{% comment %}`.

    Pasó de verdad y llegó a producción: `mi_dia_detalle.html` mostraba el
    texto "El bloque agrupa superseries: A1, A2 y A3..." EN LUGAR del nombre
    del ejercicio, en la rutina de todos los alumnos. Había 8 casos en 5
    templates, todos escritos el mismo día. No los agarró ningún test porque
    ninguno miraba ese pedazo de HTML, ni el linter (es HTML válido).

    Barre TODOS los templates del proyecto a propósito, no solo los de
    rutinas: el error es de sintaxis de Django, no de esta app.
    """

    def test_ningun_comentario_queda_sin_cerrar_en_su_linea(self):
        raiz = Path(settings.BASE_DIR) / "templates"
        culpables = []
        for plantilla in raiz.rglob("*.html"):
            for numero, linea in enumerate(
                plantilla.read_text().splitlines(), start=1
            ):
                if "{#" in linea and "#}" not in linea.split("{#", 1)[1]:
                    culpables.append(
                        f"{plantilla.relative_to(raiz)}:{numero}: {linea.strip()[:70]}"
                    )
        self.assertEqual(
            culpables,
            [],
            "Comentarios `{# #}` abiertos en una línea y cerrados en otra: "
            "Django los imprime en pantalla. Usá `{% comment %}`.\n"
            + "\n".join(culpables),
        )


class UnaSolaRutinaActivaTests(RutinasTestCase):
    """Los planes CONVIVEN y los ordena la fecha.

    Esta clase fijaba lo contrario: que asignar un plan nuevo archivara el
    anterior, para que hubiera una sola rutina activa por alumno. Duró unas
    horas. El dueño del producto corrigió la regla: **un plan dura 4 semanas y
    el alumno lo ve completas aunque el profesor ya haya cargado el
    siguiente**, así que archivar al asignar le sacaba el plan en curso el día
    en que se preparaba el próximo.

    Lo que sí sobrevive de aquel fix, y por eso los tests siguen acá: el
    desempate del `Meta.ordering` (dos planes que arrancan el mismo día) y que
    asignar no toca las rutinas de otros alumnos. La selección real la decide
    ahora `RutinaAsignada.vigente_de` -- ver `VigenciaDeRutinaTests`.
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Plan A", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.press_banca, semana=1, dia=1,
            orden=1, series=3, repeticiones="10",
        )

    def _asignar(self, fecha=None):
        return RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=fecha or timezone.localdate(),
        )

    def test_asignar_una_rutina_nueva_no_archiva_la_anterior(self):
        """Invertido a propósito respecto de la versión anterior de este test:
        el alumno tiene que poder terminar sus 4 semanas."""
        vieja = self._asignar()
        nueva = self._asignar()
        vieja.refresh_from_db()
        self.assertTrue(vieja.activa)
        self.assertTrue(nueva.activa)

    def test_asignar_no_escribe_fecha_fin(self):
        """`fecha_fin` sería un campo derivado y persistido que se
        desincroniza en cuanto se inserta un plan entre dos existentes. El fin
        del ciclo se deriva con `fecha_fin_prevista`, mismo criterio que
        `semana_actual`."""
        vieja = self._asignar()
        self._asignar()
        vieja.refresh_from_db()
        self.assertIsNone(vieja.fecha_fin)

    def test_asignar_no_borra_el_historial(self):
        vieja = self._asignar()
        self._asignar()
        vieja.refresh_from_db()
        self.assertTrue(RutinaAsignada.objects.filter(pk=vieja.pk).exists())
        self.assertEqual(vieja.items.count(), 1)

    def test_no_toca_las_rutinas_de_otro_alumno(self):
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Beto", apellido="Gómez"
        )
        suya = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=otro, plantilla=self.plantilla,
            fecha_inicio=timezone.localdate(),
        )
        self._asignar()
        suya.refresh_from_db()
        self.assertTrue(suya.activa)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=otro), suya)

    def test_con_la_misma_fecha_de_inicio_gana_la_mas_reciente(self):
        """El desempate por `-id` del `Meta.ordering`. Sin él ganaba la vieja,
        que es exactamente el caso de reasignar el mismo día."""
        vieja = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot="VIEJA",
            objetivo_snapshot="F", fecha_inicio=date(2026, 8, 1),
        )
        nueva = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot="NUEVA",
            objetivo_snapshot="F", fecha_inicio=date(2026, 8, 1),
        )
        elegida = self.alumno.rutinas_asignadas.filter(activa=True).first()
        self.assertEqual(elegida.pk, nueva.pk)
        self.assertGreater(nueva.pk, vieja.pk)

    def test_el_orden_no_depende_de_como_se_pidan(self):
        """En Postgres un `ORDER BY` con empate no garantiza ningún orden: el
        desempate tiene que estar en el propio ordering, no en el azar."""
        for _ in range(3):
            RutinaAsignada.objects.create(
                gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot="X",
                objetivo_snapshot="F", fecha_inicio=date(2026, 8, 1),
            )
        pks = list(
            self.alumno.rutinas_asignadas.values_list("pk", flat=True)
        )
        self.assertEqual(pks, sorted(pks, reverse=True))


class MigracionCerrarDuplicadasTests(RutinasTestCase):
    """La migración de datos `rutinas/0011`, que limpia lo que el bug ya dejó
    en producción.

    Se ejercita la función directamente (no con `MigratorTestCase`, que el
    proyecto no usa) pasándole un `apps` de mentira que devuelve el modelo
    real: lo que importa verificar es el CRITERIO de selección, que es donde
    puede equivocarse y archivar la rutina que el alumno está usando.
    """

    def setUp(self):
        super().setUp()
        self.otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Beto", apellido="Gómez"
        )

    def _crear(self, alumno, nombre, fecha, activa=True, fecha_fin=None):
        return RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, nombre_snapshot=nombre,
            objetivo_snapshot="Fuerza", fecha_inicio=fecha, activa=activa,
            fecha_fin=fecha_fin,
        )

    def _correr(self):
        # `import_module` y no un `from ... import`: el módulo arranca con un
        # dígito (`0011_...`), así que no es un identificador válido.
        migracion = import_module(
            "rutinas.migrations.0011_cerrar_rutinas_activas_duplicadas"
        )

        class AppsFalso:
            def get_model(self, app, modelo):
                return RutinaAsignada

        migracion.cerrar_duplicadas(AppsFalso(), None)

    def test_deja_una_sola_activa_por_alumno(self):
        self._crear(self.alumno, "vieja", date(2026, 8, 1))
        self._crear(self.alumno, "nueva", date(2026, 8, 20))
        self._correr()
        activas = self.alumno.rutinas_asignadas.filter(activa=True)
        self.assertEqual(activas.count(), 1)
        self.assertEqual(activas.first().nombre_snapshot, "nueva")

    def test_conserva_la_misma_que_ve_el_alumno_hoy(self):
        """Con fechas iguales gana la de `id` mayor -- el mismo desempate que
        el `Meta.ordering`, para que la migración no le cambie la rutina al
        alumno respecto de lo que ya está viendo."""
        self._crear(self.alumno, "primera", date(2026, 8, 1))
        segunda = self._crear(self.alumno, "segunda", date(2026, 8, 1))
        vista_antes = self.alumno.rutinas_asignadas.filter(activa=True).first()
        self._correr()
        activas = self.alumno.rutinas_asignadas.filter(activa=True)
        self.assertEqual(activas.count(), 1)
        self.assertEqual(activas.first().pk, segunda.pk)
        self.assertEqual(activas.first().pk, vista_antes.pk)

    def test_no_toca_a_un_alumno_con_una_sola_activa(self):
        sola = self._crear(self.otro, "unica", date(2026, 8, 1))
        self._crear(self.alumno, "a", date(2026, 8, 1))
        self._crear(self.alumno, "b", date(2026, 8, 2))
        self._correr()
        sola.refresh_from_db()
        self.assertTrue(sola.activa)
        self.assertIsNone(sola.fecha_fin)

    def test_no_borra_nada(self):
        self._crear(self.alumno, "vieja", date(2026, 8, 1))
        self._crear(self.alumno, "nueva", date(2026, 8, 20))
        self._correr()
        self.assertEqual(self.alumno.rutinas_asignadas.count(), 2)

    def test_la_cerrada_recibe_como_fecha_fin_el_inicio_de_la_que_la_reemplazo(self):
        vieja = self._crear(self.alumno, "vieja", date(2026, 8, 1))
        self._crear(self.alumno, "nueva", date(2026, 8, 20))
        self._correr()
        vieja.refresh_from_db()
        self.assertEqual(vieja.fecha_fin, date(2026, 8, 20))

    def test_respeta_una_fecha_fin_ya_cargada(self):
        vieja = self._crear(
            self.alumno, "vieja", date(2026, 8, 1), fecha_fin=date(2026, 8, 10)
        )
        self._crear(self.alumno, "nueva", date(2026, 8, 20))
        self._correr()
        vieja.refresh_from_db()
        self.assertEqual(vieja.fecha_fin, date(2026, 8, 10))
        self.assertFalse(vieja.activa)

    def test_no_mezcla_alumnos(self):
        a1 = self._crear(self.alumno, "a1", date(2026, 8, 1))
        self._crear(self.alumno, "a2", date(2026, 8, 20))
        b1 = self._crear(self.otro, "b1", date(2026, 8, 5))
        self._correr()
        b1.refresh_from_db()
        a1.refresh_from_db()
        self.assertTrue(b1.activa)
        self.assertFalse(a1.activa)

    def test_es_idempotente(self):
        self._crear(self.alumno, "vieja", date(2026, 8, 1))
        self._crear(self.alumno, "nueva", date(2026, 8, 20))
        self._correr()
        self._correr()
        self.assertEqual(
            self.alumno.rutinas_asignadas.filter(activa=True).count(), 1
        )


class VigenciaDeRutinaTests(RutinasTestCase):
    """`RutinaAsignada.vigente_de`: qué plan ve el alumno hoy.

    Regla de producto: un plan dura 4 semanas y el alumno lo ve completas
    aunque el profesor ya haya cargado el siguiente; cuando el ciclo termina
    pasa al nuevo, y sin siguiente se queda con el último. Todo eso lo resuelve
    "la más reciente que YA arrancó", sin comparar contra el fin del ciclo.
    """

    def _rutina(self, nombre, dias_desde_hoy, activa=True):
        return RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot=nombre,
            objetivo_snapshot="Fuerza", activa=activa,
            fecha_inicio=timezone.localdate() + timedelta(days=dias_desde_hoy),
        )

    def test_sin_rutinas_devuelve_none(self):
        self.assertIsNone(RutinaAsignada.vigente_de(alumno=self.alumno))

    def test_el_alumno_sigue_viendo_el_viejo_aunque_exista_el_nuevo(self):
        """El caso que motiva todo el cambio."""
        viejo = self._rutina("viejo", -8)
        self._rutina("nuevo", +20)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), viejo)

    def test_el_relevo_ocurre_cuando_llega_la_fecha(self):
        self._rutina("viejo", -30)
        nuevo = self._rutina("nuevo", -2)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), nuevo)

    def test_un_plan_futuro_no_se_adelanta(self):
        """El bug que se está arreglando: antes `filter(activa=True).first()`
        devolvía la de fecha futura y el alumno la veía como 'Semana 1 de 4'."""
        self._rutina("futuro", +5)
        self.assertIsNone(RutinaAsignada.vigente_de(alumno=self.alumno))

    def test_sin_siguiente_se_queda_el_ultimo_aunque_haya_terminado(self):
        viejo = self._rutina("terminado hace meses", -200)
        self.assertTrue(viejo.ya_termino)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), viejo)

    def test_dos_planes_el_mismo_dia_gana_el_mas_nuevo(self):
        self._rutina("primero", 0)
        segundo = self._rutina("segundo", 0)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), segundo)

    def test_una_rutina_archivada_no_se_elige(self):
        vigente = self._rutina("vigente", -10)
        self._rutina("archivada más nueva", -1, activa=False)
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), vigente)

    def test_no_ve_las_rutinas_de_otro_alumno(self):
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Beto", apellido="Gómez"
        )
        self._rutina("mía", -5)
        self.assertIsNone(RutinaAsignada.vigente_de(alumno=otro))

    def test_no_depende_del_meta_ordering(self):
        """`vigente_de` reordena explícito: un `.distinct()` o un
        `prefetch_related` de un caller futuro anularían el `Meta.ordering`
        sin ruido."""
        self._rutina("vieja", -20)
        nueva = self._rutina("nueva", -1)
        self.assertIn(
            "ORDER BY",
            str(
                self.alumno.rutinas_asignadas.filter(activa=True)
                .order_by("-fecha_inicio", "-id")
                .query
            ),
        )
        self.assertEqual(RutinaAsignada.vigente_de(alumno=self.alumno), nueva)

    # ---- proxima_de ----

    def test_proxima_es_la_que_arranca_antes(self):
        self._rutina("vigente", -3)
        proxima = self._rutina("en 10 días", +10)
        self._rutina("en 40 días", +40)
        self.assertEqual(RutinaAsignada.proxima_de(alumno=self.alumno), proxima)

    def test_proxima_es_none_si_no_hay_nada_programado(self):
        self._rutina("vigente", -3)
        self.assertIsNone(RutinaAsignada.proxima_de(alumno=self.alumno))

    def test_vigente_y_proxima_nunca_devuelven_la_misma(self):
        """Son conjuntos disjuntos por construcción: si se solaparan, las
        escrituras del alumno podrían caer sobre un plan que no arrancó."""
        self._rutina("vigente", -3)
        self._rutina("programada", +10)
        self.assertNotEqual(
            RutinaAsignada.vigente_de(alumno=self.alumno),
            RutinaAsignada.proxima_de(alumno=self.alumno),
        )

    # ---- fechas derivadas ----

    def test_el_ciclo_dura_cuatro_semanas_y_fecha_fin_prevista_es_exclusiva(self):
        r = self._rutina("x", 0)
        self.assertEqual(r.fecha_fin_prevista, r.fecha_inicio + timedelta(days=28))
        self.assertEqual(r.ultimo_dia, r.fecha_inicio + timedelta(days=27))

    def test_el_dia_28_ya_no_esta_cubierto(self):
        """El borde exacto: el día 27 es el último del ciclo (semana 4) y el
        28 es el primero del siguiente. Sin fijarlo, el plan que sigue arranca
        un día tarde o pisa un día."""
        r = self._rutina("empezó hace 27 días", -27)
        self.assertTrue(r.esta_vigente)
        self.assertEqual(r.semana_actual, 4)
        r2 = self._rutina("empezó hace 28 días", -28)
        self.assertTrue(r2.ya_termino)

    def test_estados_derivados(self):
        self.assertTrue(self._rutina("futura", +5).es_futura)
        self.assertTrue(self._rutina("vigente", -5).esta_vigente)
        self.assertTrue(self._rutina("vieja", -100).ya_termino)


class VigenciaEnLasVistasTests(RutinasTestCase):
    """Las seis pantallas coinciden en qué rutina muestran, y las escrituras
    del alumno no tocan un plan que no arrancó."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        usuario = User.objects.create_user("alu", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = perfil
        self.alumno.save()

        self.vigente = self._con_items("EN CURSO", -8)
        self.futura = self._con_items("PROGRAMADA", +20)

    def _con_items(self, nombre, dias):
        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot=nombre,
            objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate() + timedelta(days=dias),
        )
        RutinaAsignadaItem.objects.bulk_create([
            RutinaAsignadaItem(
                rutina_asignada=rutina, ejercicio_nombre_snapshot="Press",
                semana=semana, dia=1, orden=1, series=3, repeticiones="10",
            )
            for semana in range(1, 5)
        ])
        return rutina

    def test_el_portal_del_alumno_muestra_la_vigente(self):
        self.client.login(username="alu", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "EN CURSO")
        self.assertNotContains(response, "PROGRAMADA")

    def test_la_ficha_del_staff_muestra_la_vigente_y_anuncia_la_proxima(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))
        self.assertEqual(response.context["rutina_actual"], self.vigente)
        self.assertEqual(response.context["rutina_proxima"], self.futura)
        self.assertContains(response, "Próximo plan")

    def test_mi_dia_usa_la_vigente(self):
        self.client.login(username="alu", password="clave-123456")
        response = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertEqual(response.context["rutina_actual"], self.vigente)

    def test_el_alumno_no_puede_marcar_entrenado_un_plan_que_no_arranco(self):
        """El motivo por el que `vigente_de` no tiene fallback: si devolviera
        el plan programado, esta escritura caería sobre él y ensuciaría la
        adherencia con la que el profesor ajusta las cargas."""
        self.vigente.delete()  # queda solo la futura
        self.client.login(username="alu", password="clave-123456")
        response = self.client.post(
            reverse("rutinas:dia_completado_toggle", args=[1, 1])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(RutinaAsignadaDiaCompletado.objects.count(), 0)

    def test_el_alumno_no_puede_calificar_un_item_de_un_plan_futuro(self):
        item = self.futura.items.first()
        self.client.login(username="alu", password="clave-123456")
        response = self.client.post(
            reverse("rutinas:item_calificar", args=[item.pk]),
            {"rpe": RutinaAsignadaItem.RPE.AL_LIMITE},
        )
        self.assertEqual(response.status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.rpe, "")

    def test_el_alumno_si_puede_calificar_un_item_de_la_vigente(self):
        item = self.vigente.items.first()
        self.client.login(username="alu", password="clave-123456")
        self.client.post(
            reverse("rutinas:item_calificar", args=[item.pk]),
            {"rpe": RutinaAsignadaItem.RPE.AL_LIMITE},
        )
        item.refresh_from_db()
        self.assertEqual(item.rpe, RutinaAsignadaItem.RPE.AL_LIMITE)

    def test_el_detalle_avisa_que_el_plan_programado_no_lo_ve_el_alumno(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[self.futura.pk])
        )
        self.assertContains(response, "todavía no empezó")
        self.assertContains(response, "Programada")

    def test_archivar_saca_la_rutina_de_la_vigencia(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            reverse("rutinas:asignada_archivar", args=[self.vigente.pk])
        )
        self.assertRedirects(
            response, reverse("alumnos:detalle", args=[self.alumno.pk])
        )
        self.vigente.refresh_from_db()
        self.assertFalse(self.vigente.activa)
        self.assertIsNone(RutinaAsignada.vigente_de(alumno=self.alumno))

    def test_archivar_es_post_only_y_staff_only(self):
        url = reverse("rutinas:asignada_archivar", args=[self.vigente.pk])
        self.client.login(username="staff-a", password="clave-123456")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.login(username="alu", password="clave-123456")
        self.assertEqual(self.client.post(url).status_code, 403)


class AsignarConPlanVigenteTests(RutinasTestCase):
    """La fecha de inicio sugerida y el guard contra la rutina invisible."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio, nombre="Plan", objetivo="Fuerza",
            nivel=RutinaPlantilla.Nivel.INTERMEDIO, dias_por_semana=1,
        )
        RutinaPlantillaItem.objects.create(
            rutina=self.plantilla, ejercicio=self.press_banca, semana=1, dia=1,
            orden=1, series=3, repeticiones="10",
        )
        self.vigente = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=timezone.localdate() - timedelta(days=8),
        )

    def test_el_modelo_rechaza_una_rutina_que_nunca_se_veria(self):
        """Arrancar antes que la vigente la volvería invisible: `vigente_de`
        toma la más reciente."""
        with self.assertRaises(ValidationError):
            RutinaAsignada.crear_desde_plantilla(
                gimnasio=self.gimnasio, alumno=self.alumno,
                plantilla=self.plantilla,
                fecha_inicio=self.vigente.fecha_inicio - timedelta(days=1),
            )

    def test_el_form_lo_traduce_a_un_error_de_campo(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(reverse("rutinas:asignar"), {
            "alumno": self.alumno.pk, "plantilla": self.plantilla.pk,
            "fecha_inicio": (
                self.vigente.fecha_inicio - timedelta(days=1)
            ).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("fecha_inicio", response.context["form"].errors)

    def test_el_option_del_alumno_trae_la_fecha_sugerida(self):
        """Sin endpoint: el dato viaja en el HTML y lo usa un listener."""
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(reverse("rutinas:asignar"))
        self.assertContains(
            response,
            f'data-fecha-sugerida="{self.vigente.fecha_fin_prevista.isoformat()}"',
        )

    def test_el_prefill_por_query_param_preselecciona_al_alumno(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(
            reverse("rutinas:asignar") + f"?alumno={self.alumno.pk}"
        )
        self.assertEqual(
            response.context["form"].initial["alumno"], str(self.alumno.pk)
        )

    def test_las_opciones_no_hacen_una_query_por_alumno(self):
        for n in range(6):
            otro = Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{n}", apellido="X"
            )
            RutinaAsignada.crear_desde_plantilla(
                gimnasio=self.gimnasio, alumno=otro, plantilla=self.plantilla,
                fecha_inicio=timezone.localdate() - timedelta(days=3),
            )
        self.client.login(username="staff-a", password="clave-123456")
        with CaptureQueriesContext(connection) as muchos:
            self.client.get(reverse("rutinas:asignar"))
        self.assertLess(len(muchos), 25)
