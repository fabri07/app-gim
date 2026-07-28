"""Orquestación de las importaciones (Proyecto 2): arma el preview y, tras
la confirmación del staff, crea los registros reales. Siempre transaccional
-- mismo patrón que `RutinaAsignada.crear_desde_plantilla`
(`rutinas/models.py`) y `turnos/services.py`."""

import zipfile
from dataclasses import asdict

from openpyxl.utils.exceptions import InvalidFileException

from importaciones.matching import construir_indice_ejercicios, resolver_nombre
from importaciones.models import Importacion
from importaciones.parsing import normalizar_texto, parsear_archivo_plantillas

# Un .xlsx corrupto o que en realidad no es un .xlsx (otra extensión
# renombrada a mano) puede fallar de dos formas al abrirlo con openpyxl:
# InvalidFileException (formato no reconocido) o BadZipFile (un .xlsx es
# un zip; si el contenido no es un zip válido, falla ahí). Ambas se tratan
# igual: mensaje en español, no un 500.
ERRORES_ARCHIVO_INVALIDO = (InvalidFileException, KeyError, zipfile.BadZipFile)


class ImportacionInvalida(Exception):
    """Mensaje en español listo para messages.error() -- análoga a
    ErrorDeReserva en turnos/services.py."""


def previsualizar_importacion_plantillas(*, gimnasio, archivo, usuario):
    """Parsea `archivo`, resuelve cada nombre de ejercicio DISTINTO contra
    el catálogo del gimnasio una sola vez (no una vez por fila -- si un
    ejercicio aparece en 12 filas repartidas en 2 hojas, se matchea una
    sola vez) y persiste todo como preview en un `Importacion` nuevo, en
    estado EN_REVISION. No crea `RutinaPlantilla`/`Ejercicio` -- eso ocurre
    recién al confirmar (fuera del alcance de esta función)."""
    try:
        hojas = parsear_archivo_plantillas(archivo)
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx válido."
        )

    indice = construir_indice_ejercicios(gimnasio)

    nombres_distintos = {
        normalizar_texto(item.ejercicio_original)
        for hoja in hojas
        for item in hoja.items
    }

    ejercicios_distintos = {}
    for nombre_normalizado in nombres_distintos:
        resultado = resolver_nombre(nombre_normalizado, indice)
        if resultado.tipo == "exacto":
            ejercicios_distintos[nombre_normalizado] = {
                "tipo": "exacto",
                "ejercicio_id": resultado.ejercicio.pk,
                "nombre": resultado.ejercicio.nombre,
            }
        elif resultado.tipo == "ambiguo":
            ejercicios_distintos[nombre_normalizado] = {
                "tipo": "ambiguo",
                "candidato_id": resultado.candidato.pk,
                "candidato_nombre": resultado.candidato.nombre,
                "score": resultado.score,
            }
        else:
            ejercicios_distintos[nombre_normalizado] = {"tipo": "nuevo"}

    resultado_json = {
        "hojas": [
            {
                "nombre_hoja": hoja.nombre_hoja,
                "dias_por_semana": hoja.dias_por_semana,
                "items": [
                    {**asdict(item), "ejercicio_normalizado": normalizar_texto(item.ejercicio_original)}
                    for item in hoja.items
                ],
                "filas_invalidas": [asdict(f) for f in hoja.filas_invalidas],
            }
            for hoja in hojas
        ],
        "ejercicios_distintos": ejercicios_distintos,
        "advertencias_columnas": [],
    }

    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.PLANTILLAS,
        archivo=archivo,
        resultado=resultado_json,
        creado_por=usuario,
    )
