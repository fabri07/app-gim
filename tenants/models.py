"""
Núcleo de la arquitectura multi-tenant.

`Gimnasio` ES el tenant. `Perfil` conecta el usuario de autenticación de
Django con su Gimnasio y su rol, sin contaminar el modelo de auth (composición
sobre herencia: no extendemos ni reemplazamos User, lo enlazamos).

Adaptado de ~/gestor-pedidos/tenants/models.py (Negocio -> Gimnasio). Los
campos de white-label (logo, colores, texto de bienvenida, contacto, links)
se agregaron en Fase 1, según el modelo de datos del ROADMAP.
"""

from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel, TimeStampedModel


class Gimnasio(TimeStampedModel):
    """Un gimnasio/entrenador que usa el sistema. Unidad de aislamiento de
    datos (tenant).

    `creado` (heredado de TimeStampedModel) hace de fecha de alta; no se
    duplica un campo `fecha_alta` aparte.

    `logo`: en dev queda en el filesystem local (`MEDIA_ROOT`). Fase 5 cambia
    el storage a Cloudflare R2 vía `django-storages` sin tocar este campo —
    el filesystem de Render es efímero y nunca debe recibir uploads reales.
    """

    nombre = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    activo = models.BooleanField(default=True)

    class Tipografia(models.TextChoices):
        SISTEMA = "sistema", "Predeterminada (del sistema)"
        INTER = "inter", "Inter — moderna y neutra"
        MONTSERRAT = "montserrat", "Montserrat — deportiva, alto impacto"
        POPPINS = "poppins", "Poppins — cálida y redondeada"
        OSWALD = "oswald", "Oswald — condensada"
        PLAYFAIR = "playfair", "Playfair Display — serif clásica, premium"

    #: Única fuente de verdad para mapear cada opción de `tipografia` a su
    #: familia CSS real y su query de Google Fonts (`base.html` y el preview
    #: en vivo de `gimnasio_form.html` la consumen, no la duplican). `sistema`
    #: no tiene `google_param` a propósito: no dispara ninguna carga externa,
    #: mapea al `--font-sans` que Tailwind v4 ya aplica por preflight.
    TIPOGRAFIA_FUENTES = {
        Tipografia.SISTEMA: {"css_family": "var(--font-sans)", "google_param": None},
        Tipografia.INTER: {
            "css_family": "'Inter', var(--font-sans)",
            "google_param": "Inter:wght@400;500;600;700",
        },
        Tipografia.MONTSERRAT: {
            "css_family": "'Montserrat', var(--font-sans)",
            "google_param": "Montserrat:wght@400;500;600;700",
        },
        Tipografia.POPPINS: {
            "css_family": "'Poppins', var(--font-sans)",
            "google_param": "Poppins:wght@400;500;600;700",
        },
        Tipografia.OSWALD: {
            "css_family": "'Oswald', var(--font-sans)",
            "google_param": "Oswald:wght@400;500;600;700",
        },
        Tipografia.PLAYFAIR: {
            "css_family": "'Playfair Display', Georgia, serif",
            "google_param": "Playfair+Display:wght@400;500;600;700",
        },
    }

    logo = models.ImageField(upload_to="logos/", blank=True)
    color_primario = models.CharField(
        max_length=7, blank=True, help_text="Hex, p.ej. #2563eb"
    )
    color_secundario = models.CharField(
        max_length=7, blank=True, help_text="Hex, p.ej. #1e40af"
    )
    tipografia = models.CharField(
        max_length=20,
        choices=Tipografia.choices,
        default=Tipografia.SISTEMA,
        help_text="Tipografía del panel y del portal del alumno.",
    )
    texto_bienvenida = models.CharField(max_length=280, blank=True)
    contacto = models.CharField(max_length=120, blank=True)
    link_instagram = models.URLField(blank=True)
    link_whatsapp = models.URLField(blank=True)

    class Meta:
        verbose_name = "gimnasio"
        verbose_name_plural = "gimnasios"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def tipografia_css_family(self):
        return self.TIPOGRAFIA_FUENTES[self.tipografia]["css_family"]

    @property
    def tipografia_google_param(self):
        return self.TIPOGRAFIA_FUENTES[self.tipografia]["google_param"]


class Perfil(TimeStampedModel):
    """Vínculo 1:1 entre un usuario de Django, su Gimnasio y su rol.

    Diseño:
      - OneToOne con AUTH_USER_MODEL (settings, no `auth.User` hardcodeado):
        permite cambiar a un User custom en el futuro sin reescribir FKs.
      - Un Perfil pertenece a un único Gimnasio (FK). Un Gimnasio puede tener
        varios Perfiles (staff y alumnos) sin cambios de modelo.
      - on_delete CASCADE desde el User (si se borra el usuario, su perfil
        deja de tener sentido) pero PROTECT hacia el Gimnasio (no se borra un
        Gimnasio con usuarios vivos por accidente).
      - Roles del MVP (ver ROADMAP Fase 1): `staff` (dueño y/o entrenador,
        mismos permisos) y `alumno`. Separar dueño de entrenador queda para
        después.
    """

    class Rol(models.TextChoices):
        STAFF = "staff", "Staff"
        ALUMNO = "alumno", "Alumno"

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    gimnasio = models.ForeignKey(
        Gimnasio,
        on_delete=models.PROTECT,
        related_name="perfiles",
    )
    rol = models.CharField(max_length=10, choices=Rol.choices, default=Rol.STAFF)

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfiles"

    def __str__(self):
        return f"{self.usuario} @ {self.gimnasio} ({self.rol})"


class RegistroSuplantacion(TenantOwnedModel):
    """Auditoría de "entrar como este alumno".

    SÍ es `TenantOwnedModel`, a diferencia de `NovedadLeida` o las credenciales
    de Calendar: es dato operativo de un gimnasio y ningún staff debe ver las
    filas de otro. Scopearlo vía una FK no alcanzaría, porque la consulta
    natural es "todas las suplantaciones de MI gimnasio".

    `PROTECT` en las dos FK a propósito: una fila de auditoría no puede
    desaparecer por un cascade silencioso. El costo aceptado es que borrar un
    `User` con historial de suplantación queda bloqueado — consistente con que
    un `Alumno` nunca se borra (solo cambia de `estado`) y con el `PROTECT` de
    `TenantOwnedModel.gimnasio`.

    `creado` (de `TimeStampedModel`) hace de "iniciada_en": no se duplica el
    dato, mismo criterio que el resto del proyecto.
    """

    staff_usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="suplantaciones_iniciadas",
        verbose_name="staff que suplantó",
    )
    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="suplantaciones_recibidas",
    )
    finalizada_en = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Cuándo volvió a su cuenta. Queda en blanco si cerró la pestaña "
            "sin volver -- riesgo aceptado, ver ISSUES.md."
        ),
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "registro de suplantación"
        verbose_name_plural = "registros de suplantación"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.staff_usuario} como {self.alumno} ({self.creado:%Y-%m-%d %H:%M})"
