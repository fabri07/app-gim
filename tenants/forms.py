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
_FONDO_IMAGEN_ANCHO_MINIMO = 1280
_FONDO_IMAGEN_ALTO_MINIMO = 720
_FONDO_IMAGEN_FORMATOS_VALIDOS = {"JPEG", "PNG"}

# El logo es un asset más chico que el fondo: presupuesto de tamaño menor y
# piso de resolución menor. El piso de 200x200 no es arbitrario --
# notificaciones/icons.py::generar_icono estira el logo a un ícono PWA de
# hasta 512x512 (ImageOps.pad); sin este mínimo un logo muy chico queda
# pixelado ahí.
_LOGO_TAMANIO_MAXIMO = 2 * 1024 * 1024
_LOGO_ANCHO_MINIMO = 200
_LOGO_ALTO_MINIMO = 200
_LOGO_FORMATOS_VALIDOS = {"JPEG", "PNG"}


def _validar_imagen(
    archivo,
    *,
    ancho_minimo,
    alto_minimo,
    tamanio_maximo_bytes,
    formatos_validos,
    mensaje_tamanio,
    mensaje_dimension,
):
    """Valida un archivo de imagen recién subido: tamaño máximo, que Pillow
    pueda abrirlo, formato permitido y dimensión mínima -- lógica compartida
    por `clean_fondo_imagen` y `clean_logo`, cada uno con sus propios
    umbrales y mensajes. Devuelve el archivo con el puntero al principio
    (PIL lo consume al leer, y el form todavía necesita el archivo completo
    para guardarlo en el storage) si pasa todas las validaciones, o levanta
    `forms.ValidationError` en la primera que falle.
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
    if ancho < ancho_minimo or alto < alto_minimo:
        raise forms.ValidationError(mensaje_dimension)
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
            "dia_vencimiento_pago",
        ]
        widgets = {
            "fondo_tipo": forms.RadioSelect,
            "fondo_doodle": forms.RadioSelect,
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
            f"mínimo {_LOGO_ANCHO_MINIMO}×{_LOGO_ALTO_MINIMO}px. "
            "Fondo transparente se ve mejor."
        )
        self.fields["fondo_imagen"].help_text = (
            f"JPEG o PNG, hasta {_FONDO_IMAGEN_TAMANIO_MAXIMO // (1024 * 1024)} MB, "
            f"mínimo {_FONDO_IMAGEN_ANCHO_MINIMO}×{_FONDO_IMAGEN_ALTO_MINIMO}px."
        )

    def clean_fondo_imagen(self):
        archivo = self.cleaned_data.get("fondo_imagen")
        if not archivo or not isinstance(archivo, UploadedFile):
            # No hay archivo nuevo en este request: es el valor ya guardado
            # (o ninguno) -- ya pasó esta validación cuando se subió.
            return archivo
        return _validar_imagen(
            archivo,
            ancho_minimo=_FONDO_IMAGEN_ANCHO_MINIMO,
            alto_minimo=_FONDO_IMAGEN_ALTO_MINIMO,
            tamanio_maximo_bytes=_FONDO_IMAGEN_TAMANIO_MAXIMO,
            formatos_validos=_FONDO_IMAGEN_FORMATOS_VALIDOS,
            mensaje_tamanio="La imagen no puede pesar más de 5 MB.",
            mensaje_dimension=(
                f"La imagen debe medir al menos "
                f"{_FONDO_IMAGEN_ANCHO_MINIMO}×{_FONDO_IMAGEN_ALTO_MINIMO}px."
            ),
        )

    def clean_logo(self):
        archivo = self.cleaned_data.get("logo")
        if not archivo or not isinstance(archivo, UploadedFile):
            # No hay archivo nuevo en este request: es el valor ya guardado
            # (o ninguno) -- ya pasó esta validación cuando se subió.
            return archivo
        return _validar_imagen(
            archivo,
            ancho_minimo=_LOGO_ANCHO_MINIMO,
            alto_minimo=_LOGO_ALTO_MINIMO,
            tamanio_maximo_bytes=_LOGO_TAMANIO_MAXIMO,
            formatos_validos=_LOGO_FORMATOS_VALIDOS,
            mensaje_tamanio="El logo no puede pesar más de 2 MB.",
            mensaje_dimension=(
                f"El logo debe medir al menos "
                f"{_LOGO_ANCHO_MINIMO}×{_LOGO_ALTO_MINIMO}px."
            ),
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
