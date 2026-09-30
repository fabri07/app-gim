from django import forms

from suscripcion.models import IntentoDeCobro


class ElegirPlanForm(forms.Form):
    """El único dato que elige el gimnasio: mensual o anual. El período y el
    monto NO viajan en el form: los calcula `services.crear_intento` contra la
    base, así que no hay nada del precio que el cliente pueda tocar."""

    plan = forms.ChoiceField(
        label="Plan",
        choices=IntentoDeCobro.Plan.choices,
        widget=forms.RadioSelect,
        initial=IntentoDeCobro.Plan.MENSUAL,
    )
