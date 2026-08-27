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
    construir_indice_categorias,
    construir_indice_ejercicios,
    resolver_categorias,
    resolver_nombre,
)
from importaciones.models import Importacion
from importaciones.parsing import (
    ColumnaRequeridaFaltante,
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
                "motivo_exclusion": hoja.motivo_exclusion,
            }
            for hoja in hojas
        ],
        "ejercicios_distintos": ejercicios_distintos,
        # Campo a nivel archivo (no por hoja) -- agregamos las advertencias
        # de todas las hojas, mismo criterio que usa el import de
        # biblioteca (un solo archivo/hoja).
        "advertencias_columnas": [
            advertencia for hoja in hojas for advertencia in hoja.advertencias_columnas
        ],
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
                from ejercicios.models import CategoriaEjercicio

                # Se re-lee contra un queryset scopeado por gimnasio y no se
                # confía en el id que vino en `decisiones`: es la barrera que
                # impide que un POST manipulado enganche un ejercicio nuevo a
                # la categoría de otro tenant.
                categoria = (
                    CategoriaEjercicio.objects.for_gimnasio(gimnasio)
                    .filter(pk=decision.get("categoria_id"))
                    .first()
                )
                if categoria is None:
                    raise ImportacionInvalida(
                        "La categoría elegida no existe en este gimnasio."
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
                    categoria=categoria,
                )
            ejercicios_por_nombre[nombre_normalizado] = ejercicio
            return ejercicio

        plantillas_creadas = []
        for hoja, decision_hoja in zip(resultado["hojas"], decisiones_hojas):
            if not decision_hoja["incluir"]:
                continue
            if not hoja["items"]:
                # Defensa en profundidad: el default de `incluir` para una
                # hoja sin items ya es `False` en `PreviewPlantillasView`
                # (Tarea 9, fix post-review hallazgo 2), pero esto cubre un
                # POST armado a mano que fuerce `incluir=True` para una
                # hoja vacía -- nunca crear una `RutinaPlantilla` sin
                # ningún ejercicio.
                raise ImportacionInvalida(
                    f"La hoja «{hoja['nombre_hoja']}» no tiene ejercicios y no se puede incluir."
                )
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
                    kilos=item["kilos"],
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
        items_parseados, filas_invalidas, advertencias_columnas = parsear_archivo_biblioteca(archivo)
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx válido."
        )
    except ColumnaRequeridaFaltante as error:
        # Listar lo que SÍ se leyó es lo que hace accionable el mensaje: el
        # caso típico es una fila de título arriba de la tabla, y viendo el
        # eco de esa fila el staff entiende al toque que la app miró la fila
        # equivocada.
        leidos = ", ".join(f"«{e}»" for e in error.encabezados) or "ninguno"
        raise ImportacionInvalida(
            f"No encontré la columna «{error.campo}» en el archivo. "
            f"En la primera fila leí estos encabezados: {leidos}. "
            "La primera fila de la hoja tiene que ser la de los títulos de "
            "las columnas (por ejemplo NOMBRE, LINK, CATEGORÍA): si arriba "
            "hay un título o una fila en blanco, borrala y volvé a subirlo."
        )

    indice = construir_indice_ejercicios(gimnasio)
    # Las categorías se resuelven de una sola vez sobre el conjunto de textos
    # distintos del archivo, no fila por fila: el dedupe difuso necesita ver
    # todas juntas para poder fusionar "TRACCIÓN" con "TRACION" cuando las dos
    # aparecen en el mismo Excel.
    categorias_resueltas = resolver_categorias(
        [i["grupo_muscular_original"] for i in items_parseados],
        construir_indice_categorias(gimnasio),
    )
    filas_invalidas_json = [asdict(f) for f in filas_invalidas]
    items = []
    primera_fila_por_nombre = {}  # nombre_normalizado -> fila_excel de la 1ra aparición
    for item in items_parseados:
        nombre_normalizado = normalizar_texto(item["nombre_original"])
        if nombre_normalizado in primera_fila_por_nombre:
            # Dos filas del MISMO archivo que normalizan al mismo nombre
            # (p. ej. "Press de banca" y "PRESS DE BANCA") -- `Ejercicio`
            # no tiene `unique_together`, así que sin este chequeo
            # `confirmar_importacion_biblioteca` crearía un registro por
            # cada fila (fix post-review, hallazgo 5). Se descarta acá, en
            # el preview, para que `confirmar_importacion_biblioteca` ni
            # siquiera vea la fila duplicada.
            filas_invalidas_json.append({
                "fila_excel": item["fila_excel"],
                "motivo": (
                    "Ejercicio duplicado en el archivo (ya aparece en la fila "
                    f"{primera_fila_por_nombre[nombre_normalizado]})"
                ),
            })
            continue
        primera_fila_por_nombre[nombre_normalizado] = item["fila_excel"]

        match = resolver_nombre(nombre_normalizado, indice)
        match_json = (
            {"tipo": "exacto", "ejercicio_id": match.ejercicio.pk}
            if match.tipo == "exacto"
            else {
                "tipo": "ambiguo",
                "candidato_id": match.candidato.pk,
                "candidato_nombre": match.candidato.nombre,
                "score": match.score,
            }
            if match.tipo == "ambiguo"
            else {"tipo": "nuevo"}
        )
        resuelta = categorias_resueltas.get(item["grupo_muscular_original"])
        items.append({
            **item,
            "nombre_normalizado": nombre_normalizado,
            "categoria_resuelta": asdict(resuelta) if resuelta else None,
            "match": match_json,
        })

    resultado_json = {
        "items": items,
        "filas_invalidas": filas_invalidas_json,
        "advertencias_columnas": advertencias_columnas,
        # Para el resumen "se van a crear N categorías" del preview. Se
        # calcula acá y no en el template para no recorrer 748 items en
        # cada render.
        #
        # Se recorre `items` (los que van a llegar al confirm) y no
        # `categorias_resueltas` (todas las filas parseadas): las filas
        # descartadas por duplicadas y las de ejercicios que ya existen
        # nunca crean nada. Sin esto, volver a subir el mismo archivo
        # anunciaba "se van a crear 11 categorías" y no creaba ninguna.
        "categorias_a_crear": sorted(
            {
                i["categoria_resuelta"]["nombre"]
                for i in items
                if i["categoria_resuelta"]
                and i["categoria_resuelta"]["tipo"] == "nueva"
                and i["match"]["tipo"] != "exacto"
            }
        ),
    }

    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.BIBLIOTECA,
        archivo=archivo,
        resultado=resultado_json,
        creado_por=usuario,
    )


def _categoria_para(*, item, decision, gimnasio, cache):
    """Resuelve la `CategoriaEjercicio` de un ejercicio a crear.

    Prioridad: lo que el staff eligió a mano en el preview gana sobre lo que
    resolvió el importador -- si tocó el desplegable, fue porque el
    automático no le servía.

    `cache` evita repetir el `get_or_create` de la misma categoría nueva por
    cada uno de los cientos de ejercicios que la comparten.
    """
    from ejercicios.models import CategoriaEjercicio

    if decision.get("sin_categoria"):
        # El staff eligió explícitamente dejarlo sin clasificar; no se cae
        # al automático, que es justo lo que quiso descartar.
        return None

    elegida = decision.get("categoria_id")
    if elegida:
        categoria = (
            CategoriaEjercicio.objects.for_gimnasio(gimnasio)
            .filter(pk=elegida)
            .first()
        )
        if categoria is None:
            raise ImportacionInvalida(
                f"La categoría elegida para «{item['nombre_original']}» no "
                "existe en tu gimnasio."
            )
        return categoria

    resuelta = item.get("categoria_resuelta")
    if not resuelta:
        return None

    if resuelta["tipo"] == "existente":
        categoria = (
            CategoriaEjercicio.objects.for_gimnasio(gimnasio)
            .filter(pk=resuelta["categoria_id"])
            .first()
        )
        if categoria is None:
            # Alguien la borró entre el preview y la confirmación. Falla
            # ruidoso, igual que la rama de elección manual: crear cientos de
            # ejercicios sin clasificar en silencio es peor que pedir que se
            # vuelva a previsualizar.
            raise ImportacionInvalida(
                f"La categoría resuelta para «{item['nombre_original']}» ya "
                "no existe. Volvé a subir el archivo."
            )
        return categoria

    nombre = resuelta["nombre"]
    clave = normalizar_texto(nombre)
    if clave not in cache:
        cache[clave], _ = CategoriaEjercicio.objects.get_or_create(
            gimnasio=gimnasio,
            nombre_normalizado=clave,
            defaults={"nombre": nombre},
        )
    return cache[clave]


def confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones):
    """Mismo patrón anti-TOCTOU que `confirmar_importacion_plantillas` (Task 7,
    fix post-review): el guard de tenant/estado corre DENTRO de la
    transacción, contra una fila re-leída con `select_for_update()` -- dos
    confirmaciones concurrentes de la misma Importacion no deben poder crear
    ejercicios duplicados.

    Acá es donde las categorías marcadas "nueva" en el preview se crean de
    verdad: el preview no escribe en la base. Van por `get_or_create` sobre
    `nombre_normalizado`, así que si alguien creó esa misma categoría desde
    el panel entre el preview y esta confirmación, se reusa en vez de chocar
    contra la `UniqueConstraint`."""
    creados = []
    categorias_creadas = {}
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
            categoria = _categoria_para(
                item=item,
                decision=decision,
                gimnasio=gimnasio,
                cache=categorias_creadas,
            )
            ejercicio = Ejercicio.objects.create(
                gimnasio=gimnasio,
                nombre=item["nombre_original"],
                categoria=categoria,
                url_video=item["url_video"],
            )
            creados.append(ejercicio)

        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.save(update_fields=["estado", "confirmado_en"])

    return creados
