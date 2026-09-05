"""
Modelo de dominio: la cuota de cada alumno.

`Cuota` es un `TenantOwnedModel`: hereda `gimnasio` (aislamiento por
fila) y los timestamps de auditoría. Se FK-ea a `alumnos.Alumno` por string
("alumnos.Alumno") en vez de importar la clase, para no acoplar el orden de
carga de apps entre `pagos` y `alumnos` (Django resuelve el string una vez
que ambas apps están instaladas).

Principio no negociable del ROADMAP (Fase 1 / Fase 2 §6): pagos simples, sin
integración financiera real. Los pendientes se **autogeneran por cron** al
inicio de cada mes (una fila por alumno activo por mes calendario) y el mismo
cron pasa `pendiente -> vencido` cuando el mes ya pasó. El dueño únicamente
**confirma** un pago existente (marca pagado, sube comprobante); nunca crea
un `Cuota` a mano. Esa autogeneración vive acá (funciones de módulo,
no una capa de "servicios" separada: el proyecto es chico y no lo justifica).
"""

from datetime import timedelta

from django.db import models
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)

from core.models import TenantOwnedModel, validar_gimnasio_de

EXTENSIONES_COMPROBANTE_PERMITIDAS = ["jpg", "jpeg", "png"]

#: Largo del ciclo de cobro, en días. Son las mismas 4 semanas que
#: `rutinas.models.SEMANAS_POR_CICLO`, pero NO se importa de ahí: `pagos` no
#: debe depender de `rutinas` (ver el orden de `INSTALLED_APPS`). Si un día se
#: cambia uno, hay que cambiar el otro a mano y a sabiendas.
#:
#: Ojo con la consecuencia comercial, que es una decisión tomada y no un
#: efecto colateral: 365/28 = 13,04 cobros al año contra 12 meses. Con el
#: mismo monto por cuota eso es ~8,6% más de facturación anual por alumno.
DIAS_CICLO = 28


def ciclo_vigente(fecha_inicio_ciclo, hoy):
    """`(periodo_inicio, periodo_fin)` del ciclo que corre HOY, o `None`.

    Devuelve `None` si el alumno no tiene ancla o si el ancla todavía no
    llegó. Ese segundo caso importa: sin el guard, un ancla futura da un
    índice NEGATIVO (`(-6)//28 == -1` en Python) y se emitiría una cuota por
    un período que arrancó semanas antes del alta -- vencida y bloqueante
    desde el minuto cero. El `max(0, ...)` es defensa en profundidad para el
    día que alguien saque el guard sin darse cuenta.

    Función pura (sin ORM, sin `timezone`): `hoy` lo pasa quien llama, así se
    testea con fechas fijas.
    """
    if fecha_inicio_ciclo is None or hoy < fecha_inicio_ciclo:
        return None
    indice = max(0, (hoy - fecha_inicio_ciclo).days // DIAS_CICLO)
    inicio = fecha_inicio_ciclo + timedelta(days=DIAS_CICLO * indice)
    return inicio, inicio + timedelta(days=DIAS_CICLO - 1)


class MedioCobro(TenantOwnedModel):
    """Alias/CBU al que los alumnos transfieren la cuota. Solo datos exhibidos en el
    portal -- sin integración de pagos (principio no negociable del proyecto: "sin
    Mercado Pago ni integraciones financieras en el MVP")."""

    alias = models.CharField(max_length=60)
    titular = models.CharField(max_length=80, blank=True)
    entidad = models.CharField(max_length=60, blank=True)  # banco o billetera virtual
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "medio de cobro"
        verbose_name_plural = "medios de cobro"
        ordering = ["alias"]

    def __str__(self):
        return self.alias


class Cuota(TenantOwnedModel):
    """La cuota de un alumno para un período puntual.

    Se llamaba `PagoMensual` hasta la migración a ciclos: el nombre mentía
    porque el período dejó de ser el mes calendario. `related_name` sigue
    siendo `pagos` a propósito -- cambiarlo obliga a tocar cuatro archivos y
    sus tests sin ganar nada, y "los pagos del alumno" se lee bien.

    `unique_together` en Meta garantiza una sola fila por
    (gimnasio, alumno, mes, año): coincide con "se autogeneran... para cada
    alumno activo al inicio del mes" (un pago por mes, no varios).

    `comprobante`: es `FileField` (no `ImageField`, que exige poder abrir el
    archivo con Pillow al validar -- acá alcanza con el validador de
    extensión) restringido a `EXTENSIONES_COMPROBANTE_PERMITIDAS`
    (jpg/jpeg/png): son fotos de un comprobante de transferencia sacadas con
    el celular, nunca un PDF -- pedido explícito del dueño del producto para
    que el staff no reciba archivos que no pueda previsualizar de un
    vistazo. En dev queda en el filesystem local (`MEDIA_ROOT`); en
    producción vive en Cloudflare R2 vía `django-storages` sin tocar este
    campo.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PAGADO = "pagado", "Pagado"
        VENCIDO = "vencido", "Vencido"
        #: Condonada por el staff. NO bloquea al alumno y NO suma a
        #: `ingresos_por_mes`. Existe porque, sin esto, la única forma de
        #: sacar una cuota del conjunto que bloquea sería marcarla PAGADO --
        #: o sea, falsear la facturación del gimnasio para destrabar a
        #: alguien (una licencia, un becado, una cuota cargada por error).
        ANULADO = "anulado", "Anulada"

    #: Estados que cuentan como deuda: los que bloquean y los que el alumno
    #: puede saldar subiendo un comprobante. Se nombra una sola vez para que
    #: `acceso.py`, las vistas y los tests no puedan divergir.
    ESTADOS_IMPAGOS = (Estado.PENDIENTE, Estado.VENCIDO)

    def tolerancia_efectiva(self):
        """Ver `tolerancia_efectiva()` a nivel de módulo."""
        gimnasio = self.gimnasio
        return tolerancia_efectiva(
            self.periodo_inicio,
            gimnasio.dias_tolerancia_pago,
            gimnasio.fecha_activacion_bloqueo,
        )

    def fecha_limite_pago(self):
        """Ver `limite_de_pago()` a nivel de módulo."""
        gimnasio = self.gimnasio
        return limite_de_pago(
            self.periodo_inicio,
            self.periodo_fin,
            gimnasio.dias_tolerancia_pago,
            gimnasio.fecha_activacion_bloqueo,
        )


    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    periodo_inicio = models.DateField("Inicio del período")
    #: INCLUSIVO: el último día que la cuota cubre, no el primero que no
    #: cubre. Se persiste en vez de derivarse porque `DIAS_CICLO` puede
    #: cambiar y una cuota ya emitida no debe moverse retroactivamente
    #: (distinto criterio que `RutinaAsignada.fecha_fin_prevista`, que sí se
    #: deriva porque describe estado vivo, no un registro contable cerrado).
    periodo_fin = models.DateField("Fin del período")
    #: MUERTAS. Quedan `null=True` solo para que revertir el código alcance
    #: como vuelta atrás: el `RemoveField` sería irreversible (no tienen
    #: `default`, así que el `database_backwards` emite `ADD COLUMN NOT NULL`
    #: sin default y falla), y dos columnas muertas cuestan infinitamente
    #: menos que un restore. Las sigue llenando `save()`, derivadas de
    #: `periodo_inicio`. No leer de acá.
    mes = models.PositiveSmallIntegerField(
        null=True, blank=True, editable=False,
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    anio = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.PENDIENTE
    )
    fecha_pago = models.DateField(null=True, blank=True)
    medio_pago_texto = models.CharField(max_length=60, blank=True)
    comprobante = models.FileField(
        upload_to="comprobantes/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=EXTENSIONES_COMPROBANTE_PERMITIDAS)],
    )
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "cuota"
        verbose_name_plural = "cuotas"
        unique_together = ("gimnasio", "alumno", "periodo_inicio")
        ordering = ["-periodo_inicio"]

    def __str__(self):
        return f"{self.alumno} - {self.periodo_inicio:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        """Mantiene llenas las columnas muertas `mes`/`anio`.

        No es cosmético: son la red de la vuelta atrás. Si se revierte el
        código, el código viejo filtra por `mes`/`anio` y hace
        `f"{self.mes:02d}"`, que con `None` revienta. `bulk_create` NO pasa
        por acá, así que los caminos en lote las setean explícitamente (ver
        `generar_pagos_pendientes`).
        """
        if self.periodo_inicio is not None:
            self.mes = self.periodo_inicio.month
            self.anio = self.periodo_inicio.year
            if (campos := kwargs.get("update_fields")) is not None:
                kwargs["update_fields"] = {*campos, "mes", "anio"}
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.gimnasio_id and self.alumno_id:
            validar_gimnasio_de(self.gimnasio, alumno=self.alumno)

    @property
    def esta_impaga(self) -> bool:
        return self.estado in self.ESTADOS_IMPAGOS


#: Cuántos días antes de que arranque el ciclo siguiente se emite su cuota.
#: Sin esto no existe la fila donde registrar un pago adelantado ni a la que
#: subirle el comprobante: el alumno que quiere pagar antes de que empiece el
#: bloque no tiene dónde hacerlo y termina bloqueado por puntual.
DIAS_PREEMISION = 7


def generar_pagos_pendientes(hoy):
    """Emite las cuotas que falten: la del ciclo vigente de cada alumno activo
    y la del siguiente si arranca dentro de `DIAS_PREEMISION` días.

    **No emite nada si el ancla del alumno todavía no llegó** -- ni si no
    tiene ancla. Un ciclo que no arrancó no se factura: sin ese guard, un
    alumno cuyo ciclo empieza el lunes que viene recibiría hoy una cuota por
    un período que arrancó semanas atrás (ver `ciclo_vigente`).

    **El costo en queries es constante**, no crece con la cantidad de alumnos:
    una consulta de alumnos, una de cuotas ya existentes y un `bulk_create`.
    Es la regla que este proyecto ya pagó cara con el importador (502 en
    producción por N queries por fila, ver ISSUES.md 2026-08-27).

    `monto` se deja en 0: el dueño lo completa al confirmar el pago -- el cron
    no conoce precios. `mes`/`anio` se llenan a mano acá porque `bulk_create`
    no pasa por `save()`, y son la red de la vuelta atrás (ver el modelo).

    Devuelve cuántas cuotas faltaban y quedaron emitidas. Con `ignore_conflicts`
    ese número puede sobrar si otra corrida concurrente creó la misma fila en
    el medio; el `ignore_conflicts` está justamente para que ese caso no
    aborte el lote entero.
    """
    from alumnos.models import Alumno

    alumnos_activos = Alumno.objects.filter(
        estado=Alumno.Estado.ACTIVO,
        gimnasio__activo=True,
        fecha_inicio_ciclo__isnull=False,
    ).only("id", "gimnasio_id", "fecha_inicio_ciclo")

    previstas = {}
    for alumno in alumnos_activos:
        ciclo = ciclo_vigente(alumno.fecha_inicio_ciclo, hoy)
        if ciclo is None:
            continue
        periodos = [ciclo]
        # El ciclo siguiente, si ya está a la vista.
        siguiente_inicio = ciclo[1] + timedelta(days=1)
        if (siguiente_inicio - hoy).days <= DIAS_PREEMISION:
            periodos.append(
                (siguiente_inicio, siguiente_inicio + timedelta(days=DIAS_CICLO - 1))
            )
        for inicio, fin in periodos:
            previstas[(alumno.id, inicio)] = (alumno.gimnasio_id, fin)

    if not previstas:
        return 0

    ya_existen = set(
        Cuota.objects.filter(
            alumno_id__in={alumno_id for alumno_id, _ in previstas},
            periodo_inicio__in={inicio for _, inicio in previstas},
        ).values_list("alumno_id", "periodo_inicio")
    )

    a_crear = [
        Cuota(
            gimnasio_id=gimnasio_id,
            alumno_id=alumno_id,
            periodo_inicio=inicio,
            periodo_fin=fin,
            mes=inicio.month,
            anio=inicio.year,
            monto=0,
            estado=Cuota.Estado.PENDIENTE,
        )
        for (alumno_id, inicio), (gimnasio_id, fin) in previstas.items()
        if (alumno_id, inicio) not in ya_existen
    ]
    if a_crear:
        Cuota.objects.bulk_create(a_crear, ignore_conflicts=True)
    return len(a_crear)


def marcar_vencidos(hoy):
    """Pasa a VENCIDO las cuotas PENDIENTE cuyo plazo de pago ya pasó.

    Dos regímenes, según el gimnasio tenga o no configurada la tolerancia:

    - **Con tolerancia N**: vence a los N días de arrancar el ciclo. Es el
      mismo umbral con el que se bloquea al alumno (`periodo_inicio <= hoy -
      N`, ver `pagos/acceso.py::_umbral`), para que no haya un día en que
      esté bloqueado y la app le diga "Pendiente". Un `<` en vez de `<=` acá
      producía exactamente ese día. Y las cuotas anteriores a
      `fecha_activacion_bloqueo` siguen el régimen sin tolerancia, porque a
      esas el bloqueo no las alcanza (ver `tolerancia_efectiva`).

    - **Sin tolerancia** (el estado de todos los gimnasios hasta que alguien
      la prenda): vence recién cuando el ciclo TERMINÓ. Es la traducción
      honesta del comportamiento viejo ("vence cuando el mes cerró") y evita
      el incidente obvio: con la regla de la tolerancia aplicada a `None` como
      0 días, el día del deploy pasaba a VENCIDO todo el padrón y salía un
      push masivo de «tu cuota está vencida» a alumnos que estaban al día.

    `modificado` se escribe EXPLÍCITAMENTE porque `QuerySet.update()` no
    dispara `auto_now`. No es cosmético: `enviar_recordatorios` filtra por
    `modificado__date=hoy` para saber a quién avisarle, así que sin esto el
    push de «cuota vencida» no salía nunca (bug preexistente que este cambio
    destapa; ver el paso de despliegue sobre la primera corrida).

    Se agrupa por VALOR de tolerancia, no por gimnasio: son un puñado de
    valores distintos, así que el costo no crece con la cantidad de tenants.
    Y el umbral se calcula en Python, nunca como `periodo_inicio + columna` en
    el queryset: esa aritmética anda en Postgres y da resultados
    silenciosamente distintos en SQLite, donde corre toda la suite.
    """
    from django.utils import timezone

    ahora = timezone.now()

    total = 0
    for (tolerancia, activacion), gimnasio_ids in regimenes_de_pago().items():
        # "Ya venció" es `fecha_limite_pago < hoy`, o sea `<= hoy - 1`. La
        # misma regla que `limite_de_pago`, escrita en SQL -- ver el docstring
        # de `filtro_por_limite_de_pago` y el test que compara las dos.
        vencibles = Cuota.objects.filter(
            estado=Cuota.Estado.PENDIENTE, gimnasio_id__in=gimnasio_ids
        ).filter(
            filtro_por_limite_de_pago(
                tolerancia, activacion, hasta=hoy - timedelta(days=1)
            )
        )
        total += vencibles.update(estado=Cuota.Estado.VENCIDO, modificado=ahora)
    return total


def tolerancia_efectiva(periodo_inicio, tolerancia, activacion):
    """La tolerancia que de verdad rige para una cuota, o `None` si esa cuota
    no está sujeta al bloqueo.

    No alcanza con mirar `Gimnasio.dias_tolerancia_pago`: `pagos/acceso.py`
    ignora las cuotas anteriores a `fecha_activacion_bloqueo` (prender el
    bloqueo no es retroactivo), así que para ESAS cuotas el régimen sigue
    siendo el de siempre -- vencen cuando el ciclo terminó. Si `marcar_vencidos`
    no hiciera la misma distinción, el día que un dueño prende la tolerancia
    el cron pasaría a VENCIDO a medio padrón y `enviar_recordatorios` les
    mandaría «tu cuota está vencida» a alumnos que, por diseño, no están
    bloqueados: la ráfaga masiva que la fecha de activación existe para evitar,
    entrando por la otra puerta.

    Función pura, sin ORM, como `ciclo_vigente`.
    """
    if tolerancia is None or activacion is None or periodo_inicio < activacion:
        return None
    return tolerancia


def limite_de_pago(periodo_inicio, periodo_fin, tolerancia, activacion):
    """El último día (INCLUSIVO) en que la cuota se puede pagar sin vencer.

    - Sin tolerancia efectiva: el último día del período, `periodo_fin`.
    - Con tolerancia N: el día anterior a que se cruce el umbral de bloqueo.
      `acceso.py` bloquea cuando `periodo_inicio <= hoy - N`, o sea a partir
      del día `periodo_inicio + N`; el último día libre es `periodo_inicio +
      N - 1`. Con N = 0 el límite queda ANTES de que arranque el ciclo: la
      cuota nace vencida y bloqueando, que es lo que significa "cero días".

    Vencer, bloquear y avisar «por vencer» se calculan los tres a partir de
    esta fecha, para que no haya un día en que el alumno esté bloqueado y la
    app le diga «Pendiente», ni un aviso que llegue cuando el acceso ya se
    cortó.
    """
    efectiva = tolerancia_efectiva(periodo_inicio, tolerancia, activacion)
    if efectiva is None:
        return periodo_fin
    return periodo_inicio + timedelta(days=efectiva - 1)


def filtro_por_limite_de_pago(tolerancia, activacion, *, hasta, desde=None):
    """El `Q` equivalente a `desde <= limite_de_pago(...) <= hasta` para las
    cuotas de los gimnasios con ese par (tolerancia, activación).

    Es `limite_de_pago` escrita en SQL, para que `marcar_vencidos` y el aviso
    de «por vencer» del cron no traigan todo el padrón a memoria. Como el
    umbral se resuelve en Python (`periodo_inicio + columna` da resultados
    distintos en SQLite y en Postgres), la tolerancia y la activación llegan
    como escalares y el queryset solo compara fechas contra constantes.
    **Si tocás `limite_de_pago`, esto tiene que seguirla**: hay un test que
    recorre un rango de días y compara las dos.
    """
    from django.db.models import Q

    def entre(campo, corrimiento):
        # `campo + corrimiento` en [desde, hasta]  <=>  `campo` en
        # [desde - corrimiento, hasta - corrimiento].
        condicion = Q(**{f"{campo}__lte": hasta - corrimiento})
        if desde is not None:
            condicion &= Q(**{f"{campo}__gte": desde - corrimiento})
        return condicion

    por_fin_de_ciclo = entre("periodo_fin", timedelta(0))
    if tolerancia is None or activacion is None:
        return por_fin_de_ciclo
    return (
        Q(periodo_inicio__gte=activacion)
        & entre("periodo_inicio", timedelta(days=tolerancia - 1))
    ) | (Q(periodo_inicio__lt=activacion) & por_fin_de_ciclo)


def regimenes_de_pago():
    """Los ids de gimnasio agrupados por `(tolerancia, fecha de activación)`.

    Se agrupa por VALOR y no por gimnasio para que el costo de los barridos
    (`marcar_vencidos`, el cron de recordatorios) no crezca con la cantidad
    de tenants: son un puñado de pares distintos.
    """
    from collections import defaultdict

    from tenants.models import Gimnasio

    regimenes = defaultdict(list)
    for gimnasio_id, tolerancia, activacion in Gimnasio.objects.values_list(
        "id", "dias_tolerancia_pago", "fecha_activacion_bloqueo"
    ):
        regimenes[(tolerancia, activacion)].append(gimnasio_id)
    return regimenes

