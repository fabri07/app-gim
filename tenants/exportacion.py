"""Exportación de TODOS los datos de un gimnasio a un ZIP de CSV.

Existe para el día en que un gimnasio deja de pagar la app o migra a otro
programa: los datos son suyos. La vista (`tenants.views.ExportarDatosView`) y
el comando (`manage.py exportar_gimnasio`) son capas finas sobre
`exportar_gimnasio()`; mismo reparto que `rutinas/pdf.py` con su vista.

Tres decisiones que conviene no deshacer sin querer:

1. **Se lee paginando por pk, NO con `.iterator()`.** Producción corre contra
   PgBouncer con `disable_server_side_cursors=True` (ver `config/db.py`), y sin
   cursor de servidor el driver trae el resultado ENTERO a memoria antes de
   iterar. `RutinaAsignadaItem` crece sin techo y el contenedor tiene 512 MB.
   SQLite local no lo muestra.
2. **Dos carpetas, una sola lectura.** No hay un CSV que abra bien con doble
   click en un Excel argentino (`;` y coma decimal) Y se importe limpio en otro
   programa (`,` y punto decimal), así que van los dos. Salen de la misma
   pasada de queries: duplicar la lectura duplicaría el tiempo con el único
   worker de gunicorn tomado.
3. **`EXCLUIDOS` es explícito.** Todo `TenantOwnedModel` está en `HOJAS` o en
   `EXCLUIDOS` con su motivo, y hay un test que lo exige: un modelo nuevo no
   puede quedar afuera de la exportación por olvido. En particular
   `GoogleCalendarCredential` NUNCA entra: su `EncryptedTextField` descifra
   transparente, así que un `values()` ingenuo saca los tokens en claro.
"""

import csv
import io
import re
import shutil
import tempfile
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone

#: Filas por consulta. Acota la memoria (ver punto 1 del docstring); los tests
#: de escala lo parchean a un valor chico.
TAMANIO_TANDA = 5000

#: Techo de filas para la descarga WEB. Medido: ~160 µs por fila en una máquina
#: de desarrollo (17.000 filas en 2,8 s), y el CPU de Render es más lento. Por
#: encima de esto la generación se acerca a los 30 s de timeout de gunicorn,
#: que con un solo worker es la app entera colgada y después un 502. El comando
#: `exportar_gimnasio` no tiene este techo.
MAX_FILAS_WEB = 60_000

CARPETA_EXCEL = "para-excel"
CARPETA_IMPORTAR = "para-importar"


@dataclass(frozen=True)
class Hoja:
    nombre: str
    modelo: str
    #: Lookup que lleva del modelo al gimnasio. Los modelos "Item" no son
    #: `TenantOwnedModel`: se acotan por la cadena de FK de su padre.
    filtro: str
    descripcion: str
    #: Pares (encabezado, lookup de `values_list`).
    columnas: tuple


HOJAS = (
    Hoja(
        "gimnasio", "tenants.Gimnasio", "pk",
        "Datos generales del gimnasio.",
        (
            ("id", "id"), ("nombre", "nombre"), ("slug", "slug"),
            ("contacto", "contacto"), ("instagram", "link_instagram"),
            ("whatsapp", "link_whatsapp"), ("facebook", "link_facebook"),
            ("texto_bienvenida", "texto_bienvenida"),
            ("tipo_publico", "tipo_publico"),
            ("dias_tolerancia_pago", "dias_tolerancia_pago"),
            ("logo_archivo", "logo"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "alumnos", "alumnos.Alumno", "gimnasio",
        "Padrón completo, con la ficha de inscripción. `usuario` es el "
        "identificador con el que el alumno entra a la app (las contraseñas "
        "no se guardan legibles y no se exportan).",
        (
            ("id", "id"), ("apellido", "apellido"), ("nombre", "nombre"),
            ("email", "email"), ("telefono", "telefono"),
            ("fecha_nacimiento", "fecha_nacimiento"), ("sexo", "sexo"),
            ("estado", "estado"), ("fecha_inicio_ciclo", "fecha_inicio_ciclo"),
            ("fecha_baja", "fecha_baja"),
            ("actividad_fisica_previa", "actividad_fisica_previa"),
            ("frecuencia_actividad_previa", "frecuencia_actividad_previa"),
            ("deportes_practica", "deportes_practica"),
            ("tiene_discapacidad", "tiene_discapacidad"),
            ("discapacidad_detalle", "discapacidad_detalle"),
            ("tiene_enfermedad_cronica", "tiene_enfermedad_cronica"),
            ("enfermedad_cronica_detalle", "enfermedad_cronica_detalle"),
            ("observaciones", "observaciones"),
            ("usuario", "perfil__usuario__username"),
            ("primer_ingreso_a_la_app", "fecha_activacion"),
            ("creado", "creado"),
        ),
    ),
    Hoja(
        "cuotas", "pagos.Cuota", "gimnasio",
        "Historial de cuotas y pagos. `comprobante_archivo` es el nombre del "
        "archivo subido; los archivos en sí no van en esta exportación.",
        (
            ("id", "id"), ("alumno_id", "alumno_id"),
            ("alumno_apellido", "alumno__apellido"),
            ("alumno_nombre", "alumno__nombre"),
            ("periodo_inicio", "periodo_inicio"), ("periodo_fin", "periodo_fin"),
            ("monto", "monto"), ("estado", "estado"), ("fecha_pago", "fecha_pago"),
            ("medio_de_pago", "medio_pago_texto"),
            ("comprobante_archivo", "comprobante"),
            ("observaciones", "observaciones"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "medios_de_cobro", "pagos.MedioCobro", "gimnasio",
        "Alias/CBU que el gimnasio muestra a sus alumnos para pagar.",
        (
            ("id", "id"), ("alias", "alias"), ("titular", "titular"),
            ("entidad", "entidad"), ("activo", "activo"),
        ),
    ),
    Hoja(
        "categorias", "ejercicios.CategoriaEjercicio", "gimnasio",
        "Categorías con las que el gimnasio agrupa sus ejercicios.",
        (("id", "id"), ("nombre", "nombre"), ("orden", "orden"), ("activo", "activo")),
    ),
    Hoja(
        "ejercicios", "ejercicios.Ejercicio", "gimnasio",
        "Biblioteca de ejercicios, con su video.",
        (
            ("id", "id"), ("nombre", "nombre"), ("categoria_id", "categoria_id"),
            ("categoria", "categoria__nombre"), ("descripcion", "descripcion"),
            ("video", "url_video"), ("activo", "activo"),
        ),
    ),
    Hoja(
        "plantillas", "rutinas.RutinaPlantilla", "gimnasio",
        "Plantillas de rutina (los planes base que después se asignan).",
        (
            ("id", "id"), ("nombre", "nombre"), ("objetivo", "objetivo"),
            ("nivel", "nivel"), ("dias_por_semana", "dias_por_semana"),
            ("activa", "activa"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "plantillas_ejercicios", "rutinas.RutinaPlantillaItem", "rutina__gimnasio",
        "Ejercicios de cada plantilla: una fila por ejercicio y por semana. "
        "Se une con `plantillas` por `plantilla_id`.",
        (
            ("id", "id"), ("plantilla_id", "rutina_id"),
            ("plantilla", "rutina__nombre"), ("semana", "semana"), ("dia", "dia"),
            ("dia_nombre", "dia_nombre"), ("orden", "orden"), ("bloque", "bloque"),
            ("ejercicio_id", "ejercicio_id"), ("ejercicio", "ejercicio__nombre"),
            ("series", "series"), ("repeticiones", "repeticiones"),
            ("kilos", "kilos"), ("descanso", "descanso"), ("notas", "notas"),
        ),
    ),
    Hoja(
        "rutinas_asignadas", "rutinas.RutinaAsignada", "gimnasio",
        "Cada plan entregado a un alumno, con todo su historial.",
        (
            ("id", "id"), ("alumno_id", "alumno_id"),
            ("alumno_apellido", "alumno__apellido"),
            ("alumno_nombre", "alumno__nombre"), ("nombre", "nombre_snapshot"),
            ("objetivo", "objetivo_snapshot"), ("fecha_inicio", "fecha_inicio"),
            ("fecha_fin", "fecha_fin"), ("activa", "activa"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "rutinas_asignadas_ejercicios", "rutinas.RutinaAsignadaItem",
        "rutina_asignada__gimnasio",
        "Ejercicios de cada rutina asignada: una fila por ejercicio y por "
        "semana, con la calificación (RPE) que cargó el alumno. Se une con "
        "`rutinas_asignadas` por `rutina_asignada_id`.",
        (
            ("id", "id"), ("rutina_asignada_id", "rutina_asignada_id"),
            ("alumno_id", "rutina_asignada__alumno_id"), ("semana", "semana"),
            ("dia", "dia"), ("dia_nombre", "dia_nombre"), ("orden", "orden"),
            ("bloque", "bloque"), ("ejercicio", "ejercicio_nombre_snapshot"),
            ("categoria", "categoria_snapshot"),
            ("video", "ejercicio_video_snapshot"), ("series", "series"),
            ("repeticiones", "repeticiones"), ("kilos", "kilos"),
            ("descanso", "descanso"), ("notas", "notas"),
            ("calificacion_del_alumno", "rpe"),
        ),
    ),
    Hoja(
        "dias_entrenados", "rutinas.RutinaAsignadaDiaCompletado",
        "rutina_asignada__gimnasio",
        "Días que cada alumno marcó como entrenados.",
        (
            ("id", "id"), ("rutina_asignada_id", "rutina_asignada_id"),
            ("alumno_id", "rutina_asignada__alumno_id"), ("semana", "semana"),
            ("dia", "dia"), ("completado_en", "completado_en"),
        ),
    ),
    Hoja(
        "novedades", "novedades.Novedad", "gimnasio",
        "Novedades publicadas. Con `alumno_id` cargado es un aviso personal.",
        (
            ("id", "id"), ("titulo", "titulo"), ("mensaje", "mensaje"),
            ("fecha_publicacion", "fecha_publicacion"),
            ("visible_hasta", "visible_hasta"), ("activa", "activa"),
            ("alumno_id", "alumno_id"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "novedades_lecturas", "novedades.NovedadLeida", "novedad__gimnasio",
        "Qué alumno leyó qué novedad, y cuándo.",
        (
            ("id", "id"), ("novedad_id", "novedad_id"),
            ("novedad", "novedad__titulo"), ("alumno_id", "alumno_id"),
            ("leida_en", "creado"),
        ),
    ),
    Hoja(
        "turnos_configuracion", "turnos.ConfiguracionTurnos", "gimnasio",
        "Duración de los turnos y cupo por defecto.",
        (
            ("id", "id"), ("duracion_minutos", "duracion_minutos"),
            ("vacantes_por_defecto", "vacantes_default"),
        ),
    ),
    Hoja(
        "turnos_horarios", "turnos.HorarioAtencion", "gimnasio",
        "Franjas horarias de atención por día de la semana.",
        (
            ("id", "id"), ("dia_semana", "dia_semana"),
            ("hora_desde", "hora_desde"), ("hora_hasta", "hora_hasta"),
        ),
    ),
    Hoja(
        "turnos_cupos_excepcion", "turnos.CupoExcepcion", "gimnasio",
        "Cupos distintos del default para un día y horario puntual.",
        (
            ("id", "id"), ("dia_semana", "dia_semana"),
            ("hora_inicio", "hora_inicio"), ("vacantes", "vacantes"),
        ),
    ),
    Hoja(
        "reservas", "turnos.Reserva", "gimnasio",
        "Historial de turnos reservados.",
        (
            ("id", "id"), ("alumno_id", "alumno_id"),
            ("alumno_apellido", "alumno__apellido"),
            ("alumno_nombre", "alumno__nombre"), ("fecha", "fecha"),
            ("hora_inicio", "hora_inicio"), ("creado", "creado"),
        ),
    ),
)

#: Modelos que NO se exportan, con el motivo que se le cuenta al gimnasio en el
#: LEEME. Las claves son `_meta.label`.
EXCLUIDOS = {
    "notificaciones.SuscripcionPush": (
        "credenciales técnicas de las notificaciones de cada dispositivo"
    ),
    "notificaciones.RecordatorioEnviado": (
        "registro interno para no repetir notificaciones"
    ),
    "tenants.RegistroSuplantacion": "auditoría interna de la plataforma",
    "importaciones.Importacion": (
        "archivos de trabajo del importador de Excel; lo importado ya figura "
        "en ejercicios y plantillas"
    ),
    "calendario.GoogleCalendarCredential": (
        "credenciales de Google de cada alumno (nunca salen de la plataforma)"
    ),
    "calendario.ReservaCalendarEvent": (
        "estado técnico de la sincronización con Google Calendar"
    ),
}

# Una celda que Excel interpretaría como fórmula. `+`/`-` solo cuentan si los
# sigue algo que no sea número o espacio: si no, se corrompen los teléfonos
# ("+54 9 11..."), los negativos y un "- Tren superior".
_RE_FORMULA = re.compile(r"^(?:[=@\t\r]|[+-](?=[A-Za-z(=@]))")


def sanear_para_excel(texto):
    if _RE_FORMULA.match(texto):
        return "'" + texto
    return texto


def _campo_de(modelo, lookup):
    """El campo final de un lookup con `__`, para leerle las `choices`."""
    partes = lookup.split("__")
    for parte in partes[:-1]:
        modelo = modelo._meta.get_field(parte).related_model
    ultimo = partes[-1]
    if ultimo.endswith("_id"):
        return None
    return modelo._meta.get_field(ultimo)


def _choices_de(modelo, lookup):
    campo = _campo_de(modelo, lookup)
    return dict(campo.flatchoices) if campo is not None and campo.choices else None


def _formatear(valor, zona):
    """Devuelve el par `(para_importar, para_excel)` de una celda.

    Se formatea UNA vez por celda y no una por carpeta: las dos versiones solo
    difieren en booleanos, decimales y textos que Excel tomaría por fórmula.
    Medido con 17.000 filas, formatear dos veces era el 70% del tiempo de la
    exportación, con el único worker de gunicorn tomado mientras tanto. `zona`
    viene resuelta de afuera por lo mismo: `timezone.localtime()` la busca en
    un thread-local en cada llamada."""
    if valor is None:
        return "", ""
    tipo = type(valor)
    if tipo is str:
        if valor and valor[0] in "=@\t\r+-":
            return valor, sanear_para_excel(valor)
        return valor, valor
    if tipo is int:
        texto = str(valor)
        return texto, texto
    if tipo is bool:
        return ("true", "Sí") if valor else ("false", "No")
    if tipo is Decimal:
        texto = str(valor)
        return texto, texto.replace(".", ",")
    if tipo is datetime:
        if timezone.is_aware(valor):
            valor = valor.astimezone(zona)
        texto = valor.strftime("%Y-%m-%d %H:%M:%S")
    elif tipo is date:
        texto = valor.isoformat()
    elif tipo is time:
        texto = valor.strftime("%H:%M")
    else:
        texto = str(valor)
    return texto, texto


def _filas_de(hoja, gimnasio):
    """Genera las filas de una hoja, en tandas por pk (ver docstring, punto 1)."""
    modelo = apps.get_model(hoja.modelo)
    lookups = [lookup for _, lookup in hoja.columnas]
    choices = [_choices_de(modelo, lookup) for lookup in lookups]
    valor_filtro = gimnasio.pk if hoja.filtro == "pk" else gimnasio
    base = (
        modelo._default_manager.filter(**{hoja.filtro: valor_filtro})
        .order_by("pk")
        .values_list("pk", *lookups)
    )
    ultimo_pk = 0
    while True:
        tanda = list(base.filter(pk__gt=ultimo_pk)[:TAMANIO_TANDA])
        for pk, *valores in tanda:
            yield [
                opciones.get(valor, valor) if opciones else valor
                for valor, opciones in zip(valores, choices)
            ]
        if len(tanda) < TAMANIO_TANDA:
            return
        ultimo_pk = tanda[-1][0]


def _escribir_hoja(zf, hoja, gimnasio):
    """Escribe la hoja en las dos carpetas desde una sola lectura.

    `zipfile` admite un solo handle de escritura abierto a la vez: la versión
    para importar va directo al ZIP y la de Excel a un temporal que se vuelca
    al cerrar la hoja."""
    encabezados = [encabezado for encabezado, _ in hoja.columnas]
    cantidad = 0
    with tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024) as temporal:
        texto_excel = io.TextIOWrapper(temporal, encoding="utf-8-sig", newline="")
        escritor_excel = csv.writer(texto_excel, delimiter=";")
        escritor_excel.writerow(encabezados)
        with zf.open(f"{CARPETA_IMPORTAR}/{hoja.nombre}.csv", "w") as crudo:
            texto = io.TextIOWrapper(crudo, encoding="utf-8", newline="")
            escritor = csv.writer(texto)
            escritor.writerow(encabezados)
            zona = timezone.get_current_timezone()
            for fila in _filas_de(hoja, gimnasio):
                pares = [_formatear(valor, zona) for valor in fila]
                escritor.writerow([par[0] for par in pares])
                escritor_excel.writerow([par[1] for par in pares])
                cantidad += 1
            texto.flush()
            texto.detach()
        texto_excel.flush()
        texto_excel.detach()
        temporal.seek(0)
        with zf.open(f"{CARPETA_EXCEL}/{hoja.nombre}.csv", "w") as destino:
            shutil.copyfileobj(temporal, destino)
    return cantidad


def _leeme(gimnasio, conteos):
    lineas = [
        f"Datos de {gimnasio.nombre}",
        f"Generado el {timezone.localtime().strftime('%Y-%m-%d %H:%M')} "
        "(hora de Argentina).",
        "",
        "Este archivo contiene todos los datos que tu gimnasio cargó en la app.",
        "Son tuyos: podés guardarlos, abrirlos en Excel o llevarlos a otro programa.",
        "",
        "HAY DOS CARPETAS CON LOS MISMOS DATOS",
        "",
        f"  {CARPETA_EXCEL}/     Para abrir con doble click en Excel. Columnas",
        "                  separadas por punto y coma, decimales con coma.",
        f"  {CARPETA_IMPORTAR}/  Para cargar en otro programa. Formato CSV",
        "                  estándar: separado por comas, decimales con punto,",
        "                  codificación UTF-8.",
        "",
        "Las fechas van como AAAA-MM-DD. Los archivos se relacionan entre sí por",
        "las columnas terminadas en `_id` (por ejemplo, `alumno_id` de cuotas.csv",
        "es el `id` de alumnos.csv).",
        "",
        "ARCHIVOS",
        "",
    ]
    for hoja in HOJAS:
        cantidad = conteos[hoja.nombre]
        lineas.append(
            f"  {hoja.nombre}.csv  ({cantidad} {'fila' if cantidad == 1 else 'filas'})"
        )
        lineas += textwrap.wrap(
            hoja.descripcion, width=74,
            initial_indent="      ", subsequent_indent="      ",
        )
    lineas += [
        "",
        "QUÉ NO SE INCLUYE",
        "",
        "  - Las contraseñas: no se guardan de forma legible en ningún lado.",
        "  - Los archivos subidos (comprobantes de pago, logo): figura su nombre.",
    ]
    lineas += [f"  - {motivo[0].upper()}{motivo[1:]}." for motivo in EXCLUIDOS.values()]
    return "\r\n".join(lineas) + "\r\n"


#: Las tres tablas que crecen con el tiempo; el resto es catálogo.
_HOJAS_VOLUMINOSAS = ("rutinas_asignadas_ejercicios", "cuotas", "reservas")


def filas_estimadas(gimnasio):
    """Cuántas filas va a tener la exportación, a costo de 3 `COUNT`. La vista
    lo compara contra `MAX_FILAS_WEB` ANTES de generar nada."""
    total = 0
    for hoja in HOJAS:
        if hoja.nombre in _HOJAS_VOLUMINOSAS:
            modelo = apps.get_model(hoja.modelo)
            total += modelo._default_manager.filter(**{hoja.filtro: gimnasio}).count()
    return total


def exportar_gimnasio(*, gimnasio, destino):
    """Escribe en `destino` (file-like binario) el ZIP con todos los datos de
    `gimnasio`. Devuelve `{nombre_de_hoja: cantidad_de_filas}`.

    No chequea `gimnasio.puede_exportar`: eso es de la vista. El comando de
    Shell exporta siempre (quien tiene Shell ya tiene la base)."""
    # Snapshot consistente: son ~17 consultas, y sin esto el cron o un alumno
    # pueden escribir entre dos hojas y dejar ejercicios que referencian una
    # rutina que no está en el otro archivo. `SET TRANSACTION` solo es válido
    # como primera sentencia, así que no se intenta si ya hay una transacción
    # abierta (los tests, por ejemplo). Con PgBouncer en modo transacción es
    # seguro: la conexión se mantiene mientras dure la transacción.
    fijar_snapshot = connection.vendor == "postgresql" and not connection.in_atomic_block
    conteos = {}
    with transaction.atomic():
        if fijar_snapshot:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
            for hoja in HOJAS:
                conteos[hoja.nombre] = _escribir_hoja(zf, hoja, gimnasio)
            # Con BOM: sin él, el Bloc de notas de un Windows viejo abre los
            # acentos rotos.
            zf.writestr("LEEME.txt", _leeme(gimnasio, conteos).encode("utf-8-sig"))
    return conteos
