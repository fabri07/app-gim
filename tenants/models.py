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
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.templatetags.static import static

from core.models import TenantOwnedModel, TimeStampedModel


def doodle_static_url(doodle):
    """URL del SVG de un doodle, o "" si no hay doodle o el archivo no existe.

    El fallback NO es paranoia: el doodle se resuelve en `base.html`, que se
    renderiza en TODA página autenticada. Fuera de DEBUG los estáticos los
    sirve `CompressedManifestStaticFilesStorage`, y un `{% static %}` sin
    entrada en el manifest levanta `ValueError` -- o sea que un gimnasio con
    un `fondo_doodle` cuyo archivo no se desplegó (o se renombró después, sin
    migración de datos) vería un 500 en todo el sitio, incluida
    `gimnasio_editar`, que es la única pantalla donde podría corregirlo. Un
    doodle que no se ve es mucho mejor que un tenant encerrado afuera.
    """
    if not doodle:
        return ""
    try:
        return static(f"img/doodles/{doodle}.svg")
    except ValueError:
        return ""


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

    class TipoPublico(models.TextChoices):
        """A qué público atiende el gimnasio.

        No es un dato decorativo: define si tiene sentido preguntar el sexo de
        cada alumno y si la composición del padrón se mira por género o por
        edad. En un gimnasio de un solo género, «Alumnos por género» es una
        sola barra y «Ejercicios más asignados por género» es una copia exacta
        del gráfico general.
        """

        MIXTO = "mixto", "Mixto"
        MUJERES = "mujeres", "Solo mujeres"
        HOMBRES = "hombres", "Solo hombres"

    #: Qué valor de `Alumno.Sexo` implica cada tipo de público. Única fuente de
    #: verdad de ese mapeo, mismo criterio que `PALETAS`/`TIPOGRAFIA_FUENTES`.
    #: `MIXTO` no está a propósito: ahí el dato lo carga el staff.
    SEXO_POR_TIPO_PUBLICO = {
        TipoPublico.MUJERES: "femenino",
        TipoPublico.HOMBRES: "masculino",
    }

    class Tipografia(models.TextChoices):
        PLUS_JAKARTA = "plus_jakarta", "Plus Jakarta Sans — geométrica, moderna"
        SORA = "sora", "Sora — técnica, alto contraste"
        MANROPE = "manrope", "Manrope — cálida y legible"
        OUTFIT = "outfit", "Outfit — redondeada, amigable"
        SPACE_GROTESK = "space_grotesk", "Space Grotesk — editorial, con carácter"

    #: Única fuente de verdad para mapear cada opción de `tipografia` a su
    #: familia CSS real y su query de Google Fonts (`base.html` y el preview
    #: en vivo de `gimnasio_form.html` la consumen, no la duplican).
    #: `PLUS_JAKARTA` (el default) es la ÚNICA sin `google_param`: está
    #: auto-hospedada vía @font-face en styles/input.css, así que la mayoría
    #: del tráfico -- cualquier gimnasio que no cambió el default -- nunca
    #: dispara una carga externa a Google. Mismo rol que "sistema" cumplía
    #: antes de sacarse del catálogo.
    TIPOGRAFIA_FUENTES = {
        Tipografia.PLUS_JAKARTA: {
            "css_family": "'Plus Jakarta Sans', var(--font-sans)",
            "google_param": None,
        },
        Tipografia.SORA: {
            "css_family": "'Sora', var(--font-sans)",
            "google_param": "Sora:wght@400;500;600;700",
        },
        Tipografia.MANROPE: {
            "css_family": "'Manrope', var(--font-sans)",
            "google_param": "Manrope:wght@400;500;600;700",
        },
        Tipografia.OUTFIT: {
            "css_family": "'Outfit', var(--font-sans)",
            "google_param": "Outfit:wght@400;500;600;700",
        },
        Tipografia.SPACE_GROTESK: {
            "css_family": "'Space Grotesk', var(--font-sans)",
            "google_param": "Space+Grotesk:wght@400;500;600;700",
        },
    }

    class Paleta(models.TextChoices):
        BOSQUE = "bosque", "Bosque"
        OCEANO = "oceano", "Océano"
        ARENA = "arena", "Arena"
        PIZARRA = "pizarra", "Pizarra"

    #: Misma idea que TIPOGRAFIA_FUENTES: única fuente de verdad para los 3
    #: roles de color de cada paleta -- base.html, el preview en vivo y
    #: landing.html la consumen, no la reinventan. Paletas curadas en vez de
    #: color libre: ninguna combinación puede resultar ilegible.
    PALETAS = {
        Paleta.BOSQUE: {"fondo": "#f5ede4", "primario": "#1d6f56", "secundario": "#e8735c"},
        Paleta.OCEANO: {"fondo": "#eef3f6", "primario": "#1e3a5f", "secundario": "#e2a03f"},
        Paleta.ARENA: {"fondo": "#faf6f0", "primario": "#b4532a", "secundario": "#2f6b63"},
        Paleta.PIZARRA: {"fondo": "#f0f1f3", "primario": "#33475b", "secundario": "#5b8c5a"},
    }

    class FondoTipo(models.TextChoices):
        COLOR = "color", "Paisaje de color"
        IMAGEN = "imagen", "Imagen propia"
        DOODLE = "doodle", "Doodle temático"

    class Doodle(models.TextChoices):
        MANCUERNAS = "mancuernas", "Mancuernas"
        SOGAS = "sogas", "Sogas de battle rope"
        DISCOS = "discos", "Discos apilados"
        KETTLEBELL = "kettlebell", "Kettlebells"

    tipo_publico = models.CharField(
        "Público del gimnasio",
        max_length=10,
        choices=TipoPublico.choices,
        default=TipoPublico.MIXTO,
        help_text="En un gimnasio de un solo género no se le pregunta el sexo "
        "a cada alumno (se completa solo) y el panel muestra la composición "
        "del padrón por edad en vez de por género.",
    )
    logo = models.ImageField(upload_to="logos/", blank=True)
    paleta = models.CharField(
        max_length=20,
        choices=Paleta.choices,
        default=Paleta.BOSQUE,
        help_text="Paleta de colores del panel y del portal del alumno.",
    )
    #: El fondo (canvas atmosférico / imagen propia / doodle) es independiente
    #: de `paleta`: primario/secundario para botones y acentos SIEMPRE vienen
    #: de `paleta`, sin importar `fondo_tipo` -- evita combinaciones
    #: ilegibles de imagen/doodle + acento no armonizado.
    fondo_tipo = models.CharField(
        max_length=10,
        choices=FondoTipo.choices,
        default=FondoTipo.COLOR,
        help_text="Qué se ve detrás del contenido: paisaje de color, una imagen propia, o un doodle temático.",
    )
    fondo_imagen = models.ImageField(upload_to="fondos/", blank=True)
    fondo_doodle = models.CharField(max_length=20, choices=Doodle.choices, blank=True)
    tipografia = models.CharField(
        max_length=20,
        choices=Tipografia.choices,
        default=Tipografia.PLUS_JAKARTA,
        help_text="Tipografía del panel y del portal del alumno.",
    )
    texto_bienvenida = models.CharField(max_length=280, blank=True)
    contacto = models.CharField(max_length=120, blank=True)
    link_instagram = models.URLField(blank=True)
    link_whatsapp = models.URLField(blank=True)
    link_facebook = models.URLField(blank=True)
    dia_vencimiento_pago = models.PositiveSmallIntegerField(
        "Día límite de pago mensual (en desuso)",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text="OBSOLETO desde la migración a cuotas por ciclo: el "
        "vencimiento ya no cae un día fijo del mes sino a los "
        "`dias_tolerancia_pago` del arranque del ciclo de cada alumno. Se "
        "deja la columna un release para que revertir el código alcance "
        "como vuelta atrás (mismo criterio que `Ejercicio.grupo_muscular`).",
    )
    dias_tolerancia_pago = models.PositiveSmallIntegerField(
        "Días de tolerancia antes de bloquear",
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(60)],
        help_text="Días que el alumno puede seguir viendo su rutina y "
        "reservando turnos después de que arranca un ciclo impago. Pasados "
        "esos días se le bloquean esas dos secciones hasta que el staff "
        "confirme el pago. Dejalo VACÍO para no bloquear a nadie.",
    )
    #: Cuándo se prendió el bloqueo por primera vez. Lo estampa una señal en la
    #: transición `dias_tolerancia_pago` None -> valor, igual que
    #: `Alumno.fecha_baja`. Existe para que prender la función NO sea
    #: retroactivo: sin esto, el día que el dueño la activa quedan bloqueados
    #: de golpe todos los que arrastren cualquier cuota impaga histórica (el
    #: que estuvo de licencia, el que pagó en efectivo y nadie confirmó, el
    #: becado). `bloqueo_de` ignora las cuotas anteriores a esta fecha.
    fecha_activacion_bloqueo = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "gimnasio"
        verbose_name_plural = "gimnasios"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def fondo_doodle_url(self):
        """URL del doodle elegido (vacía si no hay o si falta el archivo).

        Las plantillas la usan como condición además de como valor: si es ""
        no entran a la rama de doodle y cae el canvas de color de siempre.
        Ver `doodle_static_url` para por qué puede venir vacía.
        """
        return doodle_static_url(self.fondo_doodle)

    @property
    def tiene_publico_mixto(self):
        """Si tiene sentido distinguir alumnos por género en este gimnasio.

        Lo consulta el dashboard (qué tarjeta de composición del padrón
        mostrar) y `AlumnoForm` (si pedir el campo «Sexo» o completarlo solo).
        Vive acá y no como comparación suelta en cada consumidor para que la
        regla no pueda divergir entre los dos.
        """
        return self.tipo_publico == self.TipoPublico.MIXTO

    def sexo_implicito(self):
        """El `Alumno.Sexo` que corresponde por definición, o `None` si es
        mixto (ahí el dato lo elige el staff, no se deduce).

        El import es tardío a propósito: `Alumno` hereda de `TenantOwnedModel`
        y ya importa `tenants`, así que a nivel de módulo sería circular.
        Mismo patrón que `tenants/analitica.py`.
        """
        valor = self.SEXO_POR_TIPO_PUBLICO.get(self.tipo_publico)
        if valor is None:
            return None
        from alumnos.models import Alumno

        # El dict guarda el string crudo (no se puede importar `Alumno` a
        # nivel de módulo), así que se resuelve contra el catálogo real: si
        # alguien renombra un valor de `Alumno.Sexo`, esto revienta acá en vez
        # de escribir en silencio un sexo que ya no existe.
        return Alumno.Sexo(valor)

    @property
    def tipografia_css_family(self):
        return self.TIPOGRAFIA_FUENTES[self.tipografia]["css_family"]

    @property
    def tipografia_google_param(self):
        return self.TIPOGRAFIA_FUENTES[self.tipografia]["google_param"]

    @property
    def color_fondo_css(self):
        return self.PALETAS[self.paleta]["fondo"]

    @property
    def color_primario_css(self):
        return self.PALETAS[self.paleta]["primario"]

    @property
    def color_secundario_css(self):
        return self.PALETAS[self.paleta]["secundario"]


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
