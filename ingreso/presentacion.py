"""Qué se le muestra a cada uno del código de ingreso: al staff en la ficha del
alumno, y al alumno en el portal.

Vive en un solo lugar porque la pregunta «¿este código abre la puerta?» se
responde igual en las dos pantallas, y separadas divergen: una ficha que dibuja
como válido el QR de un alumno dado de baja manda al staff a imprimir una
tarjeta que la puerta va a rechazar.

Tres decisiones que conviene no deshacer:

1. **El QR se dibuja SOLO si abre la puerta** (credencial activa de un alumno
   activo). Uno dado de baja o de un alumno inactivo no se dibuja: una imagen
   de QR se lee como «esto funciona», y la puerta lo va a rechazar
   (`CREDENCIAL_INACTIVA` / `ALUMNO_INACTIVO`).
2. **El SVG se arma recién al leer `svg`**, no al construir: la tarjeta del
   portal solo muestra el estado y no paga el costo de dibujar.
3. **La deuda NO apaga el código.** La puerta decide (`ingreso/decision.py`):
   el alumno bloqueado ve igual su QR y un aviso de que no lo va a dejar pasar,
   para que no concluya que «la app anda mal». El aviso sale solo si ESTE
   gimnasio rechaza por deuda en la puerta, que es una opción aparte del
   bloqueo del portal.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone

from ingreso import qr
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
)

#: Cuánto puede estar callado un lector activo antes de que el panel lo marque.
#: Todo request autenticado del lector actualiza `ultimo_contacto`, y el poll
#: del padrón hace de latido aunque nadie pase por la puerta (el lector lo
#: pide cada pocos minutos para poder abrir sin internet). Una hora sin ningún
#: request es entonces un lector apagado, sin internet o con la clave mal
#: cargada: la puerta puede estar funcionando offline con un padrón viejo, o
#: directamente sin abrir. Una hora y no quince minutos para que un corte de
#: internet corto o un reinicio del equipo no tiñan el panel de ámbar por nada
#: -- un aviso que salta seguido se aprende a ignorar.
UMBRAL_LECTOR_SIN_CONTACTO = timedelta(hours=1)


class Estado:
    SIN_CODIGO = "sin_codigo"
    DADO_DE_BAJA = "dado_de_baja"
    ALUMNO_INACTIVO = "alumno_inactivo"
    VIGENTE = "vigente"


@dataclass(frozen=True)
class CodigoParaMostrar:
    estado: str
    credencial: Credencial | None

    @property
    def vigente(self):
        return self.estado == Estado.VIGENTE

    @property
    def svg(self):
        # Doble candado con el template: aunque alguien lo llame sobre un
        # código que no abre, no sale ninguna imagen.
        return qr.svg_de(self.credencial.valor) if self.vigente else ""


def codigo_de(alumno, credencial):
    """El estado del QR de `alumno`, a partir de su credencial QR ya traída
    (o `None`). No consulta la base: quien llama decide cómo traerla."""
    if credencial is None:
        return CodigoParaMostrar(Estado.SIN_CODIGO, None)
    if not credencial.activa:
        return CodigoParaMostrar(Estado.DADO_DE_BAJA, credencial)
    if alumno.estado != alumno.Estado.ACTIVO:
        return CodigoParaMostrar(Estado.ALUMNO_INACTIVO, credencial)
    return CodigoParaMostrar(Estado.VIGENTE, credencial)


def qr_de(alumno):
    """La credencial QR del alumno, o `None`. Una query."""
    return alumno.credenciales.filter(tipo=Credencial.Tipo.QR).first()


def la_puerta_rechaza_por_deuda(gimnasio):
    """Si la puerta de este gimnasio rechaza al que debe la cuota.

    Lee la configuración SIN crearla (`obtener_configuracion` hace
    `get_or_create`): el portal corre en cada request del alumno y no tiene
    por qué escribirle una fila a un gimnasio que nunca tocó la puerta. Sin
    fila vale el default del modelo, que es no rechazar.
    """
    return bool(
        ConfiguracionIngreso.objects.filter(gimnasio=gimnasio)
        .values_list("bloquea_por_deuda", flat=True)
        .first()
    )


def hay_lector_de_qr(gimnasio):
    """Si el gimnasio tiene algún lector activo que lea QR. Sin eso, decirle al
    alumno «pedile tu código al gimnasio» lo manda a pedir algo que no
    existe."""
    return (
        Dispositivo.objects.for_gimnasio(gimnasio)
        .filter(activo=True)
        .exclude(tipo=Dispositivo.Tipo.FACIAL)
        .exists()
    )


def tarjeta_del_portal(alumno, gimnasio, *, bloqueo):
    """El contexto de la tarjeta «Tu código para entrar» del portal.

    Costo fijo, porque corre en cada request del alumno: una query por la
    credencial, más UNA de dos según el caso -- si no tiene código, si hay
    lector de QR (para decidir si la tarjeta aparece); si lo tiene vigente y
    está bloqueado por deuda, si la puerta rechaza por deuda.

    `codigo_ingreso` es `None` cuando la tarjeta no se muestra: un gimnasio que
    no usa la puerta no le tiene que hablar de códigos a nadie.
    """
    codigo = codigo_de(alumno, qr_de(alumno))
    if codigo.estado == Estado.SIN_CODIGO and not hay_lector_de_qr(gimnasio):
        return {"codigo_ingreso": None, "aviso_deuda_en_puerta": False}
    aviso = bool(bloqueo) and codigo.vigente and la_puerta_rechaza_por_deuda(gimnasio)
    return {
        "codigo_ingreso": codigo,
        "aviso_deuda_en_puerta": aviso,
        "estados_codigo": Estado,
    }


def inicio_del_dia(dia):
    """Medianoche LOCAL de `dia`, con zona. Filtrar por `momento__date` o
    comparar contra medianoche UTC corre el corte tres horas: un ingreso a las
    22:30 de Buenos Aires ya es el día siguiente en UTC."""
    return timezone.make_aware(datetime.combine(dia, time.min))


def resumen_para_el_panel(gimnasio):
    """La tarjeta «Control de ingreso» del panel del staff.

    **Dos queries, siempre**, porque corre en cada carga del panel: un
    `aggregate` sobre los lectores activos (cuántos hay y cuántos están
    callados) y otro sobre los eventos de hoy (ingresos y rechazos en la misma
    pasada). Nada por fila, nada que crezca con el historial: el rango de
    `momento` lo cubre `ingreso_listado_idx` (`gimnasio, -momento`).

    - «Hoy» es el día LOCAL, cortado en la medianoche de Buenos Aires.
    - Un ingreso es un PERMITIDO de ENTRADA: la salida no es alguien que vino.
      Los rechazos son todos los no permitidos, igual que el filtro
      `rechazados` de `ingreso:listado`, al que lleva el link.
    - `usa_la_puerta` es falso sin ningún lector activo ni movimiento hoy: ahí
      la tarjeta no muestra «0 ingresos» sino que invita a configurar la
      puerta (spec §9). El movimiento cuenta aparte para que desactivar un
      lector a la tarde no esconda lo que entró a la mañana.
    """
    ahora = timezone.now()
    hoy = timezone.localdate(ahora)
    lectores = (
        Dispositivo.objects.for_gimnasio(gimnasio)
        .filter(activo=True)
        .aggregate(
            activos=Count("pk"),
            sin_contacto=Count(
                "pk",
                filter=Q(ultimo_contacto__isnull=True)
                | Q(ultimo_contacto__lt=ahora - UMBRAL_LECTOR_SIN_CONTACTO),
            ),
        )
    )
    eventos = (
        EventoIngreso.objects.for_gimnasio(gimnasio)
        .filter(
            momento__gte=inicio_del_dia(hoy),
            momento__lt=inicio_del_dia(hoy + timedelta(days=1)),
        )
        .aggregate(
            total=Count("pk"),
            ingresos=Count(
                "pk",
                filter=Q(
                    resultado=EventoIngreso.Resultado.PERMITIDO,
                    sentido=EventoIngreso.Sentido.ENTRADA,
                ),
            ),
            rechazos=Count(
                "pk", filter=~Q(resultado=EventoIngreso.Resultado.PERMITIDO)
            ),
        )
    )
    return {
        "usa_la_puerta": bool(lectores["activos"] or eventos["total"]),
        "hoy": hoy,
        "ingresos_hoy": eventos["ingresos"],
        "rechazos_hoy": eventos["rechazos"],
        "lectores_activos": lectores["activos"],
        "lectores_sin_contacto": lectores["sin_contacto"],
    }


def fuera_del_boost(request):
    """Una pantalla que dibuja el QR tiene que ser un DOCUMENTO propio, nunca
    un `<body>` cambiado por htmx adentro de otro.

    Con el `hx-boost` global, una navegación boosteada trae la respuesta por
    XHR y la mete en el documento de la página anterior, que no lleva
    `no-store`: al salir con una navegación completa («Salir»), ese documento
    queda en el bfcache con el QR en el DOM, y el «atrás» del próximo que
    agarre el celular lo muestra. El `no-store` de la respuesta no alcanza,
    porque es el documento de afuera el que se guarda.

    Los links que llevan acá van con `hx-boost="false"`; esto es la red para
    lo que no pasa por esos links (el redirect después de un POST boosteado,
    o un link que alguien agregue mañana): ante un GET boosteado responde
    `HX-Redirect`, y htmx hace `window.location = url`, una navegación
    completa que carga la pantalla como documento propio. Devuelve `None` si
    no hace falta.
    """
    if request.method == "GET" and request.headers.get("HX-Boosted") == "true":
        respuesta = HttpResponse()
        respuesta["HX-Redirect"] = request.get_full_path()
        return respuesta
    return None
