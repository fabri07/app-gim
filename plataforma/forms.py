"""
Los formularios del panel de plataforma: dar de alta un gimnasio, registrar
un pago y ajustar cómo se le factura.

Todos se renderizan campo por campo con `partials/campo_form.html`, nunca
con `{{ form.as_p }}`: la `errorlist` de Django no tiene estilo en este
proyecto y sale en negro arriba de la etiqueta, indistinguible de una ayuda.
Un formulario que rechaza sin que se note es igual a uno que no guarda.
"""

from django import forms

from plataforma.models import PagoPlataforma
from tenants.models import Gimnasio
from tenants.services import normalizar_email


class PagoPlataformaForm(forms.ModelForm):
    """Alta de un pago ya cobrado.

    **`gimnasio` y `registrado_por` no están en el form a propósito.** El
    gimnasio sale de la URL y el usuario de la sesión, los dos estampados del
    lado del servidor por la vista -- mismo criterio que `TenantScopedMixin`
    en el resto del proyecto. Exponerlos dejaría que un POST armado a mano le
    acredite el pago de un cliente a otro.

    Igual el form necesita SABER de qué gimnasio se trata (se lo pasa la vista
    por `get_form_kwargs`) para poder avisar que ese período ya está cargado
    con un mensaje legible. La barrera de verdad sigue siendo la
    `UniqueConstraint` del modelo; esto es lo que hace que el choque se vea
    como un error al lado del campo y no como un 500.
    """

    def __init__(self, *args, gimnasio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gimnasio = gimnasio

    class Meta:
        model = PagoPlataforma
        fields = [
            "fecha_pago",
            "periodo_desde",
            "periodo_hasta",
            "alumnos_activos",
            "monto_usd",
            "tipo_cambio",
            "monto_ars",
            "notas",
        ]
        widgets = {
            "fecha_pago": forms.DateInput(attrs={"type": "date"}),
            "periodo_desde": forms.DateInput(attrs={"type": "date"}),
            "periodo_hasta": forms.DateInput(attrs={"type": "date"}),
            "notas": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        """Un período que termina antes de empezar no se guarda.

        El error se cuelga de `periodo_hasta` y no del formulario entero
        porque es ahí donde está lo que hay que corregir: un error general
        arriba de todo obliga a adivinar cuál de los dos campos mover.

        Duplica la `CheckConstraint` del modelo a propósito: sin esta
        validación el rechazo llega como un `IntegrityError` (un 500 mudo) en
        vez de un mensaje al lado del campo.
        """
        limpio = super().clean()
        desde = limpio.get("periodo_desde")
        hasta = limpio.get("periodo_hasta")
        if desde and hasta and hasta < desde:
            self.add_error(
                "periodo_hasta",
                "El período no puede terminar antes de empezar.",
            )
        if desde and self.gimnasio and self._ya_hay_un_pago_que_arranca(desde):
            self.add_error(
                "periodo_desde",
                "Ya hay un pago registrado que arranca ese día.",
            )
        return limpio

    def _ya_hay_un_pago_que_arranca(self, desde):
        """Si ese gimnasio ya tiene un pago con ese `periodo_desde`.

        Excluye la propia fila para que el día que exista una pantalla de
        edición, reguardar un pago sin cambiarle el período no choque contra
        sí mismo.
        """
        otros = PagoPlataforma.objects.filter(
            gimnasio=self.gimnasio, periodo_desde=desde
        )
        if self.instance.pk:
            otros = otros.exclude(pk=self.instance.pk)
        return otros.exists()


class FacturacionForm(forms.ModelForm):
    """Desde cuándo y si se le factura a un gimnasio.

    Es el único camino de UI para estos dos campos (fuera de `/admin/`): el
    dueño del gimnasio no los ve, `GimnasioForm.Meta.fields` los deja afuera.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # OBLIGATORIO en el form aunque sea `blank=True` en el modelo, mismo
        # criterio que `AlumnoForm.fecha_inicio_ciclo`: vaciarlo deshace la
        # migración de datos que estampó el arranque a todos los gimnasios
        # existentes, `inicio_efectivo` vuelve a caer en la fecha de alta y el
        # gimnasio pasa a VENCIDA con meses de atraso -- en silencio y desde
        # una pantalla que el dueño del producto abrió para otra cosa. Sigue
        # `blank=True` en el modelo porque un gimnasio nuevo nace en `NULL`
        # (= "desde la fecha de alta"), que es lo correcto para él.
        campo = self.fields["facturacion_inicio"]
        campo.required = True
        campo.help_text = (
            "El día desde el que se le factura el uso de la app. Los 30 días "
            "de prueba gratis se cuentan desde esta fecha."
        )

    class Meta:
        model = Gimnasio
        fields = ["facturacion_inicio", "facturacion_exenta"]
        widgets = {
            "facturacion_inicio": forms.DateInput(attrs={"type": "date"}),
        }


class CrearGimnasioForm(forms.Form):
    """Alta de un gimnasio y de la cuenta staff de su dueño.

    **No es un `ModelForm`.** Lo que se da de alta no es un `Gimnasio`: son
    tres filas (gimnasio, usuario y perfil) más las categorías de ejercicio
    iniciales, y todo eso lo arma `tenants.services.crear_gimnasio` adentro de
    una transacción. Un `ModelForm` guardaría el gimnasio por su cuenta y
    dejaría al servicio sin su atomicidad: un email repetido detectado tarde
    dejaría un gimnasio huérfano, sin dueño y sin forma de entrar.

    Es el mismo servicio que usa `manage.py crear_gimnasio`, que sigue
    existiendo: esta pantalla es un camino más cómodo, no un camino distinto.
    """

    nombre = forms.CharField(
        label="Nombre del gimnasio",
        max_length=120,
        help_text="Como lo va a ver el alumno en su app.",
    )
    email = forms.EmailField(
        label="Email del dueño",
        help_text=(
            "Es su usuario para entrar. Si va a entrar con Google, tiene que "
            "ser la cuenta de Google real."
        ),
    )
    slug = forms.SlugField(
        label="Dirección web",
        max_length=140,
        required=False,
        help_text=(
            "La parte que va en /g/… de su página pública y de su login. "
            "Vacío: se deriva del nombre."
        ),
    )
    es_demo = forms.BooleanField(
        label="Cuenta de demostración",
        required=False,
        help_text=(
            "Cuenta de demostración compartida: no puede cambiar su "
            "contraseña y se resiembra cada 6 h."
        ),
    )
    sin_password = forms.BooleanField(
        label="Sin contraseña",
        required=False,
        help_text="Solo login con Google.",
    )

    def clean_email(self):
        """Minúsculas y sin espacios, con la MISMA función que usa el alta.

        `User.objects.get(username=...)` es case-sensitive en Postgres: si
        esta pantalla normalizara distinto que el servicio, el dueño tipearía
        su mail como siempre y no entraría -- y no tendría forma de darse
        cuenta solo. Mismo criterio que `alumnos/identidad.py`.
        """
        return normalizar_email(self.cleaned_data["email"])

    def clean_slug(self):
        """Vacío devuelve `None` para que el servicio lo derive del nombre.

        Cadena vacía no sirve: `slug or slug_disponible(nombre)` la trataría
        igual, pero `None` dice explícitamente "elegilo vos" y es lo que
        espera la firma del servicio.

        **En minúsculas.** Esta pantalla es el primer lugar del proyecto donde
        una persona tipea un slug a mano: en todos los demás sale de
        `slugify()` adentro de `slug_disponible`, que siempre los devuelve en
        minúsculas. `SlugField` acepta mayúsculas, así que sin bajarlo un
        «Vida-Plena» dejaría la URL pública del gimnasio como
        `/g/Vida-Plena/login/`, distinta de todas las demás.

        Y se baja ANTES del chequeo de unicidad, no después: si se comparara
        lo tipeado contra lo guardado, `VIDA-PLENA` pasaría el control y
        chocaría recién contra el `unique=True` del modelo. Ese chequeo lo
        duplica a propósito: sin él el choque llega como `IntegrityError` (un
        500 mudo) en vez de un mensaje al lado del campo.
        """
        slug = (self.cleaned_data.get("slug") or "").lower() or None
        if slug and Gimnasio.objects.filter(slug=slug).exists():
            raise forms.ValidationError(
                "Ya hay un gimnasio con esa dirección web. Probá con otra."
            )
        return slug
