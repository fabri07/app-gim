"""Form de registro: credenciales del usuario + nombre del nuevo Gimnasio."""

from django import forms
from django.contrib.auth.forms import UserCreationForm


class RegistroForm(UserCreationForm):
    nombre_gimnasio = forms.CharField(max_length=120, label="Nombre del gimnasio")

    class Meta(UserCreationForm.Meta):
        fields = ("username",)  # password1/2 los aporta UserCreationForm
