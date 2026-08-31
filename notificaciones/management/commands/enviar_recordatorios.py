"""Barrido único cada ~15 min (GitHub Actions, no hay Celery/worker en el
proyecto -- mismo mecanismo que `pagos/management/commands/generar_pagos.py`).

Cubre: novedades programadas para hoy, pagos por vencer, pagos recién
marcados vencidos, y turnos próximos. Es idempotente vía
`notificaciones.models.RecordatorioEnviado` (dedup dentro de cada
`notificar_*` de `notificaciones/services.py`) -- correrlo más de una vez no
duplica envíos.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

DIAS_AVISO_PAGO = 3
MINUTOS_AVISO_TURNO = 60


class Command(BaseCommand):
    help = "Envía recordatorios push: novedades programadas, pagos por vencer/vencidos, turnos próximos."

    def handle(self, *args, **options):
        from novedades.models import Novedad
        from rutinas.models import RutinaAsignada
        from pagos.models import PagoMensual
        from turnos.models import Reserva
        from turnos.services import _ahora_local
        from notificaciones import services

        hoy = timezone.localdate()
        ahora = _ahora_local()

        novedades_notificadas = 0
        for novedad in Novedad.objects.filter(activa=True, fecha_publicacion=hoy):
            services.notificar_novedad(novedad)
            novedades_notificadas += 1

        # Planes que arrancan HOY: el signal de creación los salteó porque en
        # ese momento eran futuros (el alumno no los veía todavía).
        rutinas_iniciadas = 0
        for rutina in RutinaAsignada.objects.filter(
            activa=True, fecha_inicio=hoy
        ).select_related("gimnasio", "alumno"):
            services.notificar_rutina_asignada(rutina)
            rutinas_iniciadas += 1

        pagos_por_vencer = 0
        for pago in PagoMensual.objects.filter(
            estado=PagoMensual.Estado.PENDIENTE, mes=hoy.month, anio=hoy.year
        ).select_related("gimnasio", "alumno"):
            dias_restantes = pago.gimnasio.dia_vencimiento_pago - hoy.day
            if 0 <= dias_restantes <= DIAS_AVISO_PAGO:
                services.notificar_pago_por_vencer(pago)
                pagos_por_vencer += 1

        pagos_vencidos = 0
        for pago in PagoMensual.objects.filter(
            estado=PagoMensual.Estado.VENCIDO, modificado__date=hoy
        ).select_related("gimnasio", "alumno"):
            services.notificar_pago_vencido(pago)
            pagos_vencidos += 1

        ventana_fin = ahora + timedelta(minutes=MINUTOS_AVISO_TURNO)
        if ventana_fin.date() == hoy:
            condicion_turno = Q(
                fecha=hoy, hora_inicio__gte=ahora.time(), hora_inicio__lte=ventana_fin.time()
            )
        else:
            # La ventana cruza medianoche (corrida cerca de las 23:xx): sin
            # este caso, `hora_inicio__gte=23:40 AND hora_inicio__lte=00:40`
            # no matchea ningún horario -- ningún turno de la última hora del
            # día recibiría su recordatorio.
            condicion_turno = Q(fecha=hoy, hora_inicio__gte=ahora.time()) | Q(
                fecha=ventana_fin.date(), hora_inicio__lte=ventana_fin.time()
            )
        turnos_proximos = 0
        for reserva in Reserva.objects.filter(condicion_turno).select_related(
            "gimnasio", "alumno"
        ):
            services.notificar_turno_proximo(reserva)
            turnos_proximos += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Recordatorios enviados: "
                f"{novedades_notificadas} novedades, "
                f"{pagos_por_vencer} pagos por vencer, "
                f"{pagos_vencidos} pagos vencidos, "
                f"{turnos_proximos} turnos próximos, "
                f"{rutinas_iniciadas} rutinas iniciadas."
            )
        )
