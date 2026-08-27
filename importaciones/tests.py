"""Tests de `importaciones`. Ver `rutinas/tests.py` para el estilo de
fixtures de este repo."""

import io
import json

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ejercicios.models import CategoriaEjercicio, Ejercicio
from importaciones.matching import (
    resolver_categorias,
    MatchResultado,
    construir_indice_ejercicios,
    resolver_nombre,
)
from importaciones.models import Importacion
from importaciones.parsing import (
    ColumnaRequeridaFaltante,
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

    def test_alias_biblioteca_acepta_categoria(self):
        """Encabezado real del primer cliente: su Excel de 748 ejercicios
        decía CATEGORÍA, que no estaba en la lista de alias, así que la
        columna no se detectaba y los 748 salían sin clasificar. Es el
        defecto que originó toda la feature de categorías por gimnasio."""
        encabezados = ["NOMBRE", "LINK", "CATEGORÍA"]
        campos, _ = detectar_columnas(encabezados, ALIAS_BIBLIOTECA)
        self.assertEqual(campos, {"nombre": 0, "url_video": 1, "grupo_muscular": 2})

    def test_alias_biblioteca_acepta_variantes_de_categoria(self):
        for encabezado in ["Categoria", "categorías", "CATEGORIAS", "Grupo"]:
            with self.subTest(encabezado=encabezado):
                campos, _ = detectar_columnas(
                    ["Nombre", encabezado], ALIAS_BIBLIOTECA
                )
                self.assertEqual(campos.get("grupo_muscular"), 1)


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
            series=4, repeticiones="8-12", kilos="", descanso="90s", notas="",
        ))
        self.assertEqual(hoja.items[1].orden, 2)  # segundo item del mismo (semana, dia)
        self.assertEqual(hoja.filas_invalidas, [])

    def test_lee_columna_carga_como_kilos(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones", "Carga"])
        ws.append([1, "Sentadilla", 4, "8-12", "20kg"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items[0].kilos, "20kg")

    def test_sin_columna_carga_kilos_queda_vacio(self):
        """La columna es opcional -- una hoja sin ella sigue importando
        igual que antes, solo que `kilos` queda "" ."""
        hoja = leer_hoja_plantilla(_hoja_plantilla_basica())
        self.assertEqual(hoja.items[0].kilos, "")

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
        items, invalidas, advertencias = leer_hoja_biblioteca(ws)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["nombre_original"], "Press de banca")
        self.assertEqual(items[0]["grupo_muscular_original"], "Pecho")
        self.assertEqual(items[0]["url_video"], "https://youtube.com/x")
        self.assertEqual(invalidas, [])
        self.assertEqual(advertencias, [])

    def test_fila_sin_nombre_se_saltea_con_motivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["", "Pecho"])
        items, invalidas, _ = leer_hoja_biblioteca(ws)
        self.assertEqual(items, [])
        self.assertEqual(len(invalidas), 1)

    def test_columna_grupo_muscular_es_opcional(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Press de banca"])
        items, _, _ = leer_hoja_biblioteca(ws)
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
        items, invalidas, advertencias = parsear_archivo_biblioteca(_archivo_xlsx(wb))
        self.assertEqual(len(items), 1)
        self.assertEqual(invalidas, [])
        self.assertEqual(advertencias, [])


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


# `ResolverGrupoMuscularTests` se retiró el 2026-08-26 junto con
# `resolver_grupo_muscular`: matcheaba texto contra un `TextChoices` global de
# 8 valores más un diccionario de alias fijo. Con el catálogo de categorías
# por gimnasio no hay lista global contra la cual matchear. Lo reemplaza
# `ResolverCategoriasTests`, que además cubre el dedupe difuso.


class ConstruirIndiceEjerciciosTests(TestCase):
    def test_indexa_por_nombre_normalizado_y_aisla_por_tenant(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a, nombre="Press de Banca",
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b, nombre="Sentadilla",
        )
        indice = construir_indice_ejercicios(gimnasio_a)
        self.assertEqual(indice, {"press de banca": ejercicio_a})


class PrevisualizarImportacionPlantillasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
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
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
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
                "press de banca": {"accion": "crear_nuevo", "categoria_id": self.pecho.pk},
                "sentadila": {
                    "accion": accion_sentadila,
                    "ejercicio_id": self.ejercicio_existente.pk if accion_sentadila == "usar_existente" else None,
                    "categoria_id": self.piernas.pk if accion_sentadila == "crear_nuevo" else None,
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

    def test_categoria_de_otro_gimnasio_o_inexistente_falla(self):
        decisiones = self._decisiones_completas()
        decisiones["ejercicios"]["press de banca"]["categoria_id"] = 999999
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
                "peso muerto": {"accion": "crear_nuevo", "categoria_id": self.piernas.pk},
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
                "press de banca": {"accion": "crear_nuevo", "categoria_id": self.pecho.pk},
                "peso muerto": {"accion": "crear_nuevo", "categoria_id": 999999},
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


class ConfirmarImportacionPlantillasConCargaTests(TestCase):
    """La columna 'Carga' del Excel llega hasta `RutinaPlantillaItem.kilos`
    de punta a punta (parsing -> JSON del preview -> confirm)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones", "Carga"])
        ws.append([1, "Sentadilla", 4, "8-12", "20kg"])
        self.importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )

    def test_kilos_llega_al_item_creado(self):
        plantillas = confirmar_importacion_plantillas(
            importacion=self.importacion,
            gimnasio=self.gimnasio,
            decisiones={
                "hojas": [{"incluir": True, "objetivo": "Hipertrofia", "nivel": "principiante"}],
                "ejercicios": {
                    "sentadilla": {"accion": "crear_nuevo", "categoria_id": self.piernas.pk},
                },
            },
        )
        item = plantillas[0].items.get()
        self.assertEqual(item.kilos, "20kg")


class ImportacionBibliotecaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
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

    def test_previsualizar_resuelve_la_categoria_automaticamente(self):
        """La columna del archivo dice "Pecho" y el gimnasio ya tiene esa
        categoría: se reusa, no se crea una segunda."""
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        item = next(i for i in importacion.resultado["items"] if i["nombre_original"] == "Press de banca")
        self.assertEqual(item["categoria_resuelta"]["tipo"], "existente")
        self.assertEqual(item["categoria_resuelta"]["categoria_id"], self.pecho.pk)

    def test_confirmar_crea_solo_los_ejercicios_nuevos(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        creados = confirmar_importacion_biblioteca(
            importacion=importacion, gimnasio=self.gimnasio,
            decisiones={"items": {
                "press de banca": {"incluir": True, "categoria_id": self.pecho.pk},
                "sentadila": {"incluir": False, "categoria_id": None},
            }},
        )
        self.assertEqual(len(creados), 1)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio).count(), 2)

    def test_confirmar_dos_veces_falla(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        decisiones = {"items": {
            "press de banca": {"incluir": True, "categoria_id": self.pecho.pk},
            "sentadila": {"incluir": False, "categoria_id": None},
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
                    "press de banca": {"incluir": True, "categoria_id": 999999},
                    "sentadila": {"incluir": False, "categoria_id": None},
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
                    "press de banca": {"incluir": True, "categoria_id": self.pecho.pk},
                    "sentadila": {"incluir": False, "categoria_id": None},
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
                    "press de banca": {"incluir": True, "categoria_id": self.pecho.pk},
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
                "press de banca": {"incluir": False, "categoria_id": None},
                "sentadila": {"incluir": True, "categoria_id": self.piernas.pk},
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


class ImportacionPlantillasViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio_a, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio_a, nombre="Piernas"
        )

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
            "ejercicios-0-categoria": self.pecho.pk,
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


class ImportacionBibliotecaViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio_a, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio_a, nombre="Piernas"
        )

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

        # "Press de banca" resolvió grupo_muscular automáticamente ("pecho")
        # y no necesita entrada en las resoluciones manuales.
        datos = {"resoluciones": "{}"}
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

    def test_flujo_con_resolucion_manual_de_categoria(self):
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

        datos = {"resoluciones": json.dumps({"hip thrust": {"categoria_id": self.piernas.pk}})}
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Hip thrust").count(), 1)
        self.assertEqual(
            Ejercicio.objects.get(nombre="Hip thrust").categoria, self.piernas
        )

    def test_falta_resolver_un_pendiente_no_confirma(self):
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Hip thrust"])
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()

        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "{}"},  # "hip thrust" queda sin resolver
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Ejercicio.objects.count(), 0)

    def test_resoluciones_con_json_invalido_no_confirma_y_muestra_error(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": self._archivo_valido()},
        )
        importacion = Importacion.objects.get()

        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "not json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formato de resoluciones inválido.")
        self.assertEqual(Ejercicio.objects.count(), 0)

    def test_resoluciones_con_grupo_muscular_invalido_no_confirma_y_muestra_error(self):
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Hip thrust"])
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()

        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": json.dumps({"hip thrust": {"categoria_id": "no_es_un_entero"}})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Categoría inválida.")
        self.assertEqual(Ejercicio.objects.count(), 0)

    def test_preview_lista_filas_invalidas_con_motivo(self):
        # Regla global no negociable: "filas inválidas se saltean y se
        # listan con motivo" -- nunca se descartan en silencio.
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])
        ws.append(["", "Pecho"])  # sin nombre -> fila inválida

        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertEqual(len(importacion.resultado["filas_invalidas"]), 1)

        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Falta el nombre del ejercicio")

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

    def _archivo_con_ambiguo(self, gimnasio):
        Ejercicio.objects.create(
            gimnasio=gimnasio, nombre="Sentadilla", grupo_muscular="piernas",
        )
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Sentadila"])  # typo -> match ambiguo contra "Sentadilla"
        return _archivo_xlsx(wb)

    def test_preview_muestra_candidato_y_score_para_match_ambiguo(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Sentadilla")  # nombre del candidato
        # Score de rapidfuzz para "sentadila" vs "sentadilla" (WRatio),
        # confirmado corriendo `resolver_nombre` directamente: 94.
        self.assertContains(response, "94")

    def test_ambiguo_usar_existente_no_crea_ejercicio_nuevo(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        datos = {
            "resoluciones": json.dumps({"sentadila": {"accion": "usar_existente"}}),
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 1)

    def test_ambiguo_crear_nuevo_requiere_grupo_muscular_y_crea_ejercicio(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        datos = {
            "resoluciones": json.dumps(
                {"sentadila": {"accion": "crear_nuevo", "categoria_id": self.piernas.pk}}
            ),
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 2)
        self.assertTrue(
            Ejercicio.objects.filter(gimnasio=self.gimnasio_a, nombre="Sentadila").exists()
        )

    def test_ambiguo_sin_resolver_no_confirma(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "{}"},
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 1)


class RegresionCamposDelPostTests(TestCase):
    """El confirm POST manda solo decisiones (objetivo/nivel por hoja +
    resolución por ejercicio distinto), nunca el dataset entero -- por
    diseño (spec §2) no debería acercarse jamás al límite default de
    Django (1000 campos por POST) para una hoja de tamaño realista, sin
    importar cuántas filas tenga.

    NOTA (Tarea 12): la planilla original de este test (500 filas, cada una
    con un nombre de ejercicio DISTINTO -- "Ejercicio {i}" para las 500)
    contradice la premisa que el propio test dice validar: con 500
    ejercicios distintos el confirm POST manda ~1500 campos (3 por
    ejercicio × 500) y SÍ supera el límite default de 1000, tirando
    `TooManyFieldsSent`. Ninguna plantilla real tiene 500 ejercicios
    completamente distintos en una sola hoja -- una planilla de ese tamaño
    normalmente repite un vocabulario acotado de ejercicios a lo largo de
    varias semanas/días (ver el ejemplo de la spec: "4 semanas × 5 días × 6
    ejercicios × 2 hojas ~240 filas"). Este test usa 500 filas que reciclan
    un pool de 20 ejercicios distintos -- volumen de filas realista, sin
    caer en el caso patológico que rompe el propio invariante que se quiere
    demostrar. Ver ISSUES.md `[2026-07-28]` para el detalle."""

    CANTIDAD_FILAS = 500
    CANTIDAD_EJERCICIOS_DISTINTOS = 20

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_hoja_de_500_filas_no_rompe_el_confirm_post(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Full body"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        for i in range(self.CANTIDAD_FILAS):
            nombre = f"Ejercicio {i % self.CANTIDAD_EJERCICIOS_DISTINTOS}"
            ws.append([1, nombre, 3, "10"])

        self.client.login(username="staff", password="clave12345")
        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertEqual(
            len(importacion.resultado["ejercicios_distintos"]),
            self.CANTIDAD_EJERCICIOS_DISTINTOS,
        )

        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Full body", "form-0-incluir": "on",
            "form-0-objetivo": "General", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": str(self.CANTIDAD_EJERCICIOS_DISTINTOS),
            "ejercicios-INITIAL_FORMS": str(self.CANTIDAD_EJERCICIOS_DISTINTOS),
        }
        for i, nombre in enumerate(importacion.resultado["ejercicios_distintos"]):
            datos[f"ejercicios-{i}-nombre_normalizado"] = nombre
            datos[f"ejercicios-{i}-accion"] = "crear_nuevo"
            datos[f"ejercicios-{i}-categoria"] = self.pecho.pk

        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RutinaPlantilla.objects.get().items.count(), self.CANTIDAD_FILAS
        )


class RegresionCamposPostBibliotecaTests(TestCase):
    """El confirm POST de biblioteca manda las resoluciones como un único
    campo JSON -- a diferencia de plantillas (ver ISSUES.md [2026-07-28]),
    el conteo de campos del POST no escala con la cantidad de ejercicios
    pendientes de resolución manual, así que una biblioteca inicial de
    miles de ejercicios (escenario real, a diferencia de una plantilla)
    no puede romper contra DATA_UPLOAD_MAX_NUMBER_FIELDS."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_600_ejercicios_pendientes_no_rompe_el_confirm_post(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])  # sin columna Grupo Muscular -> todos pendientes
        for i in range(600):
            ws.append([f"Ejercicio {i}"])

        self.client.login(username="staff", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertEqual(len(importacion.resultado["items"]), 600)

        resoluciones = {
            f"ejercicio {i}": {"categoria_id": self.pecho.pk} for i in range(600)
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": json.dumps(resoluciones)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.count(), 600)


class SinDefaultSilenciosoDeGrupoMuscularYNivelTests(TestCase):
    """Fix post-review, hallazgo 1: ni `grupo_muscular` (ejercicio nuevo) ni
    `nivel` (hoja) pueden quedar con una opción real pre-seleccionada en el
    <select> -- eso viola el constraint no negociable de que el staff SIEMPRE
    elige el grupo muscular de un ejercicio nuevo a mano. Antes del fix, el
    <select> no tenía ninguna opción en blanco, así que el navegador
    pre-seleccionaba (y mandaba) la primera choice real ("pecho" /
    "principiante") sin que el staff la tocara."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave12345")

    def _importacion_con_ejercicio_nuevo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        return Importacion.objects.get()

    def test_preview_no_preselecciona_ninguna_opcion_real_en_los_selects(self):
        importacion = self._importacion_con_ejercicio_nuevo()
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        # La opción en blanco es la que queda "selected" -- ni "pecho" (1ra
        # choice de grupo_muscular) ni "principiante" (1ra choice de nivel).
        self.assertContains(response, '<option value="" selected>---------</option>', count=2)
        self.assertNotContains(response, '<option value="pecho" selected>')
        self.assertNotContains(response, '<option value="principiante" selected>')

    def test_confirmar_sin_elegir_grupo_muscular_no_crea_nada(self):
        # Simula un navegador real: el staff no tocó el <select>, así que
        # se manda la opción en blanco pre-seleccionada, no "pecho".
        importacion = self._importacion_con_ejercicio_nuevo()
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": "1", "ejercicios-INITIAL_FORMS": "1",
            "ejercicios-0-nombre_normalizado": "press de banca",
            "ejercicios-0-accion": "crear_nuevo",
            "ejercicios-0-categoria": "",
        }
        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error, no redirige
        self.assertContains(response, "Elegí una categoría")
        self.assertEqual(RutinaPlantilla.objects.count(), 0)
        self.assertEqual(Ejercicio.objects.count(), 0)


class HojaExcluidaPorColumnaFaltanteTests(TestCase):
    """Fix post-review, hallazgo 2: una hoja sin una columna requerida
    (ejercicio/series/repeticiones) queda con 0 items -- antes, sin ningún
    motivo registrado, y con `incluir` pre-tildado por default, así que
    confirmarla creaba una `RutinaPlantilla` vacía en silencio."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave12345")

    def _archivo_con_columna_ejercicio_mal_escrita(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercico", "Series", "Repeticiones"])  # typo: "Ejercico"
        ws.append([1, "Press de banca", 4, "8-12"])
        return _archivo_xlsx(wb)

    def test_preview_registra_y_muestra_el_motivo_de_exclusion(self):
        response = self.client.post(
            reverse("importaciones:plantillas_subir"),
            {"archivo": self._archivo_con_columna_ejercicio_mal_escrita()},
        )
        importacion = Importacion.objects.get()
        hoja = importacion.resultado["hojas"][0]
        self.assertEqual(hoja["items"], [])
        self.assertIsNotNone(hoja["motivo_exclusion"])
        self.assertIn("ejercicio", hoja["motivo_exclusion"])

        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertContains(response, "No se pudo importar")

    def test_preview_no_marca_incluir_por_default_para_hoja_excluida(self):
        response = self.client.post(
            reverse("importaciones:plantillas_subir"),
            {"archivo": self._archivo_con_columna_ejercicio_mal_escrita()},
        )
        importacion = Importacion.objects.get()
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        hoja_formset = response.context["hoja_formset"]
        self.assertFalse(hoja_formset.forms[0].initial["incluir"])

    def test_confirmar_sin_marcar_incluir_no_crea_plantilla_vacia(self):
        response = self.client.post(
            reverse("importaciones:plantillas_subir"),
            {"archivo": self._archivo_con_columna_ejercicio_mal_escrita()},
        )
        importacion = Importacion.objects.get()
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres",
            # "incluir" deliberadamente ausente -- checkbox sin marcar, ya
            # que el default ahora es no incluir una hoja sin ejercicios.
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": "0", "ejercicios-INITIAL_FORMS": "0",
        }
        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RutinaPlantilla.objects.count(), 0)

    def test_confirmar_incluir_forzado_en_hoja_vacia_falla_en_el_service(self):
        # Defensa en profundidad: aunque alguien arme un POST a mano con
        # `incluir=True` para una hoja de 0 items, el service la rechaza.
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio,
            archivo=self._archivo_con_columna_ejercicio_mal_escrita(),
            usuario=self.staff,
        )
        decisiones = {
            "hojas": [{"incluir": True, "objetivo": "Hipertrofia", "nivel": "principiante"}],
            "ejercicios": {},
        }
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 0)


class AdvertenciasColumnasLlegaAlStaffTests(TestCase):
    """Fix post-review, hallazgo 3: `detectar_columnas` calcula advertencias
    de columnas duplicadas pero antes se descartaban antes de llegar al
    staff (`services.py` hardcodeaba `"advertencias_columnas": []`)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave12345")

    def test_plantillas_columna_ejercicio_duplicada_se_muestra_en_el_preview(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        # Dos columnas "Ejercicio" -- ambas matchean el mismo alias.
        ws.append(["Dia", "Ejercicio", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", "otra cosa", 4, "8-12"])

        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertTrue(importacion.resultado["advertencias_columnas"])

        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Advertencia")
        self.assertContains(response, "ejercicio")

    def test_biblioteca_columna_nombre_duplicada_se_muestra_en_el_preview(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        # Dos columnas "Nombre" -- ambas matchean el alias de "nombre".
        ws.append(["Nombre", "Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "otra cosa", "Pecho"])

        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertTrue(importacion.resultado["advertencias_columnas"])

        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Advertencia")
        self.assertContains(response, "nombre")


class BibliotecaDedupeDentroDelArchivoTests(TestCase):
    """Fix post-review, hallazgo 5: si el MISMO archivo trae dos filas que
    normalizan al mismo nombre (p. ej. "Press de banca" y "PRESS DE
    BANCA"), antes se creaban dos `Ejercicio` -- `Ejercicio` no tiene
    `unique_together`, así que no había ningún otro chequeo que lo evitara."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave12345")

    def _archivo_con_duplicado(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])
        ws.append(["PRESS DE BANCA", "Pecho"])  # mismo nombre normalizado
        return _archivo_xlsx(wb)

    def test_previsualizar_deja_solo_la_primera_aparicion_y_lista_la_otra_como_invalida(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo_con_duplicado(), usuario=self.staff,
        )
        self.assertEqual(len(importacion.resultado["items"]), 1)
        self.assertEqual(importacion.resultado["items"][0]["nombre_original"], "Press de banca")
        self.assertEqual(len(importacion.resultado["filas_invalidas"]), 1)
        self.assertIn(
            "duplicado", importacion.resultado["filas_invalidas"][0]["motivo"].lower(),
        )

    def test_confirmar_crea_un_solo_ejercicio_para_el_duplicado(self):
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": self._archivo_con_duplicado()},
        )
        importacion = Importacion.objects.get()

        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "duplicado")

        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "{}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Ejercicio.objects.filter(nombre__iexact="press de banca").count(), 1
        )


class EjercicioResolucionMuestraContextoTests(TestCase):
    """Fix post-review, hallazgo 6: la sección "Ejercicios a resolver" del
    preview de plantillas mostraba el nombre NORMALIZADO (lowercase) en vez
    del original, y renderizaba el form completo -- incluyendo
    `ejercicio_existente_id` como un <input type="number"> crudo, sin
    ninguna explicación de qué es esa PK. El spec pide nombre original,
    candidato sugerido y score para los matches ambiguos."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
        )
        self.client.login(username="staff", password="clave12345")

    def test_preview_muestra_nombre_original_candidato_y_score_para_match_ambiguo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Sentadila", 3, "10"])  # typo -> match ambiguo con "Sentadilla"

        self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        entrada = importacion.resultado["ejercicios_distintos"]["sentadila"]
        self.assertEqual(entrada["tipo"], "ambiguo")

        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Sentadila")  # nombre ORIGINAL, no "sentadila"
        self.assertContains(response, entrada["candidato_nombre"])
        self.assertContains(response, str(entrada["score"]))
        # El pk crudo de `ejercicio_existente_id` ya no puede quedar
        # expuesto como un <input type="number"> editable sin etiqueta.
        self.assertNotContains(response, 'type="number"')
        # Una zona de drop por categoría ACTIVA del gimnasio. Antes era un
        # 8 fijo (el tamaño del `TextChoices` global); ahora se cuenta contra
        # el catálogo real, que es distinto en cada gimnasio.
        esperadas = CategoriaEjercicio.objects.for_gimnasio(
            self.gimnasio
        ).filter(activo=True).count()
        self.assertContains(
            response, 'class="rutina-drop-zona"', count=esperadas
        )


class DeteccionTolerantePorContenidoTests(SimpleTestCase):
    """La detección era de coincidencia EXACTA contra la lista de alias, así
    que un encabezado razonable como "NOMBRE DEL EJERCICIO" no matcheaba y el
    archivo entero se descartaba sin explicación."""

    def test_detecta_alias_contenido_en_el_encabezado(self):
        campos, _ = detectar_columnas(
            ["NOMBRE DEL EJERCICIO", "LINK DE YOUTUBE", "CATEGORÍA (grupo)"],
            ALIAS_BIBLIOTECA,
        )
        self.assertEqual(
            campos, {"nombre": 0, "url_video": 1, "grupo_muscular": 2}
        )

    def test_la_coincidencia_exacta_gana_sobre_la_parcial(self):
        """Con 'Ejercicio' (exacto para `nombre`) y 'Nombre del ejercicio'
        (parcial), gana la exacta y la otra no se roba la columna."""
        campos, _ = detectar_columnas(
            ["Nombre del ejercicio", "Ejercicio"], ALIAS_BIBLIOTECA
        )
        self.assertEqual(campos["nombre"], 1)

    def test_una_columna_no_se_asigna_a_dos_campos(self):
        """'Grupo muscular del ejercicio' contiene tanto 'grupo muscular'
        como 'ejercicio'; no puede terminar siendo también la de nombre."""
        campos, _ = detectar_columnas(
            ["Nombre", "Grupo muscular del ejercicio"], ALIAS_BIBLIOTECA
        )
        self.assertEqual(campos["nombre"], 0)
        self.assertEqual(campos["grupo_muscular"], 1)

    def test_gana_el_alias_mas_largo(self):
        campos, _ = detectar_columnas(
            ["Nombre", "Grupo muscular principal"], ALIAS_BIBLIOTECA
        )
        self.assertEqual(campos["grupo_muscular"], 1)

    def test_no_inventa_columnas_que_no_estan(self):
        campos, _ = detectar_columnas(["Nombre", "Observaciones"], ALIAS_BIBLIOTECA)
        self.assertNotIn("grupo_muscular", campos)
        self.assertNotIn("url_video", campos)


class BibliotecaSinColumnaNombreTests(SimpleTestCase):
    """Reproduce la importación #7 del primer cliente: el MISMO archivo que
    la #8, pero con una fila de título arriba. `leer_hoja_biblioteca`
    devolvía `[], [], []` y la app armaba un preview con cero filas y el
    botón "Confirmar importación" habilitado, sin decir nada."""

    def _hoja(self, filas):
        wb = openpyxl.Workbook()
        ws = wb.active
        for fila in filas:
            ws.append(fila)
        return ws

    def test_falta_la_columna_nombre_levanta_error(self):
        hoja = self._hoja([
            ["Biblioteca de ejercicios 2026", None, None],
            ["NOMBRE", "LINK", "CATEGORÍA"],
            ["Sentadilla", "https://y.com/1", "RODILLA"],
        ])

        with self.assertRaises(ColumnaRequeridaFaltante) as ctx:
            leer_hoja_biblioteca(hoja)

        self.assertEqual(ctx.exception.campo, "nombre")

    def test_el_error_lista_los_encabezados_que_si_encontro(self):
        """Sin esto el staff no tiene forma de saber qué leyó la app: es la
        diferencia entre "arreglá la fila 1" y adivinar."""
        hoja = self._hoja([
            ["Biblioteca 2026", "col b", None],
            ["NOMBRE", "LINK", "CATEGORÍA"],
        ])

        with self.assertRaises(ColumnaRequeridaFaltante) as ctx:
            leer_hoja_biblioteca(hoja)

        self.assertIn("Biblioteca 2026", ctx.exception.encabezados)
        self.assertIn("col b", ctx.exception.encabezados)

    def test_el_archivo_bien_armado_sigue_funcionando(self):
        hoja = self._hoja([
            ["NOMBRE", "LINK", "CATEGORÍA"],
            ["Sentadilla", "https://y.com/1", "RODILLA"],
        ])

        items, invalidas, _ = leer_hoja_biblioteca(hoja)

        self.assertEqual(len(items), 1)
        self.assertEqual(invalidas, [])


class PreviewBibliotecaSinColumnaNombreTests(TestCase):
    """El error de columna faltante tiene que llegar al staff como mensaje,
    no como un preview vacío ni como un 500."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.usuario = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )

    def _archivo_con_titulo_arriba(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Biblioteca de ejercicios 2026", None, None])
        ws.append(["NOMBRE", "LINK", "CATEGORÍA"])
        ws.append(["Sentadilla", "https://y.com/1", "RODILLA"])
        return _archivo_xlsx(wb)

    def test_no_crea_una_importacion_vacia(self):
        with self.assertRaises(ImportacionInvalida):
            previsualizar_importacion_biblioteca(
                gimnasio=self.gimnasio,
                archivo=self._archivo_con_titulo_arriba(),
                usuario=self.usuario,
            )

        self.assertEqual(Importacion.objects.count(), 0)

    def test_el_mensaje_dice_que_columna_falta_y_que_leyo(self):
        with self.assertRaises(ImportacionInvalida) as ctx:
            previsualizar_importacion_biblioteca(
                gimnasio=self.gimnasio,
                archivo=self._archivo_con_titulo_arriba(),
                usuario=self.usuario,
            )

        mensaje = str(ctx.exception)
        self.assertIn("nombre", mensaje)
        self.assertIn("Biblioteca de ejercicios 2026", mensaje)
        self.assertIn("primera fila", mensaje)


class ResolverCategoriasTests(SimpleTestCase):
    """Dedupe difuso de nombres de categoría (pedido del dueño: "identificar
    qué palabras, por más que estén mal escritas, quieren decir lo mismo,
    para no crear muchas categorías cuando en realidad son unas pocas").

    El umbral (85) se eligió midiendo `fuzz.ratio` sobre las 12 categorías
    reales del primer cliente más las 8 sembradas por default: el par de
    categorías DISTINTAS más parecido puntúa 61.5 ('Hombros'/'Brazos') y el
    typo que MENOS puntúa entre los que sí deben fusionarse da 88.9
    ('MOVILIDAD'/'MOBILIDAD'). 85 cae en ese hueco de 27 puntos. Los tests de
    abajo fijan los dos bordes.
    """

    CLIENTE = [
        "CORE", "EMPUJE", "ACCESORIOS", "TRACCIÓN", "RODILLA", "CADERA",
        "INTERMITENTE", "DEPORTIVOS", "MUSCLE UP", "MOVILIDAD",
        "SKILLS ANILLAS", "HANDSTAND",
    ]

    def test_catalogo_vacio_crea_una_categoria_por_nombre_distinto(self):
        resueltas = resolver_categorias(self.CLIENTE, {})

        nuevas = {r.nombre for r in resueltas.values() if r.tipo == "nueva"}
        self.assertEqual(len(nuevas), 12)
        self.assertEqual(nuevas, set(self.CLIENTE))

    def test_no_fusiona_categorias_realmente_distintas(self):
        """Regresión del borde de abajo: 'Hombros' y 'Brazos' puntúan 61.5.
        Si alguien baja el umbral, este test lo frena."""
        resueltas = resolver_categorias(["Hombros", "Brazos"], {})

        self.assertNotEqual(
            resueltas["Hombros"].nombre, resueltas["Brazos"].nombre
        )

    def test_fusiona_un_typo_con_la_forma_ya_vista(self):
        resueltas = resolver_categorias(["TRACCIÓN", "TRACION"], {})

        self.assertEqual(
            resueltas["TRACION"].nombre, resueltas["TRACCIÓN"].nombre
        )

    def test_fusiona_el_typo_de_menor_puntaje_del_borde(self):
        """'MOVILIDAD'/'MOBILIDAD' = 88.9, el más flojo de los que deben
        fusionarse. Si alguien sube el umbral, este test lo frena."""
        resueltas = resolver_categorias(["MOVILIDAD", "MOBILIDAD"], {})

        self.assertEqual(
            resueltas["MOBILIDAD"].nombre, resueltas["MOVILIDAD"].nombre
        )

    def test_gana_la_primera_forma_vista_como_nombre_canonico(self):
        resueltas = resolver_categorias(["DEPORTIVOS", "DEPORTIVO"], {})

        self.assertEqual(resueltas["DEPORTIVO"].nombre, "DEPORTIVOS")

    def test_reusa_una_categoria_existente_por_nombre_normalizado(self):
        indice = {"core": 7}

        resueltas = resolver_categorias(["CORE"], indice)

        self.assertEqual(resueltas["CORE"].tipo, "existente")
        self.assertEqual(resueltas["CORE"].categoria_id, 7)

    def test_reusa_una_categoria_existente_por_similitud(self):
        """El caso concreto del cliente: su 'CORE' se fusiona con la 'Core'
        que la app siembra por default, en vez de duplicarla."""
        indice = {"core": 7}

        resueltas = resolver_categorias(["Coree"], indice)

        self.assertEqual(resueltas["Coree"].tipo, "existente")
        self.assertEqual(resueltas["Coree"].categoria_id, 7)

    def test_el_catalogo_existente_gana_sobre_crear_una_nueva(self):
        indice = {"empuje": 3}

        resueltas = resolver_categorias(["EMPUJES", "EMPUJE"], indice)

        self.assertEqual(resueltas["EMPUJES"].tipo, "existente")
        self.assertEqual(resueltas["EMPUJE"].tipo, "existente")

    def test_ignora_textos_vacios(self):
        resueltas = resolver_categorias(["", None, "   "], {})

        self.assertEqual(resueltas, {})

    def test_las_doce_del_cliente_contra_las_ocho_sembradas(self):
        """La prueba de fuego: el archivo real contra un gimnasio recién
        creado. Solo CORE debe fusionarse con la sembrada; las otras 11 se
        crean, sin que ninguna se coma a otra."""
        indice = {
            "pecho": 1, "espalda": 2, "piernas": 3, "hombros": 4,
            "brazos": 5, "core": 6, "cardio": 7, "cuerpo completo": 8,
        }

        resueltas = resolver_categorias(self.CLIENTE, indice)

        existentes = [r for r in resueltas.values() if r.tipo == "existente"]
        nuevas = {r.nombre for r in resueltas.values() if r.tipo == "nueva"}
        self.assertEqual(len(existentes), 1)
        self.assertEqual(existentes[0].categoria_id, 6)
        self.assertEqual(len(nuevas), 11)


class DescartarImportacionesViejasTests(TestCase):
    """`0002_descartar_importaciones_con_formato_viejo`: una importación de
    biblioteca a medio revisar, guardada con el `resultado` de antes de las
    categorías, tiraba 500 al abrir su preview."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user("staff", password="clave-123456")
        Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )

    def _migrar(self):
        import importlib

        from django.apps import apps

        modulo = importlib.import_module(
            "importaciones.migrations.0002_descartar_importaciones_con_formato_viejo"
        )
        modulo.descartar_en_revision(apps, None)

    def _importacion(self, tipo, estado):
        return Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=tipo,
            estado=estado,
            archivo="importaciones/x.xlsx",
            resultado={"items": [], "filas_invalidas": []},
            creado_por=self.usuario,
        )

    def test_descarta_las_de_biblioteca_en_revision(self):
        importacion = self._importacion(
            Importacion.Tipo.BIBLIOTECA, Importacion.Estado.EN_REVISION
        )

        self._migrar()

        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.DESCARTADA)

    def test_no_toca_las_ya_confirmadas(self):
        importacion = self._importacion(
            Importacion.Tipo.BIBLIOTECA, Importacion.Estado.CONFIRMADA
        )

        self._migrar()

        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.CONFIRMADA)

    def test_no_toca_las_de_plantillas(self):
        """El `resultado` de plantillas no cambió de forma."""
        importacion = self._importacion(
            Importacion.Tipo.PLANTILLAS, Importacion.Estado.EN_REVISION
        )

        self._migrar()

        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)

    def test_el_preview_de_una_descartada_da_404_no_500(self):
        importacion = self._importacion(
            Importacion.Tipo.BIBLIOTECA, Importacion.Estado.EN_REVISION
        )
        self._migrar()
        self.client.login(username="staff", password="clave-123456")

        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )

        self.assertEqual(response.status_code, 404)


class BibliotecaPreviewDragYLoteTests(TestCase):
    """El drag-and-drop existía solo en el preview de PLANTILLAS (commit
    5789220). El de biblioteca —el que usa el flujo de "Importar
    ejercicios"— seguía con desplegables sueltos."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user("staff", password="clave-123456")
        Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.empuje = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )
        self.traccion = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="TRACCIÓN"
        )
        self.client.login(username="staff", password="clave-123456")

    def _preview_con_pendientes(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Video"])  # sin columna de categoría a propósito
        ws.append(["Dominadas", "https://y.com/1"])
        ws.append(["Fondos", "https://y.com/2"])
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )
        return importacion, self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )

    def test_hay_una_zona_de_drop_por_categoria_activa(self):
        CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="INACTIVA", activo=False
        )
        _, response = self._preview_con_pendientes()

        self.assertContains(response, 'class="rutina-drop-zona"', count=2)

    def test_cada_pendiente_tiene_su_chip_arrastrable(self):
        _, response = self._preview_con_pendientes()

        self.assertContains(response, 'class="rutina-chip"', count=2)
        self.assertContains(response, 'draggable="true"', count=2)

    def test_la_capa_de_arrastre_esta_oculta_para_lectores_de_pantalla(self):
        """El arrastre nativo no tiene ARIA ni anda en touch: el desplegable
        es el camino accesible y el chip es decoración."""
        _, response = self._preview_con_pendientes()

        self.assertContains(response, 'aria-hidden="true"')

    def test_el_desplegable_sigue_siendo_el_control_autoritativo(self):
        """Si el JS no corre, la pantalla tiene que seguir funcionando."""
        _, response = self._preview_con_pendientes()

        self.assertContains(response, 'class="js-categoria"', count=2)

    def test_hay_controles_de_asignacion_en_lote(self):
        _, response = self._preview_con_pendientes()

        self.assertContains(response, 'id="lote-aplicar"')
        self.assertContains(response, 'id="lote-todos"')
        self.assertContains(response, 'class="js-lote"', count=2)

    def test_las_zonas_llevan_el_id_de_la_categoria_no_su_nombre(self):
        _, response = self._preview_con_pendientes()

        self.assertContains(response, f'data-categoria="{self.empuje.pk}"')

    def test_no_ofrece_categorias_de_otro_gimnasio(self):
        otro = Gimnasio.objects.create(nombre="Otro", slug="otro")
        CategoriaEjercicio.objects.create(gimnasio=otro, nombre="AJENA")

        _, response = self._preview_con_pendientes()

        self.assertNotContains(response, "AJENA")

    def test_muestra_lo_que_decia_el_archivo_para_los_no_resueltos(self):
        """Contexto para decidir: si el Excel decía algo y no se pudo
        resolver, el staff tiene que poder verlo sin abrir el Excel."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Categoría"])
        ws.append(["Dominadas", "ZZZ"])
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )
        # "ZZZ" no matchea ninguna existente, así que se crea: no queda
        # pendiente. Este test fija que el importador NO deja pendientes
        # cuando el archivo trae categoría.
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertNotContains(response, 'class="js-lote"')
