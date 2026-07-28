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
from importaciones.matching import (
    construir_indice_ejercicios,
    resolver_grupo_muscular,
    resolver_nombre,
)
from importaciones.models import Importacion
from importaciones.parsing import (
    normalizar_texto,
    parsear_archivo_biblioteca,
    parsear_archivo_plantillas,
)
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
    catálogo permanente del gimnasio -- todo dentro de una transacción, con
    el chequeo de idempotencia (¿ya se confirmó?) ANTES de escribir nada.

    El re-fetch con `select_for_update()` ANTES de validar (en vez de
    validar sobre la instancia que ya trae el caller) es necesario porque
    dos confirmaciones concurrentes de la MISMA importación (p. ej. un
    doble click -- `hx-boost` no deduplica submits) verían ambas
    `EN_REVISION` si sólo mirásemos el objeto en memoria: mismo patrón que
    `crear_reserva` en `turnos/services.py`. En SQLite (backend de test)
    `select_for_update()` no toma un lock real -- Django lo ejecuta como un
    SELECT normal dentro de la transacción -- así que no hace falta simular
    concurrencia real acá; en Postgres (producción) sí aplica el lock (ver
    docstring de `turnos/tests.py` para el mismo caveat)."""
    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        if importacion.gimnasio_id != gimnasio.id:
            raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
        if importacion.estado != Importacion.Estado.EN_REVISION:
            raise ImportacionInvalida("Esta importación ya fue procesada.")

        resultado = importacion.resultado
        decisiones_hojas = decisiones["hojas"]
        if len(decisiones_hojas) != len(resultado["hojas"]):
            # P. ej. checkboxes sin marcar en el form de confirmación
            # (Tarea 9) simplemente no llegan en el POST -- sin este chequeo
            # las hojas sobrantes se saltearían en silencio y la importación
            # igual quedaría CONFIRMADA (ya no se podría reintentar).
            raise ImportacionInvalida("Datos de confirmación incompletos.")

        ejercicios_por_nombre = {}  # nombre_normalizado -> Ejercicio, resuelto una vez

        def _obtener_ejercicio(nombre_normalizado):
            if nombre_normalizado in ejercicios_por_nombre:
                return ejercicios_por_nombre[nombre_normalizado]
            try:
                decision = decisiones["ejercicios"][nombre_normalizado]
            except KeyError:
                raise ImportacionInvalida(
                    f"Falta la decisión para el ejercicio «{nombre_normalizado}»."
                )
            if decision["accion"] == "usar_existente":
                try:
                    ejercicio = Ejercicio.objects.get(
                        pk=decision["ejercicio_id"], gimnasio=gimnasio,
                    )
                except Ejercicio.DoesNotExist:
                    raise ImportacionInvalida(
                        "El ejercicio elegido para reusar no existe en este gimnasio."
                    )
            else:
                grupo_muscular = decision["grupo_muscular"]
                if grupo_muscular not in Ejercicio.GrupoMuscular.values:
                    # `.create()` no llama a `full_clean()` y Django no
                    # aplica `choices` a nivel de base de datos -- sin este
                    # chequeo un valor inválido quedaría persistido y ese
                    # ejercicio se volvería invisible para el filtro por
                    # grupo muscular de la app `ejercicios`.
                    raise ImportacionInvalida(
                        f"Grupo muscular inválido: «{grupo_muscular}»."
                    )
                try:
                    nombre_original = next(
                        item["ejercicio_original"]
                        for hoja in resultado["hojas"]
                        for item in hoja["items"]
                        if item["ejercicio_normalizado"] == nombre_normalizado
                    )
                except StopIteration:
                    raise ImportacionInvalida(
                        f"No se encontró el ejercicio «{nombre_normalizado}» en el archivo."
                    )
                ejercicio = Ejercicio.objects.create(
                    gimnasio=gimnasio,
                    nombre=nombre_original,
                    grupo_muscular=grupo_muscular,
                )
            ejercicios_por_nombre[nombre_normalizado] = ejercicio
            return ejercicio

        plantillas_creadas = []
        for hoja, decision_hoja in zip(resultado["hojas"], decisiones_hojas):
            if not decision_hoja["incluir"]:
                continue
            nivel = decision_hoja["nivel"]
            if nivel not in RutinaPlantilla.Nivel.values:
                raise ImportacionInvalida(f"Nivel inválido: «{nivel}».")
            plantilla = RutinaPlantilla.objects.create(
                gimnasio=gimnasio,
                nombre=hoja["nombre_hoja"],
                objetivo=decision_hoja["objetivo"],
                nivel=nivel,
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


def previsualizar_importacion_biblioteca(*, gimnasio, archivo, usuario):
    """Análogo a `previsualizar_importacion_plantillas` pero para el import
    de biblioteca: cada fila es un ejercicio suelto (nombre + grupo
    muscular opcional + video opcional), sin días/semanas/series. Igual que
    en plantillas, no crea nada todavía -- solo arma el preview."""
    try:
        items_parseados, filas_invalidas = parsear_archivo_biblioteca(archivo)
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx válido."
        )

    indice = construir_indice_ejercicios(gimnasio)
    items = []
    for item in items_parseados:
        nombre_normalizado = normalizar_texto(item["nombre_original"])
        match = resolver_nombre(nombre_normalizado, indice)
        match_json = (
            {"tipo": "exacto", "ejercicio_id": match.ejercicio.pk}
            if match.tipo == "exacto"
            else {"tipo": "ambiguo", "candidato_id": match.candidato.pk, "score": match.score}
            if match.tipo == "ambiguo"
            else {"tipo": "nuevo"}
        )
        grupo_resuelto = (
            resolver_grupo_muscular(item["grupo_muscular_original"])
            if item["grupo_muscular_original"]
            else None
        )
        items.append({
            **item,
            "nombre_normalizado": nombre_normalizado,
            "grupo_muscular_resuelto": grupo_resuelto,
            "match": match_json,
        })

    resultado_json = {
        "items": items,
        "filas_invalidas": [asdict(f) for f in filas_invalidas],
    }

    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.BIBLIOTECA,
        archivo=archivo,
        resultado=resultado_json,
        creado_por=usuario,
    )


def confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones):
    """Mismo patrón anti-TOCTOU que `confirmar_importacion_plantillas` (Task 7,
    fix post-review): el guard de tenant/estado corre DENTRO de la
    transacción, contra una fila re-leída con `select_for_update()` -- dos
    confirmaciones concurrentes de la misma Importacion no deben poder crear
    ejercicios duplicados. `grupo_muscular` se valida contra las choices
    reales antes de crear (Ejercicio.objects.create() no llama a
    full_clean(), así que un valor fuera de las 8 choices cerradas se
    guardaría en silencio sin esta validación)."""
    creados = []
    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        if importacion.gimnasio_id != gimnasio.id:
            raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
        if importacion.estado != Importacion.Estado.EN_REVISION:
            raise ImportacionInvalida("Esta importación ya fue procesada.")

        for item in importacion.resultado["items"]:
            try:
                decision = decisiones["items"][item["nombre_normalizado"]]
            except KeyError:
                raise ImportacionInvalida(
                    f"Falta la decisión para el ejercicio «{item['nombre_original']}»."
                )
            if not decision["incluir"] or item["match"]["tipo"] == "exacto":
                # "exacto" ya existe en la biblioteca: no se recrea.
                continue
            grupo_muscular = decision["grupo_muscular"]
            if grupo_muscular not in Ejercicio.GrupoMuscular.values:
                raise ImportacionInvalida(
                    f"Grupo muscular inválido para '{item['nombre_original']}'."
                )
            ejercicio = Ejercicio.objects.create(
                gimnasio=gimnasio,
                nombre=item["nombre_original"],
                grupo_muscular=grupo_muscular,
                url_video=item["url_video"],
            )
            creados.append(ejercicio)

        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.save(update_fields=["estado", "confirmado_en"])

    return creados
