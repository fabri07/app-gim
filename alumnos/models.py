"""
Modelo de dominio: el alumno de un gimnasio.

`Alumno` es el primer `TenantOwnedModel` concreto del dominio (Fase 1). Hereda
de `core.models.TenantOwnedModel` el FK `gimnasio` (aislamiento por fila) y los
timestamps de `TimeStampedModel` (`creado` hace de fecha de alta; ver mismo
criterio en `Gimnasio`, no se duplica un campo `fecha_alta` aparte).

`perfil` (Fase 3) es el vínculo al login del alumno. Vive acá, no en
`tenants.Perfil`, para no invertir el orden de dependencia del proyecto
(core -> tenants -> alumnos/...; `tenants` no debe conocer `alumnos`). Un
`Perfil` con `rol=ALUMNO` sin `Alumno.perfil` apuntándolo no debería existir
en la práctica (se crean siempre juntos, ver `alumnos/views.py`), pero el
campo es `null=True` porque no todo alumno tiene acceso creado todavía.
"""

from django.db import models

from core.models import TenantOwnedModel


class Alumno(TenantOwnedModel):
    """Una persona entrenada por el gimnasio.

    `email` y `telefono` son `blank=True` porque no todo alumno tiene los
    dos datos de contacto (algunos gimnasios sólo tienen el teléfono, p.ej.
    tras migrar desde un Excel incompleto). El magic-link de Fase 3 podrá
    enviarse por cualquiera de los dos medios, según cuál exista.

    `fecha_activacion` queda en `None` hasta que el alumno usa por primera
    vez su magic-link (Fase 3); permite medir adopción real, no sólo alta
    administrativa.
    """

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        INACTIVO = "inactivo", "Inactivo"

    class Sexo(models.TextChoices):
        MASCULINO = "masculino", "Masculino"
        FEMENINO = "femenino", "Femenino"
        NO_DECIR = "no_decir", "Prefiere no decir"

    class FrecuenciaActividad(models.TextChoices):
        DIARIA = "diaria", "Diaria"
        VARIAS_POR_SEMANA = "varias_por_semana", "Varias veces por semana"
        UNA_POR_SEMANA = "una_por_semana", "Una vez por semana"
        OCASIONAL = "ocasional", "Ocasional"

    nombre = models.CharField(max_length=120)
    apellido = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.ACTIVO
    )
    #: Cuándo pasó a INACTIVO. Lo estampa `alumnos/signals.py::
    #: registrar_fecha_de_baja`, no una vista. Sin este campo, "bajas por mes"
    #: no es derivable: `modificado` cambia con cualquier edición, así que
    #: contar bajas por ahí daría un número inventado.
    fecha_baja = models.DateField(null=True, blank=True, editable=False)
    # Ficha de inscripción ampliada (cargada por el staff el día del alta):
    # todos blank=True a propósito -- no todo alumno cuenta todo el detalle
    # en el momento, y los alumnos ya existentes no tienen esta info.
    sexo = models.CharField(max_length=10, choices=Sexo.choices, blank=True)
    actividad_fisica_previa = models.BooleanField(
        default=False, help_text="¿Hacía actividad física antes de anotarse?"
    )
    frecuencia_actividad_previa = models.CharField(
        max_length=20,
        choices=FrecuenciaActividad.choices,
        blank=True,
        help_text="Solo si hacía actividad física antes.",
    )
    deportes_practica = models.CharField(
        max_length=200,
        blank=True,
        help_text='Qué deporte(s) practica, o "ninguno". Texto libre: no '
        "amerita un catálogo cerrado como sí lo tiene grupo_muscular.",
    )
    tiene_discapacidad = models.BooleanField(default=False)
    discapacidad_detalle = models.CharField(max_length=200, blank=True)
    tiene_enfermedad_cronica = models.BooleanField(default=False)
    enfermedad_cronica_detalle = models.CharField(max_length=200, blank=True)
    observaciones = models.TextField(blank=True)
    fecha_activacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cuándo entró por primera vez con su usuario/contraseña (Fase 3).",
    )
    perfil = models.OneToOneField(
        "tenants.Perfil",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alumno",
        help_text="Login del alumno (usuario/contraseña asignados por el staff).",
    )

    class Meta:
        verbose_name = "alumno"
        verbose_name_plural = "alumnos"
        ordering = ["apellido", "nombre"]

    def __str__(self):
        return f"{self.apellido}, {self.nombre}"
