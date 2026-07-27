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
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ejercicios.models import Ejercicio
from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
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
