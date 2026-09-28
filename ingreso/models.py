"""Control de ingreso al gimnasio: quién puede pasar por la puerta y qué pasó.

La app NUNCA guarda biometría. El lector facial guarda la plantilla en su
propia memoria e identifica a la persona; acá solo se guarda el id que esa
persona tiene EN el equipo (`Credencial` de tipo FACIAL). Ver
`docs/superpowers/specs/2026-09-27-control-de-ingreso-design.md`.
"""

from django.db import models
from django.db.models.functions import Lower

from core.models import TenantOwnedModel, validar_gimnasio_de


class ConfiguracionIngreso(TenantOwnedModel):
    """Qué hace la puerta de ESTE gimnasio. Una fila por gimnasio.

    Modelo propio y no cuatro campos en `Gimnasio` porque
    `Gimnasio.version_media` deriva de `modificado` y versiona la URL del logo,
    la del fondo y la del ícono de la PWA: guardar una preferencia de la puerta
    le haría volver a bajar todo eso a cada celular del gimnasio.
    """

    bloquea_por_deuda = models.BooleanField(
        default=False,
        verbose_name="no dejar entrar al que debe la cuota",
        help_text=(
            "Usa la misma regla que el portal: los días de tolerancia se "
            "configuran en «Mi gimnasio»."
        ),
    )
    exige_reserva = models.BooleanField(
        default=False,
        verbose_name="pedir turno reservado para entrar",
        help_text="Solo para gimnasios que trabajan con cupos estrictos.",
    )
    minutos_antipassback = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="minutos entre dos ingresos de la misma persona",
        help_text="0 lo apaga. Con 2 alcanza para evitar la doble lectura.",
    )

    class Meta:
        verbose_name = "configuración de control de ingreso"
        verbose_name_plural = "configuraciones de control de ingreso"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio"], name="config_ingreso_unica_por_gimnasio"
            )
        ]

    def __str__(self):
        return f"Control de ingreso de {self.gimnasio}"


class Dispositivo(TenantOwnedModel):
    """Un lector o molinete de la puerta.

    Sin `DeleteView`: se desactiva destildando `activo`, y el `PROTECT` de
    `EventoIngreso.dispositivo` lo respalda -- borrarlo se llevaría el
    historial de asistencia. Mismo criterio que `Ejercicio`.
    """

    class Tipo(models.TextChoices):
        QR = "qr", "Lector de QR"
        FACIAL = "facial", "Lector facial"
        MIXTO = "mixto", "Lee QR y cara"

    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.QR)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    activo = models.BooleanField(default=True)
    ultimo_contacto = models.DateTimeField(null=True, blank=True, editable=False)
    # El tope diario de `ingreso/limites.py`: eventos NUEVOS registrados por
    # este lector en `eventos_hoy_fecha` (día local). Un contador y no un
    # `COUNT` de `EventoIngreso`: contar por `momento` lo esquiva un lote en
    # diferido fechado en los siete días anteriores, y contar por `creado`
    # recorre todo el historial del lector en cada request.
    eventos_hoy = models.PositiveIntegerField(default=0, editable=False)
    eventos_hoy_fecha = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "lector de la puerta"
        verbose_name_plural = "lectores de la puerta"
        ordering = ["nombre"]
        constraints = [
            # Sin distinguir mayúsculas, la misma regla que
            # `DispositivoForm.clean_nombre`: «Puerta» y «puerta» en el mismo
            # gimnasio hacen ilegible el listado de ingresos. Es una
            # constraint por EXPRESIÓN (índice único sobre
            # `(gimnasio_id, lower(nombre))`, con el gimnasio adelante para
            # que sirva también al listado), soportada por SQLite y Postgres.
            # Ojo: en SQLite tanto `lower()` como el `iexact` del form pliegan
            # solo ASCII (una «Á» no se iguala a «á» en la suite); en Postgres
            # los dos pliegan Unicode. Form y base coinciden en cada motor.
            models.UniqueConstraint(
                models.F("gimnasio"), Lower("nombre"),
                name="dispositivo_nombre_ci_unico_por_gim",
            )
        ]

    def __str__(self):
        return self.nombre


class Credencial(TenantOwnedModel):
    """Lo que identifica al alumno en la puerta.

    `alumno` es CASCADE y no PROTECT: una credencial no significa nada sin su
    alumno, y con PROTECT no se podría borrar nunca a un alumno cargado por
    error que ya pasó una vez por la puerta. `core/borrado.py` la lista sola en
    el aviso, porque lee `_meta.related_objects`.

    Para las FACIAL, `valor` es el id que el alumno tiene EN el equipo -- la
    cara se enrola en el lector y el staff copia ese id acá. Nunca una
    plantilla biométrica.
    """

    class Tipo(models.TextChoices):
        QR = "qr", "Código QR"
        FACIAL = "facial", "Cara registrada en el lector"

    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="credenciales"
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    valor = models.CharField(max_length=64)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "credencial de ingreso"
        verbose_name_plural = "credenciales de ingreso"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "tipo", "valor"],
                name="credencial_valor_unico_por_gim",
            ),
            models.UniqueConstraint(
                fields=["gimnasio", "alumno", "tipo"],
                name="credencial_una_por_alumno_y_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.alumno}"

    def clean(self):
        validar_gimnasio_de(self.gimnasio, alumno=self.alumno)

    def save(self, *args, **kwargs):
        # La puerta compara contra `Lectura.valor_normalizado` (`strip()`): un
        # id facial copiado con un espacio o un salto de línea al final no
        # matchearía nunca, y "la cara no anda" sin que nadie sepa por qué.
        # Va en `save()` y no solo en `clean()` porque el QR nace por
        # `get_or_create` y un `ModelForm` no es el único camino de alta.
        # (`bulk_create`/`update()` no pasan por acá: los usa solo el QR, que
        # sale de `tokens.generar()` sin espacios.)
        self.valor = (self.valor or "").strip()
        super().save(*args, **kwargs)


class EventoIngreso(TenantOwnedModel):
    """Cada intento de pasar por la puerta, permitido o rechazado.

    El rechazado se guarda igual: es lo que le dice al dueño que alguien está
    intentando entrar con una tarjeta revocada, y lo que explica un reclamo.

    **El valor leído NO se guarda.** De una credencial desconocida quedan los
    últimos 6 caracteres en `detalle`: alcanza para diagnosticar "esta tarjeta
    no anda" sin duplicar miles de veces la llave de la puerta en una tabla que
    además se exporta.
    """

    class Resultado(models.TextChoices):
        PERMITIDO = "permitido", "Pasó"
        SIN_CREDENCIAL = "sin_credencial", "Credencial no reconocida"
        CREDENCIAL_INACTIVA = "credencial_inactiva", "Credencial dada de baja"
        ALUMNO_INACTIVO = "alumno_inactivo", "Alumno dado de baja"
        DEUDA = "deuda", "Cuota impaga"
        SIN_RESERVA = "sin_reserva", "Sin turno reservado"
        DUPLICADO = "duplicado", "Ya había entrado"

    class Sentido(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SALIDA = "salida", "Salida"

    dispositivo = models.ForeignKey(
        Dispositivo, on_delete=models.PROTECT, related_name="eventos"
    )
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE,
        related_name="eventos_ingreso", null=True, blank=True,
    )
    credencial = models.ForeignKey(
        Credencial, on_delete=models.SET_NULL,
        related_name="eventos", null=True, blank=True,
    )
    resultado = models.CharField(max_length=24, choices=Resultado.choices)
    momento = models.DateTimeField()
    sentido = models.CharField(
        max_length=10, choices=Sentido.choices, default=Sentido.ENTRADA
    )
    en_diferido = models.BooleanField(default=False)
    id_evento_equipo = models.CharField(max_length=64, blank=True)
    detalle = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "ingreso"
        verbose_name_plural = "ingresos"
        ordering = ["-momento", "-id"]
        indexes = [
            models.Index(
                fields=["gimnasio", "-momento", "-id"], name="ingreso_listado_idx"
            ),
            models.Index(
                fields=["gimnasio", "alumno", "-momento"],
                name="ingreso_antipassback_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dispositivo", "id_evento_equipo"],
                condition=~models.Q(id_evento_equipo=""),
                name="ingreso_id_del_equipo_unico",
            )
        ]

    def __str__(self):
        return f"{self.get_resultado_display()} - {self.momento:%d/%m %H:%M}"


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config de un gimnasio: garantiza que la fila
    exista. Ojo que `turnos.models` tiene una función con este mismo nombre:
    el módulo que necesite las dos las importa calificadas por módulo."""
    config, _ = ConfiguracionIngreso.objects.get_or_create(gimnasio=gimnasio)
    return config
