"""Vistas públicas del embudo de solicitud (sin auth).

La cola de revisión del superadmin NO vive acá, vive en `plataforma/` (reusa
`SuperadminRequiredMixin`).
"""

from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from solicitudes import antispam
from solicitudes.forms import SolicitarAccesoForm
from solicitudes.services import crear_solicitud, verificar


class SolicitarAccesoView(FormView):
    """Formulario público. Al enviar, crea una `SolicitudAcceso` sin verificar
    y dispara el mail de doble opt-in.

    Neutral a enumeración y a bots: pase lo que pase (bot detectado, IP con
    rate-limit, email duplicado, o alta real) responde SIEMPRE la misma
    pantalla de "revisá tu email", sin revelar qué ocurrió."""

    template_name = "solicitudes/solicitar.html"
    form_class = SolicitarAccesoForm
    success_url = reverse_lazy("solicitudes:enviada")

    def form_valid(self, form):
        if antispam.honeypot_lleno(form.cleaned_data.get("website")) or antispam.demasiado_rapido(
            form.cleaned_data.get("tiempo")
        ):
            return super().form_valid(form)  # bot: respuesta neutral, no crea nada

        ip_hash = antispam.hash_de(antispam.ip_de(self.request))
        email_hash = antispam.hash_de(form.cleaned_data["email"].lower())
        if antispam.rate_limit_excedido(ip_hash) or antispam.duplicado_reciente(email_hash):
            return super().form_valid(form)  # también neutral

        crear_solicitud(
            datos=form.datos_de_modelo(),
            ip_hash=ip_hash,
            url_verificacion_para=lambda tok: self.request.build_absolute_uri(
                reverse("solicitudes:verificar", args=[tok])
            ),
        )
        return super().form_valid(form)


class SolicitudEnviadaView(TemplateView):
    template_name = "solicitudes/enviada.html"


class VerificarAccesoView(View):
    """Confirma el email (doble opt-in) desde el link del mail."""

    def get(self, request, token):
        solicitud, _recien = verificar(
            token_str=token,
            url_panel_para=lambda s: request.build_absolute_uri(
                reverse("plataforma:solicitud_detalle", args=[s.pk])
            ),
        )
        return render(
            request,
            "solicitudes/verificada.html",
            {"ok": solicitud is not None},
        )
