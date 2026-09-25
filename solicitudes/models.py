"""
Solicitudes de acceso (embudo público de captación de gimnasios).

El registro self-serve está cerrado (ver `tenants/services.py`): nadie crea un
`Gimnasio` por su cuenta. En su lugar, un interesado deja una **solicitud** que
el superadmin revisa a mano y, si aprueba, acuña el tenant con la ÚNICA fuente
de alta (`tenants.services.crear_gimnasio`). Esta app es esa capa de moderación
por delante del alta cerrada, no un registro nuevo.

**Ninguno de estos modelos es `TenantOwnedModel`** —y `SolicitudAcceso` ni
siquiera tiene FK a `Gimnasio` hasta que se aprueba— porque son datos *de la
plataforma*, no *de* un gimnasio: los ve el superadmin, no el staff; no se
exportan con «Exportar mis datos» ni se borran al vaciar la demo. Mismo criterio
que `plataforma.PagoPlataforma`. Por eso quedan afuera de `vaciar_gimnasio`, del
fixture `_ensuciar` y de `HOJAS`/`EXCLUIDOS` del exportador.
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class SolicitudAcceso(TimeStampedModel):
    """Un interesado que pidió una cuenta. Máquina de estados con doble opt-in.

    El email arranca SIN verificar (`NO_VERIFICADA`): recién cuando el
    interesado confirma su mail pasa a `PENDIENTE` y entra a la cola de
    revisión del superadmin. De ahí sale a `APROBADA` (se acuñó el gimnasio),
    `RECHAZADA`, o `LISTA_ESPERA` (postergada, no terminal). Una solicitud sin
    verificar que envejece pasa a `EXPIRADA`.
    """

    class Estado(models.TextChoices):
        NO_VERIFICADA = "no_verificada", "Sin verificar el email"
        PENDIENTE = "pendiente", "Pendiente de revisión"
        LISTA_ESPERA = "lista_espera", "En lista de espera"
        APROBADA = "aprobada", "Aprobada"
        RECHAZADA = "rechazada", "Rechazada"
        EXPIRADA = "expirada", "Expirada"

    #: Estados en los que un email todavía tiene un trámite "abierto": no se
    #: admite un segundo. Cerrada la solicitud (aprobada/rechazada/expirada) sí
    #: se puede volver a solicitar.
    ESTADOS_ABIERTOS = (Estado.NO_VERIFICADA, Estado.PENDIENTE, Estado.LISTA_ESPERA)

    class AniosOperando(models.TextChoices):
        MENOS_1 = "menos_1", "Menos de 1 año"
        ENTRE_1_3 = "entre_1_3", "Entre 1 y 3 años"
        ENTRE_3_10 = "entre_3_10", "Entre 3 y 10 años"
        MAS_10 = "mas_10", "Más de 10 años"

    class TamanoStaff(models.TextChoices):
        SOLO_YO = "solo_yo", "Solo yo"
        ENTRE_2_5 = "entre_2_5", "2 a 5 personas"
        ENTRE_6_15 = "entre_6_15", "6 a 15 personas"
        MAS_15 = "mas_15", "Más de 15 personas"

    class BandaIngresos(models.TextChoices):
        PREFIERE_NO_DECIR = "no_dice", "Prefiero no decirlo"
        BAJA = "baja", "Chica"
        MEDIA = "media", "Mediana"
        ALTA = "alta", "Grande"

    class FormatoRegistros(models.TextChoices):
        PAPEL = "papel", "Papel / cuaderno"
        EXCEL = "excel", "Excel o planilla"
        WHATSAPP = "whatsapp", "WhatsApp / notas sueltas"
        OTRA_APP = "otra_app", "Otra app"
        NADA = "nada", "No llevo registro"

    class ProfundidadHistoria(models.TextChoices):
        SIN_HISTORIA = "sin_historia", "Arranco de cero"
        MESES = "meses", "Unos meses"
        UN_ANIO = "un_anio", "Alrededor de un año"
        VARIOS_ANIOS = "varios_anios", "Varios años"

    class PuedeCompartirArchivos(models.TextChoices):
        SI = "si", "Sí, tengo los datos a mano"
        NO = "no", "No"
        NO_SE = "no_se", "No estoy seguro"

    # --- Identidad del lead ---
    nombre_gimnasio = models.CharField("nombre del gimnasio", max_length=200)
    email = models.EmailField(
        max_length=255,
        help_text="Se normaliza a minúsculas (mismo criterio que el alta).",
    )
    nombre_contacto = models.CharField("nombre de contacto", max_length=200)
    telefono = models.CharField("teléfono / WhatsApp", max_length=40, blank=True)
    cantidad_alumnos = models.PositiveIntegerField(
        "cantidad aproximada de alumnos",
        null=True,
        blank=True,
        help_text="Orientativo: es lo que define el escalón de precio.",
    )

    # --- Screening del negocio (catálogos cerrados, todos opcionales) ---
    anios_operando = models.CharField(
        max_length=20, choices=AniosOperando.choices, blank=True
    )
    tamano_staff = models.CharField(
        max_length=20, choices=TamanoStaff.choices, blank=True
    )
    banda_ingresos = models.CharField(
        max_length=20, choices=BandaIngresos.choices, blank=True
    )
    formato_registros = models.CharField(
        max_length=20, choices=FormatoRegistros.choices, blank=True
    )
    profundidad_historia = models.CharField(
        max_length=20, choices=ProfundidadHistoria.choices, blank=True
    )
    puede_compartir_archivos = models.CharField(
        max_length=10, choices=PuedeCompartirArchivos.choices, blank=True
    )
    comentario = models.TextField("comentario del interesado", max_length=2000, blank=True)

    # --- Estado y auditoría ---
    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.NO_VERIFICADA
    )
    verificada_en = models.DateTimeField(null=True, blank=True)
    decidida_en = models.DateTimeField(null=True, blank=True)
    # La ÚLTIMA decisión, denormalizada, para que el listado del panel no tenga
    # que hacer un subquery por fila. El historial completo vive en
    # `SolicitudEvento`.
    decidida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    motivo_decision = models.TextField(blank=True)
    gimnasio_creado = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Se llena al aprobar, cuando se acuña el tenant.",
    )
    # IP hasheada (sha256 + sal), nunca en claro: trazabilidad anti-abuso sin
    # guardar un dato personal.
    ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-creado"]
        constraints = [
            # Un solo trámite ABIERTO por email a la vez. Índice parcial:
            # funciona en SQLite (dev/tests) y Postgres (prod). Cerrada la
            # solicitud (aprobada/rechazada/expirada), el email puede volver a
            # solicitar. El choque por doble submit se maneja como respuesta
            # neutral en el service (try/except IntegrityError), sin revelar
            # que ya había un trámite.
            models.UniqueConstraint(
                fields=["email"],
                # Valores string literales (no `Estado.X`): dentro de la Meta
                # anidada, el nombre `Estado` de la clase externa no está en
                # scope. Coinciden con los `TextChoices` de arriba.
                condition=models.Q(
                    estado__in=["no_verificada", "pendiente", "lista_espera"]
                ),
                name="una_solicitud_abierta_por_email",
            )
        ]

    def __str__(self):
        return f"{self.nombre_gimnasio} <{self.email}> ({self.get_estado_display()})"


class SolicitudAccesoToken(TimeStampedModel):
    """Token de verificación de email (doble opt-in).

    Va en la URL del mail, no en el cuerpo. Se quema (`usado_en`) al verificar.
    NO hay token de contraseña acá: el link para que el aprobado defina su
    clave reusa el token de reset de Django (`default_token_generator`), ver
    `plataforma`/el email de invitación.
    """

    class Proposito(models.TextChoices):
        VERIFICACION = "verificacion", "Verificación de email"

    solicitud = models.ForeignKey(
        SolicitudAcceso, on_delete=models.CASCADE, related_name="tokens"
    )
    token = models.CharField(max_length=64, unique=True)
    proposito = models.CharField(
        max_length=20, choices=Proposito.choices, default=Proposito.VERIFICACION
    )
    expira = models.DateTimeField()
    usado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"token {self.get_proposito_display()} de {self.solicitud_id}"


class SolicitudEvento(TimeStampedModel):
    """Historial de transiciones de una solicitud (auditoría de decisiones).

    Cada `crear/verificar/aprobar/rechazar/lista_espera/expirar` deja una fila,
    en la misma transacción que el cambio de estado. Los campos denormalizados
    de `SolicitudAcceso` (`decidida_*`) son solo la última decisión; acá está
    el recorrido completo.
    """

    solicitud = models.ForeignKey(
        SolicitudAcceso, on_delete=models.CASCADE, related_name="eventos"
    )
    de_estado = models.CharField(max_length=20, choices=SolicitudAcceso.Estado.choices, blank=True)
    a_estado = models.CharField(max_length=20, choices=SolicitudAcceso.Estado.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="El superadmin que decidió; vacío si lo hizo el propio flujo (verificación, expiración).",
    )
    motivo = models.TextField(blank=True)

    class Meta:
        ordering = ["creado"]

    def __str__(self):
        return f"{self.solicitud_id}: {self.de_estado or '∅'} → {self.a_estado}"
