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
                  "claves": {"email", "telefono", "nombre"}}],
     "ya_existen": [{"fila_excel", "nombre", "apellido", "origen": "base" |
                     "archivo", "alumno_id": pk | None, "motivo"}],
     "filas_invalidas": [{"fila_excel", "motivo"}],
     "nacen_vencidas": 0, "total_filas_leidas": 120}
"""

import logging
from dataclasses import asdict

from django.core.exceptions import ValidationError
from django.utils import timezone

from alumnos.identidad import clave_telefono, normalizar_email
from alumnos.models import Alumno
from importaciones.models import Importacion
from importaciones.parsing import (
    ArchivoCSVIlegible,
    ColumnaRequeridaFaltante,
    DemasiadasFilas,
    parsear_archivo_alumnos,
)
from importaciones.parsing.comun import FILAS_BUSQUEDA_ENCABEZADO, normalizar_texto
from pagos.acceso import nace_bloqueada
from importaciones.services import ERRORES_ARCHIVO_INVALIDO, ImportacionInvalida
from tenants.demo import MARCA as MARCA_DEMO

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
            if nace_bloqueada(gimnasio, datos.get("fecha_inicio_ciclo"), hoy):
                nacen_vencidas += 1
            a_crear.append({
                "fila_excel": fila.fila_excel,
                "datos": _a_json(datos),
                "avisos": avisos,
                "claves": claves,
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
