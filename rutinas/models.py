"""
Rutinas de entrenamiento: plantillas reutilizables y su asignación a un
alumno concreto.

Dos pares de modelos:

- `RutinaPlantilla` / `RutinaPlantillaItem`: la plantilla que el staff
  diseña y reutiliza (p. ej. "Full body principiante"). Vive en el catálogo
  del gimnasio y se puede seguir editando con el tiempo.
- `RutinaAsignada` / `RutinaAsignadaItem`: el SNAPSHOT que queda cuando esa
  plantilla se asigna a un alumno en una fecha determinada. Es una copia
  congelada: editar la plantilla después no debe alterar lo que el alumno
  ya tiene asignado (mismo principio que `ItemPedido.precio_unitario` en
  ~/gestor-pedidos, que congela `producto.precio` al crear el pedido — ver
  ROADMAP Fase 1, sección "RutinaAsignada (snapshot — modelo clave)").

Los modelos "Item" (`RutinaPlantillaItem`, `RutinaAsignadaItem`) NO son
`TenantOwnedModel`: siempre se acceden a través de su padre
(`RutinaPlantilla` o `RutinaAsignada`), que ya está scopeado por gimnasio.
Repetir el FK `gimnasio` en el item sería redundante (mismo criterio que
`ItemPedido` en gestor-pedidos, que no repite `negocio` y solo lo tiene su
`Pedido` padre).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import TenantOwnedModel, TimeStampedModel

SEMANAS_POR_CICLO = 4


class RutinaPlantilla(TenantOwnedModel):
    """Plantilla de rutina reutilizable, diseñada por el staff.

    `objetivo` es texto libre (no un `TextChoices`): el ROADMAP lo describe
    con ejemplos ("Hipertrofia", "Fuerza"), no como un catálogo cerrado, y
    a diferencia de `Ejercicio.categoria` no hay ningún filtro de Fase 2
    que dependa de un set fijo de valores.
    """

    class Nivel(models.TextChoices):
        PRINCIPIANTE = "principiante", "Principiante"
        INTERMEDIO = "intermedio", "Intermedio"
        AVANZADO = "avanzado", "Avanzado"

    nombre = models.CharField(max_length=120)
    objetivo = models.CharField(max_length=120)
    nivel = models.CharField(max_length=15, choices=Nivel.choices)
    dias_por_semana = models.PositiveSmallIntegerField()
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plantilla de rutina"
        verbose_name_plural = "plantillas de rutina"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def duplicar(self):
        """Crea una copia independiente de esta plantilla y sus items.

        ROADMAP Fase 2 §4: "duplicar rutina existente (antes que crear desde
        cero)". Es lógica de modelo (no de vista) porque el copiado de los
        items debe ser atómico y reutilizable desde cualquier flujo futuro.
        La copia es completamente independiente: modificar los items de una
        no afecta a la otra (a diferencia del snapshot de `RutinaAsignada`,
        aquí SÍ seguimos editando ambas plantillas con FKs vivos a
        `Ejercicio`).
        """
        with transaction.atomic():
            copia = RutinaPlantilla.objects.create(
                gimnasio=self.gimnasio,
                nombre=f"{self.nombre} (copia)",
                objetivo=self.objetivo,
                nivel=self.nivel,
                dias_por_semana=self.dias_por_semana,
                activa=self.activa,
            )
            RutinaPlantillaItem.objects.bulk_create(
                [
                    RutinaPlantillaItem(
                        rutina=copia,
                        ejercicio=item.ejercicio,
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        kilos=item.kilos,
                        descanso=item.descanso,
                        notas=item.notas,
                        bloque=item.bloque,
                        dia_nombre=item.dia_nombre,
                    )
                    for item in self.items.all()
                ]
            )
        return copia


class RutinaPlantillaItem(TimeStampedModel):
    """Un ejercicio dentro de un día de una `RutinaPlantilla`."""

    rutina = models.ForeignKey(
        RutinaPlantilla,
        on_delete=models.CASCADE,
        related_name="items",
        # CASCADE: borrar la plantilla borra sus items, no tiene sentido que
        # sobrevivan huérfanos.
    )
    ejercicio = models.ForeignKey(
        "ejercicios.Ejercicio",
        on_delete=models.PROTECT,
        related_name="items_plantilla",
        # PROTECT: si un ejercicio sigue referenciado por una plantilla viva,
        # obligamos al staff a reasignarlo/quitarlo antes de borrarlo, en vez
        # de romper la plantilla en silencio.
    )
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
    orden = models.PositiveSmallIntegerField(help_text="Orden dentro del día.")
    series = models.PositiveSmallIntegerField()
    repeticiones = models.CharField(
        max_length=20,
        help_text='Notación libre: "10", "8-12", "AMRAP", etc.',
    )
    kilos = models.CharField(
        max_length=30,
        blank=True,
        help_text='Notación libre: "20kg", "corporal", "15kg c/u", etc.',
    )
    descanso = models.CharField(
        max_length=30, blank=True, help_text='Ej: "60s", "2 min".'
    )
    notas = models.TextField(blank=True)
    bloque = models.CharField(
        max_length=10,
        blank=True,
        help_text=(
            'Código de superserie: "A1", "B2". Los ejercicios del mismo '
            "bloque se hacen juntos, uno atrás del otro."
        ),
    )
    dia_nombre = models.CharField(
        max_length=80,
        blank=True,
        help_text='Nombre del día: "Tren superior · Core". Opcional.',
        # Denormalizado: el mismo texto se repite en todos los items de un
        # día. Es el mismo patrón que `categoria_snapshot`, y se resuelve al
        # leer con la regla "gana la semana más baja" de `agrupacion.py`. La
        # alternativa era un modelo `Dia` propio, con migración de datos, FK
        # en `crear_desde_plantilla` y cambio de forma en `dias_disponibles` y
        # en el agrupado del PDF -- demasiado para una etiqueta.
    )

    class Meta:
        verbose_name = "item de plantilla"
        verbose_name_plural = "items de plantilla"
        ordering = ["semana", "dia", "orden"]

    def __str__(self):
        return f"Día {self.dia} · {self.ejercicio.nombre}"


class RutinaAsignada(TenantOwnedModel):
    """El snapshot de una `RutinaPlantilla` asignado a un alumno concreto.

    A diferencia de los modelos "Item", ESTE sí es `TenantOwnedModel`: se
    consulta directamente por alumno a lo largo del tiempo (historial de
    rutinas asignadas), no solo a través de un padre.
    """

    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="rutinas_asignadas",
        # PROTECT: el historial de rutinas de un alumno no debe desaparecer
        # si se intenta borrar al alumno; primero hay que decidir qué hacer
        # con ese historial.
    )
    nombre_snapshot = models.CharField(max_length=120)
    objetivo_snapshot = models.CharField(max_length=120)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(
        null=True,
        blank=True,
        help_text="Cierre MANUAL, opcional. La vigencia NO depende de este "
        "campo sino de `fecha_inicio` (ver `vigente_de`): el fin real de un "
        "ciclo se deriva, no se persiste.",
    )
    activa = models.BooleanField(
        default=True,
        help_text="Archivada a mano. NO significa 'la que ve el alumno' -- eso "
        "lo decide `vigente_de` por fecha.",
    )

    class Meta:
        verbose_name = "rutina asignada"
        verbose_name_plural = "rutinas asignadas"
        # El `-id` desempata dos rutinas que arrancan el MISMO día (el caso de
        # reasignar hoy): un `ORDER BY` con empate no garantiza ningún orden en
        # Postgres y podía cambiar entre requests. `vigente_de` igual repite
        # este orden de forma explícita, porque depender del Meta es frágil
        # (un `.distinct()` o un `prefetch_related` de un caller lo anulan sin
        # ruido).
        ordering = ["-fecha_inicio", "-id"]
        indexes = [
            # `vigente_de` corre en CADA request del portal del alumno y de la
            # ficha, y desde que las rutinas ya no se archivan el historial de
            # un alumno crece sin techo. Sin esto solo estaba el índice del FK.
            models.Index(
                fields=["alumno", "-fecha_inicio", "-id"],
                name="rutina_vigente_idx",
            ),
        ]

    def __str__(self):
        return f"{self.alumno} · {self.nombre_snapshot} desde {self.fecha_inicio}"

    # ------------------------------------------------------------------
    # Vigencia: qué rutina le toca al alumno hoy
    # ------------------------------------------------------------------

    @classmethod
    def vigente_de(cls, *, alumno):
        """La rutina que el alumno tiene que estar viendo HOY, o `None`.

        Regla de producto: un plan dura 4 semanas y el alumno lo ve completas,
        aunque el profesor ya haya cargado el siguiente; cuando el ciclo
        termina pasa al nuevo, y si no hay siguiente se queda con el último.

        Todo eso lo resuelve **"la más reciente que YA arrancó"**, sin comparar
        contra el fin del ciclo:

        - mientras el plan viejo corre, el nuevo todavía no arrancó -> gana el
          viejo;
        - al llegar su fecha, el nuevo pasa a ser el más reciente arrancado;
        - terminado el ciclo y sin siguiente, el último sigue siendo el más
          reciente;
        - un plan cargado a futuro no se adelanta.

        **Nunca devuelve una rutina que no arrancó**, y por eso no tiene
        fallback: `proxima_de` es una función aparte. Si esta devolviera el
        plan programado cuando no hay ninguno vigente, TODOS los consumidores
        heredarían ese modo -- incluidas las tres escrituras del alumno, que
        lo dejarían marcar como entrenado y calificar un plan que no empezó,
        ensuciando la adherencia con la que el profesor ajusta las cargas.

        El scoping por gimnasio lo aporta el `alumno` ya resuelto por el
        caller (mismo criterio explícito que `crear_desde_plantilla` con su
        `gimnasio`). Keyword-only para que no se pueda confundir el argumento.
        """
        return (
            alumno.rutinas_asignadas.filter(
                activa=True, fecha_inicio__lte=timezone.localdate()
            )
            .order_by("-fecha_inicio", "-id")
            .first()
        )

    @classmethod
    def proxima_de(cls, *, alumno):
        """El próximo plan programado (el que todavía no arrancó), o `None`.

        SOLO para mostrar información: el estado vacío del portal ("tu plan
        empieza el DD/MM") y el aviso de la ficha. Ninguna escritura debe
        usarla -- ver el docstring de `vigente_de`.
        """
        return (
            alumno.rutinas_asignadas.filter(
                activa=True, fecha_inicio__gt=timezone.localdate()
            )
            .order_by("fecha_inicio", "id")
            .first()
        )

    @property
    def fecha_fin_prevista(self):
        """Primer día NO cubierto por el ciclo (`fecha_inicio + 4 semanas`).

        Es la fecha en la que corresponde arrancar el plan siguiente, así que
        se define EXCLUSIVA a propósito. Para mostrarle al humano "hasta el
        X" hay que restarle un día -- eso lo hace `ultimo_dia`.
        """
        return self.fecha_inicio + timedelta(weeks=SEMANAS_POR_CICLO)

    @property
    def ultimo_dia(self):
        """El último día que el ciclo SÍ cubre, para mostrar en pantalla."""
        return self.fecha_fin_prevista - timedelta(days=1)

    @property
    def es_futura(self) -> bool:
        return self.fecha_inicio > timezone.localdate()

    @property
    def ya_termino(self) -> bool:
        return timezone.localdate() >= self.fecha_fin_prevista

    @property
    def esta_vigente(self) -> bool:
        """Hoy cae dentro de sus 4 semanas.

        Ojo: no es lo mismo que "es la que ve el alumno". Un plan terminado
        sigue siendo el que ve el alumno mientras no haya otro (ver
        `vigente_de`); esta property es para rotular el estado en pantalla.
        """
        return not self.es_futura and not self.ya_termino

    @classmethod
    def crear_desde_plantilla(cls, *, gimnasio, alumno, plantilla, fecha_inicio):
        """Copia la plantilla (y sus items) al momento de la asignación.

        Editar la plantilla después NO debe afectar esta asignación — ver
        ROADMAP Fase 1, RutinaAsignada.

        `gimnasio` se recibe explícito (no se infiere de `plantilla.gimnasio`
        ni de `alumno.gimnasio`) para que quien llama sea explícito sobre en
        qué tenant está operando: validamos acá que los tres coincidan, en
        vez de confiar en que el caller ya filtró correctamente. Asignar una
        plantilla o un alumno de OTRO gimnasio sería un bug de aislamiento
        de tenant, así que lo tratamos como error de datos (`ValidationError`),
        no como un bug de programación (`AssertionError`).
        """
        if plantilla.gimnasio_id != gimnasio.id or alumno.gimnasio_id != gimnasio.id:
            raise ValidationError(
                "La plantilla y el alumno deben pertenecer al gimnasio indicado."
            )

        # Una rutina que arranca ANTES que la vigente nunca sería elegida por
        # `vigente_de` (que toma la más reciente): quedaría como una fila
        # invisible en la base. Es un error de datos, no una preferencia, así
        # que se corta acá y no solo en el form -- `crear_desde_plantilla` es
        # el único camino por el que la app crea una asignación, mismo criterio
        # que el guard cross-tenant de arriba.
        vigente = cls.vigente_de(alumno=alumno)
        if vigente is not None and fecha_inicio < vigente.fecha_inicio:
            raise ValidationError(
                f"La fecha de inicio no puede ser anterior a la del plan que "
                f"{alumno} está haciendo (desde el "
                f"{vigente.fecha_inicio:%d/%m/%Y}): esta rutina nunca llegaría "
                f"a verse."
            )

        # NO se archiva la rutina anterior ni se le escribe `fecha_fin`. Los
        # planes conviven y los ordena la fecha: el alumno tiene que poder
        # terminar sus 4 semanas aunque el profesor ya haya cargado el
        # siguiente (ver `vigente_de`). `fecha_fin` tampoco se persiste porque
        # sería un campo derivado que se desincroniza en cuanto se inserta un
        # plan entre dos existentes -- se deriva con `fecha_fin_prevista`,
        # mismo criterio que `semana_actual`.
        with transaction.atomic():
            asignada = cls.objects.create(
                gimnasio=gimnasio,
                alumno=alumno,
                nombre_snapshot=plantilla.nombre,
                objetivo_snapshot=plantilla.objetivo,
                fecha_inicio=fecha_inicio,
            )
            RutinaAsignadaItem.objects.bulk_create(
                [
                    RutinaAsignadaItem(
                        rutina_asignada=asignada,
                        ejercicio_nombre_snapshot=item.ejercicio.nombre,
                        ejercicio_video_snapshot=item.ejercicio.url_video,
                        categoria_snapshot=(
                            item.ejercicio.categoria.nombre
                            if item.ejercicio.categoria_id
                            else ""
                        ),
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        kilos=item.kilos,
                        descanso=item.descanso,
                        notas=item.notas,
                        bloque=item.bloque,
                        dia_nombre=item.dia_nombre,
                    )
                    for item in plantilla.items.select_related(
                        "ejercicio__categoria"
                    )
                ]
            )
        return asignada

    @property
    def semana_actual(self) -> int:
        """Semana del ciclo (1-4) que le toca a esta asignación hoy, según
        `fecha_inicio`. Se recalcula sola en cada acceso -- no es un campo
        persistido, así que nunca se desincroniza. Sin loop: una vez
        alcanzada la semana 4 se sostiene ahí hasta que el staff cierre esta
        asignación y cree una nueva."""
        dias_transcurridos = (timezone.localdate() - self.fecha_inicio).days
        if dias_transcurridos < 0:
            return 1
        return min(SEMANAS_POR_CICLO, (dias_transcurridos // 7) + 1)


class RutinaAsignadaItem(TimeStampedModel):
    """Un ejercicio dentro de un día de una `RutinaAsignada`, ya congelado.

    Sin FK viva a `Ejercicio`: ese es justamente el punto del snapshot —
    editar o borrar el `Ejercicio` original nunca debe alterar la rutina
    histórica de un alumno.
    """

    class RPE(models.TextChoices):
        MAS_INTENSO = "mas_intenso", "Podría hacer más intenso"
        SEGUIR_INTENSIDAD = "seguir_intensidad", "Podría seguir con esta intensidad"
        AL_LIMITE = "al_limite", "Estoy al límite"
        BAJAR_INTENSIDAD = "bajar_intensidad", "Debería bajar la intensidad"

    rutina_asignada = models.ForeignKey(
        RutinaAsignada,
        on_delete=models.CASCADE,
        related_name="items",
    )
    ejercicio_nombre_snapshot = models.CharField(max_length=120)
    # 500, igual que `Ejercicio.url_video`: este campo es una COPIA de aquel,
    # así que siempre tiene que ser al menos igual de ancho. Quedó en el
    # default de 200 de `URLField` cuando el origen se ensanchó
    # (`ejercicios/0004`, 2026-08-27) y asignar una rutina que usara uno de
    # esos links largos daba `DataError` en Postgres -- invisible en SQLite,
    # que no valida largos. Lo fija `AnchoDeCamposSnapshotTests`.
    ejercicio_video_snapshot = models.URLField(max_length=500, blank=True)
    categoria_snapshot = models.CharField(
        max_length=60,
        blank=True,
        help_text="NOMBRE VISIBLE de la categoría del ejercicio al momento "
        "de asignar la rutina (no un slug ni una FK): las categorías son "
        "por gimnasio desde 2026-08-26, así que no hay ningún catálogo "
        "global contra el cual traducir un código. Guardarlo ya renderizado "
        "es lo que deja a rutinas/agrupacion.py sin lookups. Vacío si el "
        "ejercicio no tenía categoría, o en asignaciones anteriores al "
        "campo -- agrupacion.py bucketea esos casos bajo 'Sin categoría' "
        "en vez de romper.",
    )
    rpe = models.CharField(
        max_length=20,
        choices=RPE.choices,
        blank=True,
        help_text="Cómo sintió el alumno la intensidad de este ejercicio "
        "esta semana. Lo carga el propio alumno desde su portal, no el "
        "staff. blank=True: recién se completa cuando el alumno lo califica.",
    )
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
    orden = models.PositiveSmallIntegerField(help_text="Orden dentro del día.")
    series = models.PositiveSmallIntegerField()
    repeticiones = models.CharField(max_length=20)
    kilos = models.CharField(
        max_length=30,
        blank=True,
        help_text='Notación libre: "20kg", "corporal", "15kg c/u", etc.',
    )
    descanso = models.CharField(max_length=30, blank=True)
    notas = models.TextField(blank=True)
    bloque = models.CharField(
        max_length=10,
        blank=True,
        help_text=(
            'Código de superserie: "A1", "B2". Los ejercicios del mismo '
            "bloque se hacen juntos, uno atrás del otro."
        ),
    )
    dia_nombre = models.CharField(
        max_length=80,
        blank=True,
        help_text='Nombre del día: "Tren superior · Core". Opcional.',
        # Denormalizado: el mismo texto se repite en todos los items de un
        # día. Es el mismo patrón que `categoria_snapshot`, y se resuelve al
        # leer con la regla "gana la semana más baja" de `agrupacion.py`. La
        # alternativa era un modelo `Dia` propio, con migración de datos, FK
        # en `crear_desde_plantilla` y cambio de forma en `dias_disponibles` y
        # en el agrupado del PDF -- demasiado para una etiqueta.
    )

    class Meta:
        verbose_name = "item de rutina asignada"
        verbose_name_plural = "items de rutina asignada"
        ordering = ["semana", "dia", "orden"]

    def __str__(self):
        return f"Día {self.dia} · {self.ejercicio_nombre_snapshot}"


class RutinaAsignadaDiaCompletado(TimeStampedModel):
    """Registro de que el alumno marcó un día (de una semana puntual del
    ciclo) como entrenado. NO es `TenantOwnedModel` -- se scopea a través de
    `rutina_asignada`, que ya está acotada por gimnasio (mismo patrón que
    `RutinaAsignadaItem` y `novedades.NovedadLeida`).

    Es un registro de "hecho/no hecho" a nivel día, no por ejercicio -- el
    alumno confirma la sesión completa de golpe al terminarla, no ejercicio
    por ejercicio (eso ya lo cubre el RPE de cada `RutinaAsignadaItem`).
    """

    rutina_asignada = models.ForeignKey(
        RutinaAsignada,
        on_delete=models.CASCADE,
        related_name="dias_completados",
    )
    dia = models.PositiveSmallIntegerField()
    semana = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
    )
    completado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "día completado"
        verbose_name_plural = "días completados"
        # Confirmar dos veces el mismo día/semana no debe duplicar el
        # registro -- el toggle borra en vez de insertar de nuevo.
        unique_together = ("rutina_asignada", "dia", "semana")

    def __str__(self):
        return f"{self.rutina_asignada} · día {self.dia}, semana {self.semana}"
