"""Las escrituras del control de ingreso. Las vistas no escriben."""

from django.db import transaction

from alumnos.models import Alumno
from ingreso import tokens
from ingreso.models import Credencial


def generar_credencial_qr(alumno, *, regenerar=False):
    """El QR del alumno. Fijo: sirve impreso en una tarjeta para el que no
    tiene smartphone. `regenerar=True` lo reemplaza (el viejo deja de servir).

    Regenerar es un UPDATE en la misma fila, así que no queda rastro del
    código anterior: es el costo asumido de la unicidad por alumno.
    """
    credencial, creada = Credencial.objects.get_or_create(
        gimnasio=alumno.gimnasio,
        alumno=alumno,
        tipo=Credencial.Tipo.QR,
        defaults={"valor": tokens.generar()},
    )
    if not creada and regenerar:
        credencial.valor = tokens.generar()
        credencial.activa = True
        credencial.save(update_fields=["valor", "activa", "modificado"])
    return credencial


@transaction.atomic
def generar_credenciales_en_bloque(gimnasio):
    """Le crea el QR a todos los alumnos ACTIVOS que todavía no tienen.

    Un `bulk_create` y no una query por alumno: el gimnasio de 200 alumnos es
    justo el caso que motiva el botón.
    """
    ya_tienen = Credencial.objects.for_gimnasio(gimnasio).filter(
        tipo=Credencial.Tipo.QR
    ).values_list("alumno_id", flat=True)
    faltantes = (
        Alumno.objects.for_gimnasio(gimnasio)
        .filter(estado=Alumno.Estado.ACTIVO)
        .exclude(pk__in=ya_tienen)
    )
    nuevas = [
        Credencial(
            gimnasio=gimnasio, alumno=alumno,
            tipo=Credencial.Tipo.QR, valor=tokens.generar(),
        )
        for alumno in faltantes
    ]
    Credencial.objects.bulk_create(nuevas)
    return len(nuevas)
