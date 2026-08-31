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

    def test_plantilla_detail_muestra_columna_semana(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_a.pk])
        )
        self.assertContains(response, "<th>Semana</th>", html=True)

    def test_asignada_detail_muestra_semana_actual(self):
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
        self.assertContains(response, "Semana actual: 2 de 4")

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
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio, alumno=self.alumno, plantilla=self.plantilla,
            fecha_inicio=date(2026, 8, 31),
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[asignada.pk])
        )
        self.assertContains(response, "<th>Bloque</th>", html=False)
        self.assertContains(response, "A1")

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
