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
        from pagos.models import Cuota, filtro_por_limite_de_pago, regimenes_de_pago

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

        # "Por vencer": la cuota PENDIENTE cuyo último día para pagar
        # (`Cuota.fecha_limite_pago`) cae entre hoy y dentro de
        # `DIAS_AVISO_PAGO` días. La fecha límite depende del régimen del
        # gimnasio: fin del ciclo sin tolerancia, `periodo_inicio +
        # tolerancia - 1` con ella. Una versión anterior medía la ventana
        # desde el ARRANQUE del ciclo, así que en un gimnasio sin tolerancia
        # (todos, hoy) el aviso salía el día 1 diciendo «vence el día 28» --
        # y como el dedup es por cuota, cerca del vencimiento real no llegaba
        # nada. Se agrupa por régimen y no por gimnasio para que el costo no
        # crezca con la cantidad de tenants, y el umbral se calcula en Python
        # (`periodo_inicio + columna` anda en Postgres y da otra cosa en
        # SQLite).
        regimenes = regimenes_de_pago()
        pagos_por_vencer = 0
        for (tolerancia, activacion), gimnasio_ids in regimenes.items():
            for pago in (
                Cuota.objects.filter(
                    gimnasio_id__in=gimnasio_ids, estado=Cuota.Estado.PENDIENTE
                )
                .filter(
                    filtro_por_limite_de_pago(
                        tolerancia,
                        activacion,
                        desde=hoy,
                        hasta=hoy + timedelta(days=DIAS_AVISO_PAGO),
                    )
                )
                .select_related("gimnasio", "alumno")
            ):
                services.notificar_pago_por_vencer(pago)
                pagos_por_vencer += 1


        pagos_vencidos = 0
        for pago in Cuota.objects.filter(
            estado=Cuota.Estado.VENCIDO, modificado__date=hoy
        ).select_related("gimnasio", "alumno"):
            services.notificar_pago_vencido(pago)
            pagos_vencidos += 1

        # "Acceso bloqueado": las cuotas impagas que HOY cruzan el umbral de
        # tolerancia de su gimnasio. Mismo agrupado por régimen que arriba.
        accesos_bloqueados = 0
        for (tolerancia, activacion), gimnasio_ids in regimenes.items():
            if tolerancia is None or activacion is None:
                continue  # ese gimnasio no bloquea a nadie

            # Solo las que cruzan el umbral HOY: avisar todos los días sería
            # acoso, y el dedup de `RecordatorioEnviado` solo cubre repetirlo
            # para la misma cuota.
            for pago in Cuota.objects.filter(
                gimnasio_id__in=gimnasio_ids,
                estado__in=Cuota.ESTADOS_IMPAGOS,
                periodo_inicio=hoy - timedelta(days=tolerancia),
                periodo_inicio__gte=activacion,
            ).select_related("gimnasio", "alumno"):
                services.notificar_acceso_bloqueado(pago)
                accesos_bloqueados += 1

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
                f"{rutinas_iniciadas} rutinas iniciadas, "
                f"{accesos_bloqueados} accesos bloqueados."
            )
        )
