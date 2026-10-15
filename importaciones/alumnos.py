"""Importación de ALUMNOS desde Excel/CSV: previsualización.

Mismo molde que la biblioteca (subir -> previsualizar -> confirmar). Esta
primera mitad lee el archivo, valida lo que el lector Django-free no puede ver
(email, largos contra `Alumno._meta`, duplicados contra la base) y deja una
`Importacion` EN_REVISION con todo lo necesario en `resultado`; **no crea
ningún alumno**. La confirmación (Task 6) vive en este mismo módulo.

**Regla de producto: no inventar ni perder en silencio.** Un dato opcional que
no entra queda vacío, la fila lleva un aviso y el texto original se agrega a
`observaciones`. Este módulo es el ÚNICO que escribe esa línea
(`Datos del archivo que no se pudieron cargar: A; B.`): junta lo que el lector
dejó en `FilaAlumno.no_cargados` con lo que detecta acá.

**Claves de duplicado.** Cada fila de `a_crear` guarda en `claves` el email
normalizado, la clave de teléfono y la clave de nombre (`claves_de(datos)`,
reutilizable): al confirmar se vuelven a calcular los duplicados contra la base
de ese momento sin reabrir el archivo.

**Forma final de `Importacion.resultado`** (consumida por la confirmación y por
las pantallas)::

    {"version": 1, "fila_encabezado": 3,
     "columnas_detectadas": {campo: encabezado}, "columnas_ignoradas": [...],
     "advertencias_columnas": [...],
     "avisos_lectura": [...],   # avisos de la lectura entera ("Dejé de leer...")
     "a_crear": [{"fila_excel", "datos": {...fechas ISO...}, "avisos": [...],
                  "claves": {"email", "telefono", "nombre"},
                  "trae_contacto": bool}],
     "ya_existen": [{"fila_excel", "nombre", "apellido", "origen": "base" |
                     "archivo", "alumno_id": pk | None, "motivo"}],
     "filas_invalidas": [{"fila_excel", "motivo"}],
     "nacen_vencidas": 0, "total_filas_leidas": 120}
"""

import logging
from dataclasses import asdict
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from alumnos.ciclo import DIAS_MAX_INICIO_FUTURO, DIAS_MAX_INICIO_PASADO
from alumnos.identidad import clave_telefono, normalizar_email
from alumnos.models import Alumno
from importaciones.models import Importacion
from importaciones.parsing import (
    ArchivoCSVIlegible,
    ColumnaRequeridaFaltante,
    DemasiadasFilas,
    parsear_archivo_alumnos,
)
from importaciones.parsing.alumnos import ALIAS_ALUMNOS, MARCA_FILA_EJEMPLO
from importaciones.parsing.comun import FILAS_BUSQUEDA_ENCABEZADO, normalizar_texto
from pagos.acceso import nace_bloqueada
from importaciones.services import ERRORES_ARCHIVO_INVALIDO, ImportacionInvalida
from tenants.demo import MARCA as MARCA_DEMO
from tenants.models import Gimnasio

logger = logging.getLogger(__name__)

VERSION_RESULTADO = 1

# Las claves de `datos` que se pasan a `Alumno(**datos)` al confirmar.
CAMPOS_IMPORTABLES = frozenset({
    "nombre", "apellido", "email", "telefono", "fecha_nacimiento", "sexo",
    "estado", "fecha_inicio_ciclo", "actividad_fisica_previa",
    "frecuencia_actividad_previa", "deportes_practica", "tiene_discapacidad",
    "discapacidad_detalle", "tiene_enfermedad_cronica",
    "enfermedad_cronica_detalle", "observaciones",
})

# Textos opcionales cuyo largo se valida contra `Alumno._meta`, con el nombre
# con el que se citan en observaciones. `email` y `telefono` tienen su propio
# aviso más abajo.
_ETIQUETAS_TEXTO = {
    "email": "Email",
    "telefono": "Teléfono",
    "deportes_practica": "Deportes que practica",
    "discapacidad_detalle": "Detalle de discapacidad",
    "enfermedad_cronica_detalle": "Detalle de enfermedad crónica",
}

_FECHAS = ("fecha_nacimiento", "fecha_inicio_ciclo")


def _max_length(campo):
    return Alumno._meta.get_field(campo).max_length


def claves_de(datos):
    """Las tres claves con las que se compara un alumno: `email` en minúsculas
    (o `""`), `telefono` por `clave_telefono` (o `None`) y `nombre` como los
    tokens de nombre y apellido normalizados y ordenados (no depende de dónde
    se cortó entre uno y otro). Sirve igual para una fila del archivo y para un
    alumno de la base."""
    tokens = normalizar_texto(f"{datos.get('nombre', '')} {datos.get('apellido', '')}").split()
    return {
        "email": (datos.get("email") or "").strip().lower(),
        "telefono": clave_telefono(datos.get("telefono") or ""),
        "nombre": " ".join(sorted(tokens)),
    }


def _visible(nombre, apellido):
    return f"{apellido}, {nombre}"


class _ClavesDeAlumnos:
    """Índice de identidades para detectar duplicados: primero todos los
    alumnos del gimnasio (activos e inactivos, UNA query), después las filas
    del archivo que se van aceptando, para que la primera aparición gane."""

    def __init__(self, gimnasio):
        self._por_email, self._por_telefono, self._por_nombre = {}, {}, {}
        filas = (
            Alumno.objects.for_gimnasio(gimnasio)
            .values_list("pk", "nombre", "apellido", "email", "telefono")
        )
        for pk, nombre, apellido, email, telefono in filas:
            self.agregar(
                claves_de({
                    "nombre": nombre, "apellido": apellido,
                    "email": email, "telefono": telefono,
                }),
                nombre=nombre, apellido=apellido, origen="base", alumno_id=pk,
            )

    def agregar(self, claves, *, nombre, apellido, origen, alumno_id=None, fila=None):
        identidad = {
            "claves": claves, "nombre": nombre, "apellido": apellido,
            "origen": origen, "alumno_id": alumno_id, "fila": fila,
        }
        if claves["email"]:
            self._por_email.setdefault(claves["email"], []).append(identidad)
        if claves["telefono"]:
            self._por_telefono.setdefault(claves["telefono"], []).append(identidad)
        self._por_nombre.setdefault(claves["nombre"], []).append(identidad)

    def evaluar(self, claves, *, trae_contacto):
        """`(duplicado, avisos)`. `duplicado` es la identidad repetida o `None`;
        `avisos` solo importa cuando la fila se crea.

        Duplicado: (comparte email o teléfono Y el nombre) o (la fila no trae
        ni email ni teléfono Y el nombre coincide). Contacto igual con otro
        nombre (padres e hijos comparten celular) y nombre igual con otro
        contacto se crean, con un aviso."""
        por_contacto = []
        for email_o_tel, indice, que in (
            (claves["email"], self._por_email, "email"),
            (claves["telefono"], self._por_telefono, "teléfono"),
        ):
            for identidad in indice.get(email_o_tel, []) if email_o_tel else []:
                por_contacto.append((identidad, que))
        por_nombre = self._por_nombre.get(claves["nombre"], [])

        # `trae_contacto` mira lo que había en el archivo, no las claves: un
        # email inválido y sin teléfono no tiene clave utilizable, pero la fila
        # no deja de ser «de alguien con contacto». Con solo el nombre igual no
        # alcanza para decir que es la misma persona: se crea con aviso.
        if trae_contacto:
            for identidad, _ in por_contacto:
                if identidad["claves"]["nombre"] == claves["nombre"]:
                    return identidad, []
        elif por_nombre:
            return por_nombre[0], []

        avisos, vistos = [], set()
        for identidad, que in por_contacto:
            if id(identidad) in vistos:
                continue
            vistos.add(id(identidad))
            avisos.append(
                f"Comparte {que} con {_visible(identidad['nombre'], identidad['apellido'])}"
            )
        contactos = {id(i) for i, _ in por_contacto}
        for identidad in por_nombre:
            if id(identidad) not in contactos:
                avisos.append(
                    "Hay un alumno con el mismo nombre "
                    f"({_visible(identidad['nombre'], identidad['apellido'])}): "
                    "revisá que no sea el mismo"
                )
                break
        return None, avisos


def _validar_valores(datos, avisos, no_cargados):
    """Email, teléfono y largos de opcionales: lo que no entra queda vacío, con
    aviso y con el texto original para observaciones."""
    email = datos["email"]
    if email:
        try:
            normalizado = normalizar_email(email)
        except ValidationError:
            normalizado = None
        if normalizado is None or len(normalizado) > _max_length("email"):
            datos["email"] = ""
            avisos.append("El email no es válido: lo dejé vacío")
            no_cargados.append(f"{_ETIQUETAS_TEXTO['email']} «{email}»")
        else:
            datos["email"] = normalizado

    for campo in ("telefono", "deportes_practica", "discapacidad_detalle",
                  "enfermedad_cronica_detalle"):
        valor = datos[campo]
        if len(valor) > _max_length(campo):
            datos[campo] = ""
            avisos.append(
                f"{_ETIQUETAS_TEXTO[campo]} supera los {_max_length(campo)} "
                "caracteres: lo dejé vacío"
            )
            no_cargados.append(f"{_ETIQUETAS_TEXTO[campo]} «{valor}»")


def _componer_observaciones(datos, avisos, no_cargados):
    observaciones = datos["observaciones"]
    if observaciones == MARCA_DEMO:
        # `sembrar_demo --borrar` borra por esta marca exacta: un alumno real
        # importado con ella se iría con la demo.
        observaciones = f"{MARCA_DEMO} (importado)"
        avisos.append("La observación «[demo]» es una marca interna: la cambié")
    if no_cargados:
        linea = "Datos del archivo que no se pudieron cargar: " + "; ".join(no_cargados) + "."
        observaciones = f"{observaciones}\n{linea}" if observaciones else linea
    datos["observaciones"] = observaciones


def _a_json(datos):
    return {
        campo: (valor.isoformat() if campo in _FECHAS and valor else valor)
        for campo, valor in datos.items()
    }


def _leer(archivo, hoy):
    try:
        hoja = parsear_archivo_alumnos(archivo, hoy=hoy)
    except ArchivoCSVIlegible:
        raise ImportacionInvalida(
            "No pude leer el CSV: no está en UTF-8 ni en Windows-1252. "
            "Guardalo como .xlsx o como CSV UTF-8 y volvé a subirlo."
        )
    except DemasiadasFilas as error:
        raise ImportacionInvalida(
            f"El archivo tiene {error.filas} filas con datos y el máximo por "
            f"archivo es {error.maximo}. Dividilo en partes y subí cada una por "
            "separado."
        )
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx o un .csv válido."
        )
    except ColumnaRequeridaFaltante as error:
        leidos = ", ".join(f"«{e}»" for e in error.encabezados) or "ninguno"
        raise ImportacionInvalida(
            "No encontré la columna del nombre del alumno. Miré las "
            f"{FILAS_BUSQUEDA_ENCABEZADO} primeras filas de la hoja y la que "
            f"más se parecía a los títulos fue la fila {error.fila}, donde "
            f"leí: {leidos}. La hoja tiene que tener una fila con los títulos "
            "(por ejemplo Nombre y Apellido, o Nombre y apellido en una sola "
            "columna)."
        )
    # El lector consumió el archivo: queda rebobinado para guardarlo entero.
    # Si no se puede rebobinar, guardar el archivo dejaría uno truncado sin
    # rastro: se corta acá.
    try:
        archivo.seek(0)
    except (AttributeError, ValueError, OSError):
        logger.exception("No se pudo rebobinar el archivo de alumnos")
        raise ImportacionInvalida(
            "No pude leer el archivo hasta el final. Volvé a subirlo."
        )
    return hoja


def previsualizar_importacion_alumnos(*, gimnasio, archivo, usuario):
    """Lee el archivo y deja una `Importacion` ALUMNOS EN_REVISION. No crea
    alumnos."""
    # Antes de leer nada: la cuenta de prueba es compartida y un padrón real
    # (con datos de salud) quedaría a la vista de otros prospectos.
    if gimnasio.es_demo:
        raise ImportacionInvalida(
            "En la cuenta de prueba no se importan alumnos: es una cuenta "
            "compartida y los datos que subas los vería cualquiera que la use."
        )

    hoy = timezone.localdate()
    hoja = _leer(archivo, hoy)

    indice = _ClavesDeAlumnos(gimnasio)
    a_crear, ya_existen = [], []
    filas_invalidas = [asdict(f) for f in hoja.filas_invalidas]
    nacen_vencidas = 0

    for fila in hoja.filas:
        datos = dict(fila.datos)
        avisos = list(fila.avisos)
        no_cargados = list(fila.no_cargados)

        # Un nombre que no entra no se puede «dejar vacío»: sin él no hay alumno.
        for campo, etiqueta in (("nombre", "nombre"), ("apellido", "apellido")):
            if len(datos[campo]) > _max_length(campo):
                filas_invalidas.append({
                    "fila_excel": fila.fila_excel,
                    "motivo": f"El {etiqueta} supera los {_max_length(campo)} caracteres",
                })
                break
        else:
            trae_contacto = bool(datos["email"] or datos["telefono"])
            _validar_valores(datos, avisos, no_cargados)
            claves = claves_de(datos)
            repetido, avisos_duplicado = indice.evaluar(claves, trae_contacto=trae_contacto)
            if repetido is not None:
                if repetido["origen"] == "base":
                    motivo = (
                        "Ya existe en el gimnasio: "
                        f"{_visible(repetido['nombre'], repetido['apellido'])}"
                    )
                else:
                    motivo = f"Ya aparece en la fila {repetido['fila']} del archivo"
                ya_existen.append({
                    "fila_excel": fila.fila_excel,
                    "nombre": datos["nombre"],
                    "apellido": datos["apellido"],
                    "origen": repetido["origen"],
                    "alumno_id": repetido["alumno_id"],
                    "motivo": motivo,
                })
                continue

            avisos.extend(avisos_duplicado)
            _componer_observaciones(datos, avisos, no_cargados)
            indice.agregar(
                claves, nombre=datos["nombre"], apellido=datos["apellido"],
                origen="archivo", fila=fila.fila_excel,
            )
            # La misma regla del bloqueo, no una copia (ver `pagos.acceso`).
            # Un inactivo no cuenta: el cron no le emite cuotas, no bloquea.
            if datos.get("estado") != Alumno.Estado.INACTIVO and nace_bloqueada(gimnasio, datos.get("fecha_inicio_ciclo"), hoy):
                nacen_vencidas += 1
            a_crear.append({
                "fila_excel": fila.fila_excel,
                "datos": _a_json(datos),
                "avisos": avisos,
                "claves": claves,
                # Lo que traía el archivo ANTES de validar: al confirmar los
                # datos ya están limpios y un email inválido ya no se ve.
                "trae_contacto": trae_contacto,
            })

    resultado = {
        "version": VERSION_RESULTADO,
        "fila_encabezado": hoja.fila_encabezado,
        "columnas_detectadas": hoja.columnas_detectadas,
        "columnas_ignoradas": hoja.columnas_ignoradas,
        "advertencias_columnas": hoja.advertencias_columnas,
        "avisos_lectura": hoja.avisos_lectura,
        "a_crear": a_crear,
        "ya_existen": ya_existen,
        "filas_invalidas": filas_invalidas,
        "nacen_vencidas": nacen_vencidas,
        "total_filas_leidas": hoja.total_filas_leidas,
    }
    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.ALUMNOS,
        archivo=archivo,
        resultado=resultado,
        creado_por=usuario,
    )


def _borrar_archivo_al_commit(archivo, pk):
    """Borra el archivo subido DESPUÉS del commit. Un fallo del storage se
    loguea y no da 500: a lo sumo queda un objeto huérfano."""
    def borrar():
        try:
            archivo.delete(save=False)
        except Exception:
            logger.warning(
                "No se pudo borrar el archivo de la importación %s", pk, exc_info=True
            )

    if archivo:
        transaction.on_commit(borrar)


def confirmar_importacion_alumnos(*, importacion, gimnasio):
    """Crea los alumnos de una `Importacion` ALUMNOS EN_REVISION.

    Devuelve el resumen ``{"creados", "ya_existian", "descartadas",
    "salteados_al_confirmar", "sin_fecha_inicio"}``. Las primeras cuatro claves
    son las que se persisten en `resultado["resumen"]`; `sin_fecha_inicio`
    (creados sin `fecha_inicio_ciclo`, que no reciben cuotas hasta que el staff
    la cargue) solo viaja en el valor devuelto, para el mensaje y el redirect
    de la vista. Levanta `ImportacionInvalida` si la importación es de otro
    gimnasio, no es de alumnos o ya no está EN_REVISION.

    Los duplicados se recalculan contra la base de ESTE momento (el preview
    puede tener horas): las filas que ahora repiten a alguien se saltean y se
    cuentan en `salteados_al_confirmar`. Se toma un lock sobre la fila del
    `Gimnasio` ANTES de recargar las claves, para que dos importaciones
    paralelas del mismo gimnasio no se vean como inexistentes entre sí.

    El archivo se borra en `on_commit` y el campo `archivo` se vacía en la
    misma transacción: así ni un fallo del storage deja un link roto en la
    pantalla (a lo sumo queda un objeto huérfano, que se loguea)."""
    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        if importacion.gimnasio_id != gimnasio.id:
            raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
        if importacion.tipo != Importacion.Tipo.ALUMNOS:
            raise ImportacionInvalida("Esta importación no es de alumnos.")
        if importacion.estado != Importacion.Estado.EN_REVISION:
            raise ImportacionInvalida("Esta importación ya fue procesada.")

        # Serializa importaciones paralelas del mismo gimnasio. Se relee la
        # fila: la tolerancia y el tipo de público son los de este instante.
        gimnasio = Gimnasio.objects.select_for_update().get(pk=gimnasio.pk)
        resultado = importacion.resultado
        indice = _ClavesDeAlumnos(gimnasio)
        sexo_implicito = None if gimnasio.tiene_publico_mixto else gimnasio.sexo_implicito()

        nuevos, salteados, sin_fecha_inicio = [], 0, 0
        for fila in resultado["a_crear"]:
            datos = {c: v for c, v in fila["datos"].items() if c in CAMPOS_IMPORTABLES}
            claves = claves_de(datos)
            # Misma regla que el preview: con contacto en el archivo el nombre
            # solo no alcanza para decir que es la misma persona.
            # `.get`: una importación EN_REVISION anterior no guardó la clave.
            trae_contacto = fila.get(
                "trae_contacto", bool(claves["email"] or claves["telefono"])
            )
            repetido, _ = indice.evaluar(claves, trae_contacto=trae_contacto)
            if repetido is not None:
                salteados += 1
                continue
            indice.agregar(
                claves, nombre=datos["nombre"], apellido=datos["apellido"],
                origen="archivo", fila=fila["fila_excel"],
            )
            for campo in _FECHAS:
                datos[campo] = date.fromisoformat(datos[campo]) if datos.get(campo) else None
            datos["estado"] = datos.get("estado") or Alumno.Estado.ACTIVO
            if sexo_implicito and not datos.get("sexo"):
                datos["sexo"] = sexo_implicito
            if datos["fecha_inicio_ciclo"] is None:
                sin_fecha_inicio += 1
            nuevos.append(Alumno(
                gimnasio=gimnasio, importado=True, fecha_baja=None, **datos
            ))

        # `bulk_create` no pasa por `save()` ni por `pre_save`: por eso el
        # estado y `fecha_baja` se fijan acá y el ancla queda como vino.
        Alumno.objects.bulk_create(nuevos, batch_size=500)

        resumen = {
            "creados": len(nuevos),
            "ya_existian": len(resultado.get("ya_existen", [])),
            "descartadas": len(resultado.get("filas_invalidas", [])),
            "salteados_al_confirmar": salteados,
        }
        archivo = importacion.archivo
        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.resultado = {"version": VERSION_RESULTADO, "resumen": resumen}
        importacion.archivo = ""
        importacion.save(
            update_fields=["estado", "confirmado_en", "resultado", "archivo", "modificado"]
        )
        _borrar_archivo_al_commit(archivo, importacion.pk)

    return {**resumen, "sin_fecha_inicio": sin_fecha_inicio}


def descartar_importacion_alumnos(*, importacion, gimnasio):
    """Descarta una importación ALUMNOS EN_REVISION: tras descartar no puede
    quedar guardado el padrón (trae datos de salud), así que `resultado` se
    achica igual que al confirmar -- con `creados=0` -- y el archivo se borra.
    Levanta `ImportacionInvalida` si ya no está EN_REVISION."""
    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        if importacion.gimnasio_id != gimnasio.id:
            raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
        if importacion.estado != Importacion.Estado.EN_REVISION:
            raise ImportacionInvalida("Esta importación ya fue procesada.")
        resultado = importacion.resultado
        archivo = importacion.archivo
        importacion.estado = Importacion.Estado.DESCARTADA
        importacion.resultado = {
            "version": VERSION_RESULTADO,
            "resumen": {
                "creados": 0,
                "ya_existian": len(resultado.get("ya_existen", [])),
                "descartadas": len(resultado.get("filas_invalidas", [])),
                "salteados_al_confirmar": 0,
            },
        }
        importacion.archivo = ""
        importacion.save(update_fields=["estado", "resultado", "archivo", "modificado"])
        _borrar_archivo_al_commit(archivo, importacion.pk)


ENCABEZADOS_EJEMPLO = [
    "Nombre", "Apellido", "Email", "Teléfono", "Fecha de nacimiento", "Sexo",
    "Estado", "Inicio del ciclo", "Actividad física previa",
    "Frecuencia de actividad", "Deportes que practica", "Tiene discapacidad",
    "Detalle discapacidad", "Tiene enfermedad crónica",
    "Detalle enfermedad crónica", "Observaciones",
]


def construir_ejemplo_alumnos():
    """Un `.xlsx` de ejemplo, listo para llenar, generado al vuelo (nunca un
    binario versionado: se desincronizaría del parser). Las dos filas llevan
    `MARCA_FILA_EJEMPLO`, así que si el staff las deja el importador las
    descarta. La fecha de «Inicio del ciclo» sale de hoy, para que el ejemplo
    no envejezca fuera del rango aceptado."""
    from openpyxl import Workbook

    hoy = timezone.localdate()
    inicio = (hoy - timedelta(days=3)).strftime("%d/%m/%Y")

    wb = Workbook()
    ws = wb.active
    ws.title = "Alumnos"
    ws.append(ENCABEZADOS_EJEMPLO)
    ws.append([
        "Lucía", "Fernández", "lucia@ejemplo.com", "11 5555-1234", "14/03/1992",
        "Femenino", "", "", "Sí", "Varias veces por semana", "Natación", "No",
        "", "No", "", MARCA_FILA_EJEMPLO,
    ])
    ws.append([
        "Martín", "Gómez", "", "11 4444-9876", "02/11/1985", "Masculino",
        "Activo", inicio, "Sí", "Ocasional", "Fútbol", "No", "", "Sí",
        "Asma", MARCA_FILA_EJEMPLO,
    ])

    ayuda = wb.create_sheet("Cómo llenarlo")
    for linea in [
        "Obligatorio: el nombre y el apellido del alumno, en dos columnas, o el nombre completo en una sola.",
        "Todo lo demás es opcional. Las filas de ejemplo de la hoja «Alumnos» se descartan solas: borralas o pisalas.",
        "Los títulos no tienen que estar en la primera fila: podés dejar un título arriba.",
        "",
        "Fechas: escribilas como dd/mm/aaaa (día primero), por ejemplo 14/03/1992.",
        f"Inicio del ciclo: es el día desde el que se le cobra. Se acepta de {DIAS_MAX_INICIO_PASADO} días atrás a {DIAS_MAX_INICIO_FUTURO} días adelante. "
        "Sin esa columna, o con la celda vacía, el alumno no paga hasta que le fijes la fecha desde el listado.",
        "Estado: Activo o Inactivo (también Alta/Baja). Vacío = activo.",
        "Sexo: Masculino, Femenino o Prefiere no decir.",
        "Frecuencia de actividad: Diaria, Varias veces por semana, Una vez por semana u Ocasional.",
        "Actividad física previa, Tiene discapacidad y Tiene enfermedad crónica: Sí o No.",
        "Un dato que no se entienda queda vacío y se copia a Observaciones para que no se pierda.",
        "",
        "Duplicados: un alumno se considera repetido si coincide su email o teléfono Y su nombre, "
        "o si no trae ni email ni teléfono y coincide el nombre. Los repetidos no se crean ni se modifican.",
        "Los alumnos importados entran sin acceso a la app: el acceso se les da después, uno por uno.",
        "Se acepta .xlsx y también .csv (separado por comas o punto y coma).",
        "",
        "También se entienden estos nombres de columna:",
    ]:
        ayuda.append([linea])
    for campo, alias in ALIAS_ALUMNOS.items():
        ayuda.append([campo, ", ".join(alias)])
    return wb
