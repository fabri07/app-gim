"""Orquestación de las importaciones (Proyecto 2): arma el preview y, tras
la confirmación del staff, crea los registros reales. Siempre transaccional
-- mismo patrón que `RutinaAsignada.crear_desde_plantilla`
(`rutinas/models.py`) y `turnos/services.py`."""

import zipfile
from dataclasses import asdict

from django.db import transaction
from django.utils import timezone
from openpyxl.utils.exceptions import InvalidFileException

from ejercicios.models import Ejercicio
from importaciones.matching import construir_indice_ejercicios, resolver_nombre
from importaciones.models import Importacion
from importaciones.parsing import normalizar_texto, parsear_archivo_plantillas
from rutinas.models import RutinaPlantilla, RutinaPlantillaItem

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


def confirmar_importacion_plantillas(*, importacion, gimnasio, decisiones):
    """Crea las `RutinaPlantilla`/`RutinaPlantillaItem`/`Ejercicio` reales a
    partir del preview persistido en `importacion.resultado` y las
    decisiones del staff. Es la única función de esta app que escribe en el
    catálogo permanente del gimnasio -- todo dentro de una transacción,
    con el chequeo de idempotencia (¿ya se confirmó?) ANTES de escribir
    nada. Mismo patrón que `RutinaAsignada.crear_desde_plantilla`
    (`rutinas/models.py`) y la validación de estado antes de mutar de
    `turnos/services.py`."""
    if importacion.gimnasio_id != gimnasio.id:
        raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
    if importacion.estado != Importacion.Estado.EN_REVISION:
        raise ImportacionInvalida("Esta importación ya fue procesada.")

    resultado = importacion.resultado
    ejercicios_por_nombre = {}  # nombre_normalizado -> Ejercicio, resuelto una vez

    def _obtener_ejercicio(nombre_normalizado):
        if nombre_normalizado in ejercicios_por_nombre:
            return ejercicios_por_nombre[nombre_normalizado]
        decision = decisiones["ejercicios"][nombre_normalizado]
        if decision["accion"] == "usar_existente":
            ejercicio = Ejercicio.objects.get(
                pk=decision["ejercicio_id"], gimnasio=gimnasio,
            )
        else:
            nombre_original = next(
                item["ejercicio_original"]
                for hoja in resultado["hojas"]
                for item in hoja["items"]
                if item["ejercicio_normalizado"] == nombre_normalizado
            )
            ejercicio = Ejercicio.objects.create(
                gimnasio=gimnasio,
                nombre=nombre_original,
                grupo_muscular=decision["grupo_muscular"],
            )
        ejercicios_por_nombre[nombre_normalizado] = ejercicio
        return ejercicio

    plantillas_creadas = []
    with transaction.atomic():
        for hoja, decision_hoja in zip(resultado["hojas"], decisiones["hojas"]):
            if not decision_hoja["incluir"]:
                continue
            plantilla = RutinaPlantilla.objects.create(
                gimnasio=gimnasio,
                nombre=hoja["nombre_hoja"],
                objetivo=decision_hoja["objetivo"],
                nivel=decision_hoja["nivel"],
                dias_por_semana=hoja["dias_por_semana"],
            )
            RutinaPlantillaItem.objects.bulk_create([
                RutinaPlantillaItem(
                    rutina=plantilla,
                    ejercicio=_obtener_ejercicio(item["ejercicio_normalizado"]),
                    semana=item["semana"],
                    dia=item["dia"],
                    orden=item["orden"],
                    series=item["series"],
                    repeticiones=item["repeticiones"],
                    descanso=item["descanso"],
                    notas=item["notas"],
                )
                for item in hoja["items"]
            ])
            plantillas_creadas.append(plantilla)

        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.save(update_fields=["estado", "confirmado_en"])

    return plantillas_creadas
