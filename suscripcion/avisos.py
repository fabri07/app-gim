"""Avisos de vencimiento de la suscripción, por mail al staff del gimnasio.

Sin esto nadie visita la pantalla de pago de la que no se enteró. Los dispara
`manage.py avisar_cobros` (cron diario). El retraso del cron es inocuo: un mail
a las 11 en vez de a las 9 da igual.
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from plataforma import facturacion, precios
from suscripcion import emails
from suscripcion.models import AvisoDeCobro
from tenants.models import Perfil

#: El cron no tiene `request` para armar la URL absoluta; mismo criterio que el
#: `USER_AGENT` de `plataforma/cambio.py`, que ya escribe el dominio.
URL_SITIO = "https://www.tugimapp.com"

Tipo = AvisoDeCobro.Tipo


def _tipo_de_aviso(dias):
    """Qué aviso corresponde a `dias` hasta el vencimiento (o None)."""
    if dias < 0:
        return Tipo.VENCIDO
    if dias == 0:
        return Tipo.VENCE_HOY
    if dias <= precios.DIAS_AVISO_COBRO:
        return Tipo.POR_VENCER
    return None


def avisar(hoy=None):
    """Manda los avisos que corresponden hoy y devuelve cuántos mandó.

    Costo fijo de queries: el monitor entero (una), los destinatarios de todos
    los gimnasios candidatos (una) y, por aviso nuevo, el dedup. Nunca una
    consulta por gimnasio para descubrir a quién avisar.
    """
    hoy = hoy or timezone.localdate()
    candidatos = []
    for fila in facturacion.filas_del_monitor(hoy):
        if not fila.factura or not fila.vencimiento:
            continue
        tipo = _tipo_de_aviso((fila.vencimiento - hoy).days)
        if tipo:
            candidatos.append((fila, tipo))
    if not candidatos:
        return 0

    destinatarios = defaultdict(list)
    filas = (
        Perfil.objects.filter(
            rol=Perfil.Rol.STAFF,
            gimnasio_id__in=[fila.gimnasio.pk for fila, _ in candidatos],
            usuario__is_active=True,
        )
        .exclude(usuario__email="")
        .values_list("gimnasio_id", "usuario__email")
    )
    for gimnasio_id, email in filas:
        destinatarios[gimnasio_id].append(email)

    mandados = 0
    for fila, tipo in candidatos:
        gimnasio = fila.gimnasio
        with transaction.atomic():
            _, creado = AvisoDeCobro.objects.get_or_create(
                gimnasio=gimnasio, periodo_desde=fila.vencimiento, tipo=tipo
            )
            if not creado:
                continue
            para = destinatarios.get(gimnasio.pk, [])
            if para:
                # Valores ya resueltos: el callback no vuelve a tocar la base.
                transaction.on_commit(
                    lambda g=gimnasio, p=para, t=tipo, v=fila.vencimiento, usd=fila.precio_usd: (
                        emails.enviar_aviso_cobro(
                            g, p, tipo=t, vencimiento=v, precio_usd=usd
                        )
                    )
                )
                mandados += 1
    return mandados
