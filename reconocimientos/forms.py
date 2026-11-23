from django import forms
from django.db import transaction
from django.utils import timezone

from core.forms import TenantScopedModelForm
from reconocimientos.models import Premio


class PremioForm(TenantScopedModelForm):
    """Alta/edición de un premio. Incluye `activo`: no hay botón de borrar
    (mismo patrón que `MedioCobro`), se destilda. Marcar `por_defecto` desmarca
    el anterior en la misma transacción."""

    class Meta:
        model = Premio
        fields = [
            "nombre", "tipo", "valor", "cuotas", "texto",
            "da_insignia", "por_defecto", "activo",
        ]
        widgets = {"texto": forms.TextInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Un obsequio no usa cuotas: el campo puede venir vacío.
        self.fields["cuotas"].required = False

    def clean_cuotas(self):
        cuotas = self.cleaned_data.get("cuotas")
        return 1 if cuotas is None else cuotas

    def clean(self):
        datos = super().clean()
        # Un premio inactivo no puede ser el «por defecto»: si no, al guardarlo
        # le sacaría el default a uno activo.
        if not datos.get("activo"):
            datos["por_defecto"] = False
        return datos

    def save(self, commit=True):
        instancia = super().save(commit=False)
        if not commit:
            return instancia
        with transaction.atomic():
            if instancia.por_defecto:
                # `update()` no dispara `auto_now`: `modificado` explícito.
                Premio.objects.for_gimnasio(self.gimnasio).filter(
                    por_defecto=True
                ).exclude(pk=instancia.pk).update(
                    por_defecto=False, modificado=timezone.now()
                )
            instancia.save()
            self.save_m2m()
        return instancia
