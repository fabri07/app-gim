"""Forms de `tenants`: personalización white-label (Fase 4) de un gimnasio ya
existente.

El form de registro se borró junto con `RegisterView`: el alta de gimnasios
dejó de ser self-serve y se hace con `manage.py crear_gimnasio`.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.core.files.uploadedfile import UploadedFile
from PIL import Image

from tenants.models import Gimnasio, Perfil

_FONDO_IMAGEN_TAMANIO_MAXIMO = 5 * 1024 * 1024
# El fondo se pinta con `background-size: cover` (`base.html`), o sea que el
# navegador YA recorta la imagen centrada al tamaño de cada pantalla: la
# FORMA de la imagen no importa, su resolución sí. Por eso el piso no es
# "ancho >= 1280 Y alto >= 720" (que rechazaba una foto cuadrada de 1080x1075
# con más píxeles que el mínimo, y una foto vertical de celular solo por la
# orientación) sino superficie + lado más corto:
#
#   1. al menos tantos píxeles como una 1280x720, y
#   2. ningún lado por debajo de 720 -- una panorámica de 4000x250 supera el
#      punto 1 pero con `cover` hay que estirarle el alto a la pantalla entera.
_FONDO_IMAGEN_RESOLUCION_REFERENCIA = (1280, 720)
_FONDO_IMAGEN_LADO_MINIMO = 720
_FONDO_IMAGEN_FORMATOS_VALIDOS = {"JPEG", "PNG"}

# El logo es un asset más chico que el fondo: presupuesto de tamaño menor y
# piso de resolución menor. El piso de 200x200 no es arbitrario --
# notificaciones/icons.py::generar_icono estira el logo a un ícono PWA de
# hasta 512x512 (ImageOps.pad); sin este mínimo un logo muy chico queda
# pixelado ahí. Expresado con los mismos dos knobs que el fondo, da
# exactamente el criterio de siempre para cualquier forma de logo.
_LOGO_TAMANIO_MAXIMO = 2 * 1024 * 1024
_LOGO_RESOLUCION_REFERENCIA = (200, 200)
_LOGO_LADO_MINIMO = 200
_LOGO_FORMATOS_VALIDOS = {"JPEG", "PNG"}


def _lado_cuadrado_equivalente(resolucion_referencia):
    """Lado de la imagen CUADRADA que tiene la misma superficie que
    `resolucion_referencia`, redondeado a la decena de abajo: "960×960" le
    dice más a un dueño de gimnasio que "921.600 píxeles". Lo usan el mensaje
    de error y el `help_text`, que tienen que decir el mismo número.
    """
    ancho, alto = resolucion_referencia
    return int((ancho * alto) ** 0.5) // 10 * 10


def _validar_imagen(
    archivo,
    *,
    resolucion_referencia,
    lado_minimo,
    tamanio_maximo_bytes,
    formatos_validos,
    etiqueta,
    mensaje_tamanio,
):
    """Valida un archivo de imagen recién subido: tamaño máximo, que Pillow
    pueda abrirlo, formato permitido y resolución mínima -- lógica compartida
    por `clean_fondo_imagen` y `clean_logo`, cada uno con sus propios
    umbrales. Devuelve el archivo con el puntero al principio (PIL lo consume
    al leer, y el form todavía necesita el archivo completo para guardarlo en
    el storage) si pasa todas las validaciones, o levanta
    `forms.ValidationError` en la primera que falle.

    La resolución se mide como superficie + lado más corto, no como ancho y
    alto por separado: así el criterio no depende de la ORIENTACIÓN de la
    imagen (ver el comentario de `_FONDO_IMAGEN_RESOLUCION_REFERENCIA`).
    `etiqueta` es el sujeto de los mensajes de error ("La imagen" / "El
    logo"), que se arman acá para que digan cuánto mide la imagen que
    subieron -- sin eso el dueño no tiene forma de saber qué le falta.
    """
    if archivo.size > tamanio_maximo_bytes:
        raise forms.ValidationError(mensaje_tamanio)
    try:
        imagen = Image.open(archivo)
        ancho, alto = imagen.size
        formato = imagen.format
    except Exception:
        raise forms.ValidationError("El archivo no es una imagen válida.")
    if formato not in formatos_validos:
        # Ordenado alfabéticamente para que el mensaje sea determinístico
        # (un `set` no garantiza orden de iteración) -- para el caso actual
        # de los dos únicos llamadores ({"JPEG", "PNG"}) da exactamente
        # "JPEG o PNG".
        formatos_legibles = " o ".join(sorted(formatos_validos))
        raise forms.ValidationError(f"Solo se aceptan imágenes {formatos_legibles}.")
    if min(ancho, alto) < lado_minimo:
        raise forms.ValidationError(
            f"{etiqueta} mide {ancho}×{alto}px y ningún lado puede ser menor "
            f"a {lado_minimo}px."
        )
    referencia_ancho, referencia_alto = resolucion_referencia
    if ancho * alto < referencia_ancho * referencia_alto:
        mensaje = (
            f"{etiqueta} mide {ancho}×{alto}px y es muy chica. Necesita al "
            f"menos la superficie de una imagen de "
            f"{referencia_ancho}×{referencia_alto}px"
        )
        if referencia_ancho != referencia_alto:
            # Con una referencia ya cuadrada la aclaración sobraría.
            lado = _lado_cuadrado_equivalente(resolucion_referencia)
            mensaje += f" — una cuadrada de {lado}×{lado}px también sirve."
        else:
            mensaje += "."
        raise forms.ValidationError(mensaje)
    archivo.seek(0)
    return archivo


class GimnasioForm(forms.ModelForm):
    """Personalización del gimnasio (Fase 4, "Personalización por
    gimnasio"). No es `TenantScopedModelForm`: `Gimnasio` ES el tenant, no
    tiene FK a otro `TenantOwnedModel` para acotar. `slug` y `activo` quedan
    afuera a propósito -- son de gestión de la plataforma, no algo que el
    dueño edite desde su propio panel; siguen disponibles en `/admin/`.
    """

    class Meta:
        model = Gimnasio
        fields = [
            "nombre",
            "tipo_publico",
            "logo",
            "paleta",
            "tipografia",
            "fondo_tipo",
            "fondo_imagen",
            "fondo_doodle",
            "texto_bienvenida",
            "contacto",
            "link_instagram",
            "link_whatsapp",
            "link_facebook",
            "dias_tolerancia_pago",
        ]
        widgets = {
            "fondo_tipo": forms.RadioSelect,
            "fondo_doodle": forms.RadioSelect,
            # Los tres links son `URLField`: "@migimnasio" o un teléfono
            # suelto no validan. El placeholder muestra la forma exacta
            # mientras se tipea, antes de que aparezca el error -- el dueño
            # de un gimnasio no tiene por qué saber qué es una URL.
            "contacto": forms.TextInput(
                attrs={"placeholder": "Ej: 11 2345-6789 · hola@migimnasio.com"}
            ),
            "link_instagram": forms.URLInput(
                attrs={"placeholder": "https://www.instagram.com/migimnasio"}
            ),
            "link_whatsapp": forms.URLInput(
                attrs={"placeholder": "https://wa.me/5491123456789"}
            ),
            "link_facebook": forms.URLInput(
                attrs={"placeholder": "https://www.facebook.com/migimnasio"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # fondo_doodle es blank=True en el modelo -> Django agrega un choice
        # vacío ("---------") al formfield por defecto. Para una grilla de
        # exactamente 4 miniaturas no queremos un 5to radio "ninguno" -- la
        # ausencia de selección ya se resuelve con required=False (heredado
        # de blank=True) más la validación cruzada en clean().
        self.fields["fondo_doodle"].choices = Gimnasio.Doodle.choices
        # `help_text` armado acá (no hardcodeado en el template) para que
        # los números mostrados nunca puedan desincronizarse de los
        # umbrales que `clean_logo`/`clean_fondo_imagen` realmente aplican
        # -- una sola fuente de verdad para las dos puntas.
        self.fields["logo"].help_text = (
            f"JPEG o PNG, hasta {_LOGO_TAMANIO_MAXIMO // (1024 * 1024)} MB, "
            f"mínimo {_LOGO_RESOLUCION_REFERENCIA[0]}×{_LOGO_RESOLUCION_REFERENCIA[1]}px. "
            "Fondo transparente se ve mejor."
        )
        # El fondo se recorta solo (`background-size: cover`), así que la
        # ayuda tiene que hablar de resolución, no de forma: sin la aclaración
        # de la foto cuadrada, el dueño con un logo de 1080x1075 supone que le
        # falta ancho y no lo intenta.
        _fondo_ref = "×".join(str(n) for n in _FONDO_IMAGEN_RESOLUCION_REFERENCIA)
        _fondo_lado = _lado_cuadrado_equivalente(_FONDO_IMAGEN_RESOLUCION_REFERENCIA)
        self.fields["fondo_imagen"].help_text = (
            f"JPEG o PNG, hasta {_FONDO_IMAGEN_TAMANIO_MAXIMO // (1024 * 1024)} MB. "
            f"Al menos la superficie de una foto de {_fondo_ref}px "
            f"(una cuadrada de {_fondo_lado}×{_fondo_lado}px también sirve), "
            f"y ningún lado menor a {_FONDO_IMAGEN_LADO_MINIMO}px. "
            "Se recorta sola y centrada para llenar la pantalla."
        )

    def clean_fondo_imagen(self):
        archivo = self.cleaned_data.get("fondo_imagen")
        if not archivo or not isinstance(archivo, UploadedFile):
            # No hay archivo nuevo en este request: es el valor ya guardado
            # (o ninguno) -- ya pasó esta validación cuando se subió.
            return archivo
        return _validar_imagen(
            archivo,
            resolucion_referencia=_FONDO_IMAGEN_RESOLUCION_REFERENCIA,
            lado_minimo=_FONDO_IMAGEN_LADO_MINIMO,
            tamanio_maximo_bytes=_FONDO_IMAGEN_TAMANIO_MAXIMO,
            formatos_validos=_FONDO_IMAGEN_FORMATOS_VALIDOS,
            etiqueta="La imagen",
            mensaje_tamanio="La imagen no puede pesar más de 5 MB.",
        )

    def clean_logo(self):
        archivo = self.cleaned_data.get("logo")
        if not archivo or not isinstance(archivo, UploadedFile):
            # No hay archivo nuevo en este request: es el valor ya guardado
            # (o ninguno) -- ya pasó esta validación cuando se subió.
            return archivo
        return _validar_imagen(
            archivo,
            resolucion_referencia=_LOGO_RESOLUCION_REFERENCIA,
            lado_minimo=_LOGO_LADO_MINIMO,
            tamanio_maximo_bytes=_LOGO_TAMANIO_MAXIMO,
            formatos_validos=_LOGO_FORMATOS_VALIDOS,
            etiqueta="El logo",
            mensaje_tamanio="El logo no puede pesar más de 2 MB.",
        )

    def clean(self):
        cleaned_data = super().clean()
        fondo_tipo = cleaned_data.get("fondo_tipo")
        if fondo_tipo == Gimnasio.FondoTipo.DOODLE and not cleaned_data.get(
            "fondo_doodle"
        ):
            self.add_error("fondo_doodle", "Elegí un doodle para este modo de fondo.")
        # "fondo_imagen" not in self.errors: si clean_fondo_imagen() ya rechazó
        # el archivo (pesa de más, formato, resolución), Django lo sacó de
        # cleaned_data -- sin este guard sumaríamos un segundo error que dice
        # "subí una imagen" a alguien que SÍ subió una. La plantilla muestra
        # errors.0 y taparía el engaño, pero form.errors completo (lector de
        # pantalla, un {{ form.errors }} futuro) vería el mensaje contradictorio.
        if (
            fondo_tipo == Gimnasio.FondoTipo.IMAGEN
            and not cleaned_data.get("fondo_imagen")
            and "fondo_imagen" not in self.errors
        ):
            self.add_error(
                "fondo_imagen",
                "Subí una imagen para este modo de fondo (o elegí otro tipo).",
            )
        return cleaned_data


class ResetPasswordStaffForm(PasswordResetForm):
    """Olvidé mi contraseña -- SOLO para cuentas de staff/dueño.

    `PasswordResetForm.get_users()` busca por `User.email`, pero
    `alumnos/services.py::crear_acceso` también puebla ese campo cuando el
    staff elige email (no teléfono) como identificador del alumno -- el
    mismo campo que usaría una cuenta de staff. Sin este override, un
    alumno con email como identificador podría auto-resetear su propia
    contraseña, contradiciendo la decisión de producto de que el staff es
    quien asigna/regenera el acceso del alumno. Punto de extensión
    recomendado por la propia documentación de Django para restringir el
    reset a un subconjunto de usuarios.

    Un email que no matchea (no existe, es de un alumno, está inactivo, o
    no tiene contraseña usable) sigue mostrando la misma pantalla genérica
    de "si el email existe, te mandamos instrucciones" -- comportamiento
    anti-enumeración que Django ya trae por default, sin tocar nada acá.
    """

    def get_users(self, email):
        UserModel = get_user_model()
        activos = UserModel._default_manager.filter(
            **{
                f"{UserModel.get_email_field_name()}__iexact": email,
                "is_active": True,
                "perfil__rol": Perfil.Rol.STAFF,
            }
        )
        return (u for u in activos if u.has_usable_password())
