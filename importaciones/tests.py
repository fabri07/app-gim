"""Tests de `importaciones`. Ver `rutinas/tests.py` para el estilo de
fixtures de este repo."""

import io

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

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
    previsualizar_importacion_plantillas,
)
from rutinas.models import RutinaPlantilla
from tenants.models import Gimnasio


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
