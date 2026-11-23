"""Cuadro de honor: los compañeros con una insignia de asistencia perfecta vigente.

La definición de «persona visible» es la MISMA que la del ranking
(`ranking.condiciones_de_persona`): un menor, alguien sin términos aceptados,
con opt-out o dado de baja no sale en el cuadro de nadie.
"""

from django.db.models import Count, Exists, OuterRef
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.services import resumen_del_ciclo
from reconocimientos.models import Premio, PremioOtorgado
from reconocimientos.ranking import _nombre_publico, condiciones_de_persona


def alumnos_visibles(gimnasio, hoy=None):
    """Alumnos del gimnasio que sus compañeros pueden ver."""
    hoy = hoy or timezone.localdate()
    return Alumno.objects.for_gimnasio(gimnasio).filter(condiciones_de_persona(hoy))


def cuadro_de_honor(gimnasio, hoy=None):
    """Nombres públicos («Lucía M.») de los visibles con una insignia vigente.

    Una sola query (`Exists`, así un alumno con dos premios vigentes no se
    repite). Orden alfabético por nombre público y luego por id: estable."""
    hoy = hoy or timezone.localdate()
    con_insignia = PremioOtorgado.objects.filter(
        alumno=OuterRef("pk"),
        estado=PremioOtorgado.Estado.OTORGADO,
        da_insignia=True,
        insignia_hasta__gte=hoy,
    )
    filas = (
        alumnos_visibles(gimnasio, hoy)
        .filter(Exists(con_insignia))
        .order_by()
        .values_list("id", "nombre", "apellido")
    )
    nombres = sorted((_nombre_publico(n, a), i) for i, n, a in filas)
    return [nombre for nombre, _ in nombres]


#: Cuántos premios muestra «Mi perfil» (los más recientes).
MAX_PREMIOS_PERFIL = 10


def _estado_del_premio(otorgado):
    """Frase de «Tus premios» para un otorgado vigente."""
    if otorgado.tipo == Premio.Tipo.OBSEQUIO:
        if otorgado.entregado_en:
            return "Entregado"
        return "Retiralo en el gimnasio"
    aplicadas = otorgado.n_aplicaciones
    if aplicadas == 0:
        return "Se aplicará en tu próxima cuota"
    return f"Aplicado en {aplicadas} de {otorgado.cuotas} cuotas"


def datos_del_alumno(alumno, hoy=None):
    """Contexto de «Mi perfil»: avance del ciclo, premios vigentes e insignia.

    Costo fijo en queries: las del resumen de asistencia más UNA de premios
    (con `n_aplicaciones` anotado). Los ANULADO no se muestran."""
    hoy = hoy or timezone.localdate()
    resumen = resumen_del_ciclo(alumno, hoy)
    otorgados = list(
        PremioOtorgado.objects.for_gimnasio(alumno.gimnasio)
        .filter(alumno=alumno, estado=PremioOtorgado.Estado.OTORGADO)
        .annotate(n_aplicaciones=Count("aplicaciones"))
        .order_by("-periodo_inicio", "-pk")[:MAX_PREMIOS_PERFIL]
    )
    premios = [
        {"nombre": o.nombre, "estado": _estado_del_premio(o), "texto": o.texto}
        for o in otorgados
    ]
    insignia_hasta = max(
        (o.insignia_hasta for o in otorgados
         if o.da_insignia and o.insignia_hasta and o.insignia_hasta >= hoy),
        default=None,
    )
    return {
        "resumen": resumen,
        "premios_alumno": premios,
        "insignia_hasta": insignia_hasta,
    }
