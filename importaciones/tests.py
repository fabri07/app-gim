"""Tests de `importaciones`. Ver `rutinas/tests.py` para el estilo de
fixtures de este repo."""

import io

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path, reverse

from config.urls import urlpatterns as _urlpatterns_produccion
from ejercicios.models import Ejercicio
from importaciones.matching import (
    MatchResultado,
    construir_indice_ejercicios,
    resolver_grupo_muscular,
    resolver_nombre,
)
from importaciones.models import Importacion
from importaciones.parsing import (
    ALIAS_BIBLIOTECA,
    ALIAS_PLANTILLA,
    FilaInvalida,
    HojaParseada,
    ItemParseado,
    detectar_columnas,
    leer_hoja_biblioteca,
    leer_hoja_plantilla,
    normalizar_texto,
    parsear_archivo_biblioteca,
    parsear_archivo_plantillas,
)
from importaciones.services import (
    ImportacionInvalida,
    confirmar_importacion_biblioteca,
    confirmar_importacion_plantillas,
    previsualizar_importacion_biblioteca,
    previsualizar_importacion_plantillas,
)
from rutinas.models import RutinaPlantilla, RutinaPlantillaItem
from tenants.models import Gimnasio, Perfil


# `importaciones.urls` todavía no está incluido en `config/urls.py` -- eso
# es la Tarea 12 (wiring final), fuera del alcance de esta tarea. Pero los
# tests de vistas de acá abajo necesitan poder resolver
# `reverse("importaciones:...")` (y, para el caso anónimo, `reverse("login")`
# vía `LOGIN_URL`) igual. En vez de tocar `config/urls.py` antes de tiempo,
# este módulo se usa a sí mismo como un ROOT_URLCONF alternativo (solo bajo
# `@override_settings`, solo para `ImportacionPlantillasViewsTests`):
# reexporta el urlconf real de producción (`config.urls`, sin modificarlo)
# más el include que la Tarea 12 va a agregar ahí de forma definitiva.
urlpatterns = list(_urlpatterns_produccion) + [
    path("importaciones/", include("importaciones.urls")),
]


def _archivo_xlsx(wb):
    """Serializa un Workbook de openpyxl a un SimpleUploadedFile, como
    llegaría desde un <input type=file>."""
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        "plan.xlsx", buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class ImportacionModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central", slug="gimnasio-central"
        )

    def test_creacion_con_defaults(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.PLANTILLAS,
            resultado={"hojas": []},
        )
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertIsNone(importacion.confirmado_en)
        self.assertIsNone(importacion.creado_por)

    def test_str(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.BIBLIOTECA,
            resultado={"items": []},
        )
        self.assertIn("Gimnasio Central", str(importacion))


class ImportacionAislamientoTenantTests(TestCase):
    def test_for_gimnasio_no_mezcla_gimnasios(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        Importacion.objects.create(
            gimnasio=gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        Importacion.objects.create(
            gimnasio=gimnasio_b, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_a).count(), 1)
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_b).count(), 1)


class NormalizarTextoTests(SimpleTestCase):
    def test_lowercase_y_sin_tildes(self):
        self.assertEqual(normalizar_texto("Press de Banca"), "press de banca")
        self.assertEqual(normalizar_texto("PRÉSS DÉ BÁNCA"), "press de banca")

    def test_colapsa_espacios(self):
        self.assertEqual(normalizar_texto("  Sentadilla   libre  "), "sentadilla libre")

    def test_texto_vacio(self):
        self.assertEqual(normalizar_texto(""), "")
        self.assertEqual(normalizar_texto(None), "")


class DetectarColumnasTests(SimpleTestCase):
    def test_matchea_por_alias_case_y_acento_insensible(self):
        encabezados = ["Semana", "Día", "Ejercicio", "Series", "Repeticiones", "Descanso", "Notas"]
        campos, advertencias = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(
            campos,
            {"semana": 0, "dia": 1, "ejercicio": 2, "series": 3,
             "repeticiones": 4, "descanso": 5, "notas": 6},
        )
        self.assertEqual(advertencias, [])

    def test_orden_de_columnas_no_importa(self):
        encabezados = ["Ejercicio", "Series", "Dia"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos, {"ejercicio": 0, "series": 1, "dia": 2})

    def test_columna_no_encontrada_no_esta_en_el_resultado(self):
        encabezados = ["Ejercicio", "Series"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertNotIn("descanso", campos)
        self.assertNotIn("semana", campos)

    def test_columna_duplicada_usa_la_primera_y_avisa(self):
        encabezados = ["Series", "Ejercicio", "Sets"]  # "Series" y "Sets" son alias de "series"
        campos, advertencias = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos["series"], 0)
        self.assertEqual(len(advertencias), 1)
        self.assertIn("series", advertencias[0].lower())

    def test_encabezado_none_se_ignora(self):
        # Celda de header vacía (columna sin usar en la planilla real).
        encabezados = ["Ejercicio", None, "Series"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos, {"ejercicio": 0, "series": 2})

    def test_alias_biblioteca(self):
        encabezados = ["Nombre", "Grupo Muscular", "Video"]
        campos, _ = detectar_columnas(encabezados, ALIAS_BIBLIOTECA)
        self.assertEqual(campos, {"nombre": 0, "grupo_muscular": 1, "url_video": 2})


def _hoja_plantilla_basica():
    """Workbook en memoria con encabezados + 2 filas válidas, sin celdas
    combinadas. Reutilizado por varios tests de este módulo."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones", "Descanso", "Notas"])
    ws.append([1, 1, "Press de banca", 4, "8-12", "90s", ""])
    ws.append([1, 1, "Sentadilla", 3, "10", "60s", "Cuidar la técnica"])
    return ws


class LeerHojaPlantillaTests(SimpleTestCase):
    def test_lee_filas_validas(self):
        hoja = leer_hoja_plantilla(_hoja_plantilla_basica())
        self.assertEqual(len(hoja.items), 2)
        self.assertEqual(hoja.items[0], ItemParseado(
            semana=1, dia=1, orden=1, ejercicio_original="Press de banca",
            series=4, repeticiones="8-12", descanso="90s", notas="",
        ))
        self.assertEqual(hoja.items[1].orden, 2)  # segundo item del mismo (semana, dia)
        self.assertEqual(hoja.filas_invalidas, [])

    def test_dias_por_semana_es_el_maximo_dia_de_filas_validas(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([3, "Sentadilla", 3, "10"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.dias_por_semana, 3)

    def test_fila_sin_semana_cae_en_semana_1(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])  # sin columna Semana
        ws.append([1, "Press de banca", 4, "8-12"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items[0].semana, 1)

    def test_fila_con_series_invalida_se_saltea_con_motivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", "cuatro", "8-12"])  # "series" no numérico
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items, [])
        self.assertEqual(len(hoja.filas_invalidas), 1)
        self.assertEqual(hoja.filas_invalidas[0].fila_excel, 2)  # fila 1 = header
        self.assertIn("series", hoja.filas_invalidas[0].motivo.lower())

    def test_fila_totalmente_vacia_se_saltea_sin_motivo_ruidoso(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([None, None, None, None])
        ws.append([1, "Sentadilla", 3, "10"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(len(hoja.items), 2)
        self.assertEqual(hoja.filas_invalidas, [])  # vacía != inválida, se ignora en silencio

    def test_columna_ejercicio_ausente_devuelve_hoja_sin_items(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Series", "Repeticiones"])  # sin "Ejercicio"
        ws.append([1, 4, "8-12"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items, [])
        self.assertEqual(hoja.dias_por_semana, 0)

    def test_orden_secuencial_dentro_de_semana_y_dia(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, 1, "Press de banca", 4, "8-12"])
        ws.append([1, 1, "Sentadilla", 3, "10"])
        ws.append([2, 1, "Peso muerto", 3, "8"])  # otra semana: orden vuelve a 1
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual([i.orden for i in hoja.items], [1, 2, 1])

    def test_celda_combinada_de_semana_se_resuelve_por_el_ancla(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, 1, "Press de banca", 4, "8-12"])
        ws.append([None, 2, "Sentadilla", 3, "10"])  # "Semana" mergeada con la fila de arriba
        ws.merge_cells("A2:A3")
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items[1].semana, 1)


class LeerHojaBibliotecaTests(SimpleTestCase):
    def test_lee_ejercicios_validos(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular", "Video"])
        ws.append(["Press de banca", "Pecho", "https://youtube.com/x"])
        ws.append(["Sentadilla", "Piernas", ""])
        items, invalidas = leer_hoja_biblioteca(ws)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["nombre_original"], "Press de banca")
        self.assertEqual(items[0]["grupo_muscular_original"], "Pecho")
        self.assertEqual(items[0]["url_video"], "https://youtube.com/x")
        self.assertEqual(invalidas, [])

    def test_fila_sin_nombre_se_saltea_con_motivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["", "Pecho"])
        items, invalidas = leer_hoja_biblioteca(ws)
        self.assertEqual(items, [])
        self.assertEqual(len(invalidas), 1)

    def test_columna_grupo_muscular_es_opcional(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Press de banca"])
        items, _ = leer_hoja_biblioteca(ws)
        self.assertIsNone(items[0]["grupo_muscular_original"])


class ParsearArchivoPlantillasTests(SimpleTestCase):
    def test_multi_hoja_produce_una_hojaparseada_por_hoja(self):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Press de banca", 4, "8-12"])
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Sentadilla", 3, "10"])

        hojas = parsear_archivo_plantillas(_archivo_xlsx(wb))

        self.assertEqual(len(hojas), 2)
        self.assertEqual({h.nombre_hoja for h in hojas}, {"Hombres", "Mujeres"})


class ParsearArchivoBibliotecaTests(SimpleTestCase):
    def test_usa_la_primera_hoja(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])
        items, invalidas = parsear_archivo_biblioteca(_archivo_xlsx(wb))
        self.assertEqual(len(items), 1)
        self.assertEqual(invalidas, [])


class ResolverNombreTests(SimpleTestCase):
    def setUp(self):
        # Índice armado a mano -- resolver_nombre es pura, no toca DB.
        self.indice = {"press de banca": "PRESS_ID", "sentadilla": "SENTADILLA_ID"}

    def test_match_exacto_tras_normalizar(self):
        resultado = resolver_nombre("press de banca", self.indice)
        self.assertEqual(resultado.tipo, "exacto")
        self.assertEqual(resultado.ejercicio, "PRESS_ID")

    def test_typo_da_ambiguo_con_candidato(self):
        resultado = resolver_nombre("sentadila", self.indice)  # falta una "l"
        self.assertEqual(resultado.tipo, "ambiguo")
        self.assertEqual(resultado.candidato, "SENTADILLA_ID")
        self.assertGreaterEqual(resultado.score, 60)

    def test_nombre_sin_relacion_da_nuevo(self):
        resultado = resolver_nombre("hip thrust", self.indice)
        self.assertEqual(resultado.tipo, "nuevo")
        self.assertIsNone(resultado.candidato)

    def test_indice_vacio_siempre_da_nuevo(self):
        resultado = resolver_nombre("cualquier cosa", {})
        self.assertEqual(resultado.tipo, "nuevo")


class ResolverGrupoMuscularTests(SimpleTestCase):
    def test_match_exacto_contra_choices(self):
        self.assertEqual(resolver_grupo_muscular("Pecho"), Ejercicio.GrupoMuscular.PECHO)

    def test_match_por_alias(self):
        self.assertEqual(resolver_grupo_muscular("Abdomen"), Ejercicio.GrupoMuscular.CORE)

    def test_sin_match_devuelve_none(self):
        self.assertIsNone(resolver_grupo_muscular("no existe esto"))


class ConstruirIndiceEjerciciosTests(TestCase):
    def test_indexa_por_nombre_normalizado_y_aisla_por_tenant(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a, nombre="Press de Banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        indice = construir_indice_ejercicios(gimnasio_a)
        self.assertEqual(indice, {"press de banca": ejercicio_a})


class PrevisualizarImportacionPlantillasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

    def _archivo_dos_hojas(self):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Press de banca", 4, "8-12"])
        ws1.append([1, "sentadila", 3, "10"])  # typo de un ejercicio ya cargado
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Press de banca", 3, "10-12"])  # mismo ejercicio nuevo, otra hoja
        return _archivo_xlsx(wb)

    def test_crea_importacion_en_revision_sin_tocar_rutinaplantilla(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        self.assertEqual(importacion.tipo, Importacion.Tipo.PLANTILLAS)
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertEqual(importacion.gimnasio, self.gimnasio)
        self.assertEqual(importacion.creado_por, self.usuario)
        self.assertEqual(RutinaPlantilla.objects.count(), 0)

    def test_resultado_tiene_una_entrada_por_hoja(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        nombres_hoja = {h["nombre_hoja"] for h in importacion.resultado["hojas"]}
        self.assertEqual(nombres_hoja, {"Hombres", "Mujeres"})

    def test_ejercicio_repetido_entre_hojas_se_resuelve_una_sola_vez(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        # "Press de banca" aparece en las dos hojas -> una sola entrada en ejercicios_distintos
        self.assertIn("press de banca", importacion.resultado["ejercicios_distintos"])
        entrada = importacion.resultado["ejercicios_distintos"]["press de banca"]
        self.assertEqual(entrada["tipo"], "nuevo")

    def test_typo_de_ejercicio_existente_da_ambiguo_con_candidato(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        entrada = importacion.resultado["ejercicios_distintos"]["sentadila"]
        self.assertEqual(entrada["tipo"], "ambiguo")
        self.assertEqual(entrada["candidato_id"], self.ejercicio_existente.pk)

    def test_archivo_no_valido_lanza_importacioninvalida(self):
        archivo_roto = SimpleUploadedFile("plan.xlsx", b"esto no es un xlsx")
        with self.assertRaises(ImportacionInvalida):
            previsualizar_importacion_plantillas(
                gimnasio=self.gimnasio, archivo=archivo_roto, usuario=self.usuario,
            )


class ConfirmarImportacionPlantillasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([1, "sentadila", 3, "10"])
        self.importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )

    def _decisiones_completas(self, accion_sentadila="usar_existente"):
        return {
            "hojas": [{"incluir": True, "objetivo": "Hipertrofia", "nivel": "principiante"}],
            "ejercicios": {
                "press de banca": {"accion": "crear_nuevo", "grupo_muscular": "pecho"},
                "sentadila": {
                    "accion": accion_sentadila,
                    "ejercicio_id": self.ejercicio_existente.pk if accion_sentadila == "usar_existente" else None,
                    "grupo_muscular": "piernas" if accion_sentadila == "crear_nuevo" else None,
                },
            },
        }

    def test_crea_una_plantilla_por_hoja_incluida(self):
        plantillas = confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.assertEqual(len(plantillas), 1)
        self.assertEqual(plantillas[0].nombre, "Hombres")
        self.assertEqual(plantillas[0].objetivo, "Hipertrofia")
        self.assertEqual(plantillas[0].nivel, "principiante")
        self.assertEqual(plantillas[0].dias_por_semana, 1)
        self.assertEqual(plantillas[0].items.count(), 2)

    def test_usar_existente_no_duplica_el_ejercicio(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(accion_sentadila="usar_existente"),
        )
        self.assertEqual(Ejercicio.objects.filter(nombre__iexact="sentadilla").count(), 1)
        item_sentadilla = RutinaPlantillaItem.objects.get(ejercicio=self.ejercicio_existente)
        self.assertEqual(item_sentadilla.series, 3)

    def test_crear_nuevo_crea_exactamente_un_ejercicio(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.assertEqual(
            Ejercicio.objects.filter(gimnasio=self.gimnasio, nombre="Press de banca").count(), 1
        )

    def test_marca_la_importacion_como_confirmada(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.importacion.refresh_from_db()
        self.assertEqual(self.importacion.estado, Importacion.Estado.CONFIRMADA)
        self.assertIsNotNone(self.importacion.confirmado_en)

    def test_confirmar_dos_veces_falla_sin_duplicar(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio,
                decisiones=self._decisiones_completas(),
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 1)

    def test_importacion_de_otro_gimnasio_falla(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=otro_gimnasio,
                decisiones=self._decisiones_completas(),
            )

    def test_hoja_no_incluida_no_crea_plantilla(self):
        decisiones = self._decisiones_completas()
        decisiones["hojas"][0]["incluir"] = False
        plantillas = confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
        )
        self.assertEqual(plantillas, [])
        self.assertEqual(RutinaPlantilla.objects.count(), 0)

    def test_decisiones_hojas_incompletas_falla(self):
        # Simula un form de confirmación (Tarea 9) donde faltó la decisión
        # de una hoja -- p. ej. un checkbox sin marcar que no llegó en el
        # POST. No debe saltearse en silencio.
        decisiones = self._decisiones_completas()
        decisiones["hojas"] = []
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 0)
        self.importacion.refresh_from_db()
        self.assertEqual(self.importacion.estado, Importacion.Estado.EN_REVISION)

    def test_grupo_muscular_invalido_falla(self):
        decisiones = self._decisiones_completas()
        decisiones["ejercicios"]["press de banca"]["grupo_muscular"] = "banana"
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )

    def test_nivel_invalido_falla(self):
        decisiones = self._decisiones_completas()
        decisiones["hojas"][0]["nivel"] = "experto-supremo"
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )

    def test_usar_existente_con_ejercicio_de_otro_gimnasio_falla(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        ejercicio_de_otro_gimnasio = Ejercicio.objects.create(
            gimnasio=otro_gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        decisiones = self._decisiones_completas(accion_sentadila="usar_existente")
        decisiones["ejercicios"]["sentadila"]["ejercicio_id"] = ejercicio_de_otro_gimnasio.pk
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 0)

    def test_mismo_ejercicio_nuevo_en_dos_hojas_crea_uno_solo(self):
        # A diferencia del fixture de setUp (una sola hoja), acá el mismo
        # nombre aparece en DOS hojas distintas -- el memo de
        # `_obtener_ejercicio` tiene que estar scopeado a todo el confirm,
        # no por hoja.
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Peso muerto", 4, "8-12"])
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Peso muerto", 3, "10"])
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )
        decisiones = {
            "hojas": [
                {"incluir": True, "objetivo": "Fuerza", "nivel": "principiante"},
                {"incluir": True, "objetivo": "Fuerza", "nivel": "principiante"},
            ],
            "ejercicios": {
                "peso muerto": {"accion": "crear_nuevo", "grupo_muscular": "piernas"},
            },
        }
        plantillas = confirmar_importacion_plantillas(
            importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
        )
        self.assertEqual(
            Ejercicio.objects.filter(gimnasio=self.gimnasio, nombre="Peso muerto").count(), 1
        )
        ejercicio = Ejercicio.objects.get(gimnasio=self.gimnasio, nombre="Peso muerto")
        items = RutinaPlantillaItem.objects.filter(ejercicio=ejercicio)
        self.assertEqual(items.count(), 2)
        self.assertEqual({p.pk for p in plantillas}, set(items.values_list("rutina_id", flat=True)))

    def test_falla_a_mitad_de_transaccion_no_deja_datos_parciales(self):
        # Dos hojas: la primera es válida y alcanzaría a crear su plantilla
        # e ítems antes de que la segunda dispare la validación de grupo
        # muscular. Todo el atomic() tiene que revertirse, no solo la
        # segunda hoja.
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Press de banca", 4, "8-12"])
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Peso muerto", 3, "8"])
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )
        ejercicios_antes = Ejercicio.objects.count()
        decisiones = {
            "hojas": [
                {"incluir": True, "objetivo": "Hipertrofia", "nivel": "principiante"},
                {"incluir": True, "objetivo": "Fuerza", "nivel": "principiante"},
            ],
            "ejercicios": {
                "press de banca": {"accion": "crear_nuevo", "grupo_muscular": "pecho"},
                "peso muerto": {"accion": "crear_nuevo", "grupo_muscular": "banana"},
            },
        }
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 0)
        self.assertEqual(RutinaPlantillaItem.objects.count(), 0)
        self.assertEqual(Ejercicio.objects.count(), ejercicios_antes)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)


class ImportacionBibliotecaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

    def _archivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular", "Video"])
        ws.append(["Press de banca", "Pecho", "https://youtube.com/x"])
        ws.append(["sentadila", "Piernas", ""])  # typo de la ya existente
        return _archivo_xlsx(wb)

    def test_previsualizar_no_crea_ejercicios(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        self.assertEqual(importacion.tipo, Importacion.Tipo.BIBLIOTECA)
        self.assertEqual(Ejercicio.objects.count(), 1)  # solo la que ya existía

    def test_previsualizar_resuelve_grupo_muscular_automaticamente(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        item = next(i for i in importacion.resultado["items"] if i["nombre_original"] == "Press de banca")
        self.assertEqual(item["grupo_muscular_resuelto"], "pecho")

    def test_confirmar_crea_solo_los_ejercicios_nuevos(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        creados = confirmar_importacion_biblioteca(
            importacion=importacion, gimnasio=self.gimnasio,
            decisiones={"items": {
                "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
                "sentadila": {"incluir": False, "grupo_muscular": None},
            }},
        )
        self.assertEqual(len(creados), 1)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio).count(), 2)

    def test_confirmar_dos_veces_falla(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        decisiones = {"items": {
            "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
            "sentadila": {"incluir": False, "grupo_muscular": None},
        }}
        confirmar_importacion_biblioteca(importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones)
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_biblioteca(importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones)

    def test_confirmar_grupo_muscular_invalido_no_crea_nada(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        ejercicios_antes = Ejercicio.objects.count()
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio,
                decisiones={"items": {
                    "press de banca": {"incluir": True, "grupo_muscular": "banana"},
                    "sentadila": {"incluir": False, "grupo_muscular": None},
                }},
            )
        self.assertEqual(Ejercicio.objects.count(), ejercicios_antes)

    def test_confirmar_con_gimnasio_distinto_falla(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro Gym", slug="otro-gym")
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=otro_gimnasio,
                decisiones={"items": {
                    "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
                    "sentadila": {"incluir": False, "grupo_muscular": None},
                }},
            )

    def test_confirmar_decision_faltante_da_error_claro(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio,
                decisiones={"items": {
                    "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
                    # falta la decisión de "sentadila"
                }},
            )

    def test_confirmar_match_ambiguo_crea_nuevo_ejercicio(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        item = next(i for i in importacion.resultado["items"] if i["nombre_original"] == "sentadila")
        self.assertEqual(item["match"]["tipo"], "ambiguo")
        creados = confirmar_importacion_biblioteca(
            importacion=importacion, gimnasio=self.gimnasio,
            decisiones={"items": {
                "press de banca": {"incluir": False, "grupo_muscular": None},
                "sentadila": {"incluir": True, "grupo_muscular": "piernas"},
            }},
        )
        self.assertEqual(len(creados), 1)
        self.assertEqual(
            Ejercicio.objects.filter(gimnasio=self.gimnasio, nombre="sentadila").count(), 1,
        )


class SubirPlantillasFormTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")

    def test_acepta_xlsx(self):
        wb = openpyxl.Workbook()
        archivo = _archivo_xlsx(wb)
        from importaciones.forms import SubirPlantillasForm
        form = SubirPlantillasForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertTrue(form.is_valid(), form.errors)

    def test_rechaza_extension_invalida(self):
        archivo = SimpleUploadedFile("plan.csv", b"a,b,c", content_type="text/csv")
        from importaciones.forms import SubirPlantillasForm
        form = SubirPlantillasForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)

    def test_biblioteca_tambien_valida_extension(self):
        archivo = SimpleUploadedFile("plan.txt", b"nada")
        from importaciones.forms import SubirBibliotecaForm
        form = SubirBibliotecaForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertFalse(form.is_valid())


class HojaMetadataFormSetTests(SimpleTestCase):
    def test_requiere_objetivo_y_nivel(self):
        from importaciones.forms import HojaMetadataFormSet
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "", "form-0-nivel": "",
        }
        formset = HojaMetadataFormSet(datos)
        self.assertFalse(formset.is_valid())

    def test_valido_con_todos_los_campos(self):
        from importaciones.forms import HojaMetadataFormSet
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
        }
        formset = HojaMetadataFormSet(datos)
        self.assertTrue(formset.is_valid(), formset.errors)


class ResolucionEjercicioFormSetTests(SimpleTestCase):
    def test_crear_nuevo_requiere_grupo_muscular(self):
        from importaciones.forms import ResolucionEjercicioFormSet
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_normalizado": "hip thrust", "form-0-accion": "crear_nuevo",
            "form-0-grupo_muscular": "",
        }
        formset = ResolucionEjercicioFormSet(datos)
        self.assertFalse(formset.is_valid())

    def test_usar_existente_no_requiere_grupo_muscular(self):
        from importaciones.forms import ResolucionEjercicioFormSet
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_normalizado": "sentadila", "form-0-accion": "usar_existente",
            "form-0-ejercicio_existente_id": "7", "form-0-grupo_muscular": "",
        }
        formset = ResolucionEjercicioFormSet(datos)
        self.assertTrue(formset.is_valid(), formset.errors)


@override_settings(ROOT_URLCONF="importaciones.tests")
class ImportacionPlantillasViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF)

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF)

        self.usuario_alumno = User.objects.create_user(username="usuario_alumno", password="clave12345")
        Perfil.objects.create(usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO)

    def _archivo_valido(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        return _archivo_xlsx(wb)

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(reverse("importaciones:plantillas_subir"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_alumno_recibe_403(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        response = self.client.get(reverse("importaciones:plantillas_subir"))
        self.assertEqual(response.status_code, 403)

    def test_subir_archivo_invalido_no_crea_importacion(self):
        self.client.login(username="staff_a", password="clave12345")
        archivo = SimpleUploadedFile("plan.txt", b"nada")
        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": archivo},
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Importacion.objects.count(), 0)

    def test_preview_de_otro_gimnasio_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_b, tipo=Importacion.Tipo.PLANTILLAS, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_de_importacion_ya_confirmada_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS,
            estado=Importacion.Estado.CONFIRMADA, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_flujo_completo_subir_preview_confirmar(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": self._archivo_valido()},
        )
        self.assertEqual(response.status_code, 302)
        importacion = Importacion.objects.get()
        self.assertRedirects(
            response, reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hombres")
        self.assertContains(response, "Press de banca")

        datos_confirmacion = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": "1", "ejercicios-INITIAL_FORMS": "1",
            "ejercicios-0-nombre_normalizado": "press de banca",
            "ejercicios-0-accion": "crear_nuevo",
            "ejercicios-0-grupo_muscular": "pecho",
        }
        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos_confirmacion,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RutinaPlantilla.objects.count(), 1)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.CONFIRMADA)

        # Reabrir el preview de una importación ya confirmada da 404.
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_descartar_marca_la_importacion_como_descartada(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:plantillas_descartar", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 302)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.DESCARTADA)


@override_settings(ROOT_URLCONF="importaciones.tests")
class ImportacionBibliotecaViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF)

        self.usuario_alumno = User.objects.create_user(username="usuario_alumno", password="clave12345")
        Perfil.objects.create(usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO)

    def _archivo_valido(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])
        return _archivo_xlsx(wb)

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(reverse("importaciones:biblioteca_subir"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_alumno_recibe_403(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        response = self.client.get(reverse("importaciones:biblioteca_subir"))
        self.assertEqual(response.status_code, 403)

    def test_subir_archivo_invalido_no_crea_importacion(self):
        self.client.login(username="staff_a", password="clave12345")
        archivo = SimpleUploadedFile("ejercicios.txt", b"nada")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": archivo},
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Importacion.objects.count(), 0)

    def test_preview_de_otro_gimnasio_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_b, tipo=Importacion.Tipo.BIBLIOTECA, resultado={"items": []},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_de_importacion_ya_confirmada_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.BIBLIOTECA,
            estado=Importacion.Estado.CONFIRMADA, resultado={"items": []},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_flujo_completo_subir_preview_confirmar(self):
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])

        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        self.assertEqual(response.status_code, 302)
        importacion = Importacion.objects.get()
        self.assertRedirects(
            response, reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Press de banca")

        datos = {
            "form-TOTAL_FORMS": "0", "form-INITIAL_FORMS": "0",
            # "Press de banca" resolvió grupo_muscular automáticamente ("pecho")
            # y no necesita entrada en el formset de resolución manual.
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Press de banca").count(), 1)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.CONFIRMADA)

        # Reabrir el preview de una importación ya confirmada da 404.
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_flujo_con_resolucion_manual_de_grupo_muscular(self):
        # "hip thrust" no tiene grupo muscular en el archivo -> requiere
        # entrada en el formset de resolución manual del preview.
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Hip thrust"])

        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()

        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "hip thrust")

        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-valor_original": "hip thrust",
            "form-0-grupo_muscular": "piernas",
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Hip thrust").count(), 1)
        self.assertEqual(
            Ejercicio.objects.get(nombre="Hip thrust").grupo_muscular, "piernas"
        )

    def test_descartar_marca_la_importacion_como_descartada_y_redirige_a_subir(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.BIBLIOTECA, resultado={"items": []},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_descartar", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("importaciones:biblioteca_subir"))
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.DESCARTADA)
