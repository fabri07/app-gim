"""Respaldo del vencimiento perezoso de la tienda, más los avisos que el
camino perezoso no manda (corre dentro de GETs y no puede tocar la red).

Lo corre `enviar-recordatorios.yml` cada 15 min. Idempotente: el dedup de los
avisos es `RecordatorioEnviado`, así que un pedido que venció perezosamente
igual recibe su aviso una sola vez."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from notificaciones import services as avisos
from tienda import services
from tienda.models import Pedido

VENTANA_AVISO_VENCIDO = timedelta(days=2)
DEMORA_REVISION = timedelta(hours=24)


class Command(BaseCommand):
    help = "Vence pedidos de la tienda sin comprobante y manda los avisos pendientes."

    def handle(self, *args, **options):
        ahora = timezone.now()
        vencidos = services.vencer_pedidos(ahora=ahora)

        recientes = Pedido.objects.filter(
            estado=Pedido.Estado.VENCIDO, modificado__gte=ahora - VENTANA_AVISO_VENCIDO
        ).select_related("gimnasio", "alumno__perfil__usuario")
        avisados = sum(avisos.notificar_pedido_vencido(p) for p in recientes)

        demorados = Pedido.objects.filter(
            estado=Pedido.Estado.EN_REVISION, comprobante_subido_en__lt=ahora - DEMORA_REVISION
        ).select_related("gimnasio", "alumno")
        recordados = sum(avisos.notificar_revision_demorada(p) for p in demorados)

        self.stdout.write(self.style.SUCCESS(
            f"Pedidos vencidos: {len(vencidos)}. Avisos de vencido: {avisados}. "
            f"Recordatorios de revisión: {recordados}."
        ))
