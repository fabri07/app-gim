"""Vistas del control de ingreso.

Las de la API (`/ingreso/v1/...`) son las que llama el lector de la puerta:
sin sesión, autenticadas por token (`ingreso/auth.py`). Las vistas no
escriben: el registro vive en `ingreso/services.py`.
"""

import json
from datetime import date, timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.http import quote_etag
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import (
    CreateView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from alumnos.models import Alumno
from core.mixins import TenantScopedMixin
from ingreso import auth, limites, presentacion, services, tarjetas
from ingreso.decision import Lectura
from ingreso.forms import (
    ERROR_NOMBRE_REPETIDO,
    ConfiguracionIngresoForm,
    CredencialFacialForm,
    DispositivoForm,
    error_valor_repetido,
)
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from pagos import acceso
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin

#: Cuántas tarjetas entran en un PDF (30 = 3 hojas A4).
#:
#: Chica a propósito, y el motivo no es el timeout de 30 s sino la PUERTA:
#: gunicorn corre con UN solo worker síncrono, compartido con la API de los
#: lectores (`/ingreso/v1/`). Mientras se arma un PDF, cada lectura online de
#: CUALQUIER gimnasio de la plataforma queda en cola, y un molinete espera su
#: veredicto un par de segundos antes de rendirse. Medido en una Mac
#: (Python 3.14, 2026-09-28): ~33 ms por tarjeta, casi todo en `segno`
#: eligiendo la máscara del QR y escribiendo el PNG; 300 tarjetas ≈ 10 s,
#: 100 ≈ 3,4 s, 30 ≈ 0,9 s. En Render es probablemente el doble o más (sin
#: medir, ver `ISSUES.md` [2026-09-28]). Es la misma familia de riesgo que el
#: costo del 304 del padrón (spec, riesgos aceptados); la salida de fondo es
#: más de un worker o una cola, no achicar esto más.
TARJETAS_POR_TANDA = 30

LARGO_ID_EVENTO_EQUIPO = EventoIngreso._meta.get_field("id_evento_equipo").max_length


class LecturaInvalida(ValueError):
    """Un evento que no se puede leer. No tumba el lote: ocupa su posición en
    la respuesta con `services.respuesta_invalida` y el resto sigue."""


class CuerpoInvalido(ValueError):
    """El request entero no tiene la forma del contrato: 400."""


def _momento_leido(crudo):
    """El `momento` que manda el lector, ya con zona. Solo el FORMATO: el
    rango lo mira quien llama, porque depende del modo."""
    if not isinstance(crudo, str):
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    texto = crudo.strip()
    # `parse_datetime` acepta "2026-09-28" (medianoche) desde Python 3.11: una
    # fecha sin hora ubicaría el ingreso a las 00:00 y ensuciaría el
    # antipassback y los reportes del día.
    if "T" not in texto and " " not in texto:
        raise LecturaInvalida(f"momento sin hora: {crudo!r}")
    try:
        momento = parse_datetime(texto)
    except ValueError:  # bien formado pero imposible: mes 13
        momento = None
    if momento is None:
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    # Un lector puede mandar la fecha sin zona. Comparar un naive contra un
    # aware explota, así que se le pone la local -- que es la del gimnasio, no
    # UTC.
    if timezone.is_naive(momento):
        momento = timezone.make_aware(momento)
    return momento


def _duracion(delta):
    minutos = round(abs(delta).total_seconds() / 60)
    dias, minutos = divmod(minutos, 60 * 24)
    horas, minutos = divmod(minutos, 60)
    if dias:
        return f"{dias} d {horas} h"
    if horas:
        return f"{horas} h {minutos} min"
    return f"{minutos} min"


def _nota_de_reloj(crudo, ahora):
    """En online el `momento` del lector no decide nada, pero si está mal es
    un reloj a corregir: queda anotado en `detalle` para el instalador."""
    if crudo is None:
        return ""
    try:
        momento = _momento_leido(crudo)
    except LecturaInvalida as exc:
        return f"reloj del lector: {exc}"
    desfase = momento - ahora
    if abs(desfase.total_seconds()) <= services.SEGUNDOS_DE_DESFASE_A_ANOTAR:
        return ""
    sentido = "adelantado" if desfase.total_seconds() > 0 else "atrasado"
    return f"reloj del lector {sentido} {_duracion(desfase)}"


def _momento_en_diferido(crudo, *, piso, tope):
    """En diferido el `momento` es LO que se registra (el equipo ya abrió):
    obligatorio y dentro del rango creíble."""
    if crudo is None:
        raise LecturaInvalida(
            "en diferido el momento es obligatorio: es la hora en que el "
            "equipo abrió"
        )
    momento = _momento_leido(crudo)
    # Con el reloj adelantado ensuciaría el antipassback y los reportes para
    # siempre; con el reloj reseteado, lo mismo hacia atrás.
    if momento > tope:
        raise LecturaInvalida(
            f"momento en el futuro ({crudo}): revisá el reloj del lector"
        )
    if momento < piso:
        raise LecturaInvalida(
            f"momento demasiado viejo ({crudo}, más de "
            f"{services.DIAS_DE_ANTIGUEDAD_TOLERADOS} días): revisá el reloj "
            "del lector"
        )
    return momento


def _id_recibido(evento):
    """El `id_evento_equipo` tal como vino, para devolverlo en la respuesta de
    un evento inválido (o `None`)."""
    if not isinstance(evento, dict):
        return None
    crudo = evento.get("id_evento_equipo")
    if isinstance(crudo, bool) or not isinstance(crudo, (str, int)):
        return None
    return crudo


def _lectura_de(evento, *, ahora, piso, tope):
    if not isinstance(evento, dict):
        raise LecturaInvalida("cada evento tiene que ser un objeto")
    tipo = evento.get("tipo")
    if tipo not in Credencial.Tipo.values:
        raise LecturaInvalida(
            f"tipo desconocido: {tipo!r} (se espera "
            f"{' o '.join(Credencial.Tipo.values)})"
        )
    valor = evento.get("valor")
    if not isinstance(valor, str):
        raise LecturaInvalida("falta «valor», o no es texto")
    sentido = evento.get("sentido", EventoIngreso.Sentido.ENTRADA)
    if sentido not in EventoIngreso.Sentido.values:
        raise LecturaInvalida(f"sentido desconocido: {sentido!r}")

    id_equipo = evento.get("id_evento_equipo")
    if id_equipo is None:
        id_equipo = ""
    elif isinstance(id_equipo, bool) or not isinstance(id_equipo, (str, int)):
        raise LecturaInvalida("id_evento_equipo tiene que ser texto o número")
    id_equipo = str(id_equipo).strip()
    # SQLite no valida el largo de un `varchar` y Postgres sí: sin esto, un
    # `DataError` en el `bulk_create` que la suite no ve.
    if len(id_equipo) > LARGO_ID_EVENTO_EQUIPO:
        raise LecturaInvalida(
            f"id_evento_equipo de más de {LARGO_ID_EVENTO_EQUIPO} caracteres"
        )

    # Un string "false" es verdadero en Python: aceptar cualquier cosa y
    # pasarla por `bool()` mandaría a diferido un evento online.
    en_diferido = evento.get("abierto_por_el_equipo", False)
    if en_diferido is None:
        en_diferido = False
    if not isinstance(en_diferido, bool):
        raise LecturaInvalida(
            f"abierto_por_el_equipo tiene que ser true o false, no {en_diferido!r}"
        )

    crudo = evento.get("momento")
    if en_diferido:
        # La idempotencia del backlog depende del id: sin él, el reintento de
        # un upload que se cortó registraría el mismo ingreso dos veces.
        if not id_equipo:
            raise LecturaInvalida(
                "en diferido id_evento_equipo es obligatorio: sin él, "
                "reintentar el upload duplicaría el ingreso"
            )
        momento = _momento_en_diferido(crudo, piso=piso, tope=tope)
        nota = ""
    else:
        # **En online manda el reloj del servidor.** El equipo está esperando
        # el veredicto para abrir, así que el ingreso ES ahora; el `momento`
        # que manda solo puede equivocarse. Un lector con la hora en UTC (tres
        # horas adelantado) o con el reloj reseteado dejaba a todos afuera, o
        # medía la reserva y el antipassback contra otro día.
        momento = ahora
        nota = _nota_de_reloj(crudo, ahora)

    return Lectura(
        tipo=tipo, valor=valor, sentido=sentido, momento=momento,
        id_equipo=id_equipo, en_diferido=en_diferido, nota=nota,
    )


def _parsear(cuerpo):
    """Una entrada por evento, en orden: una `Lectura`, o la respuesta de un
    evento inválido (un dict). Solo un error de FORMA del request entero
    levanta `CuerpoInvalido`; un evento mal armado no tumba a los demás."""
    datos = json.loads(cuerpo)
    if not isinstance(datos, dict) or "eventos" not in datos:
        raise CuerpoInvalido("se espera un objeto con «eventos»")
    eventos = datos["eventos"]
    if not isinstance(eventos, list):
        raise CuerpoInvalido("«eventos» tiene que ser una lista")
    if len(eventos) > services.MAX_EVENTOS_POR_LOTE:
        raise CuerpoInvalido(
            f"máximo {services.MAX_EVENTOS_POR_LOTE} eventos por request"
        )

    ahora = timezone.now()
    tope = ahora + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
    piso = ahora - timedelta(days=services.DIAS_DE_ANTIGUEDAD_TOLERADOS)
    entradas = []
    for evento in eventos:
        try:
            entradas.append(
                _lectura_de(evento, ahora=ahora, piso=piso, tope=tope)
            )
        except LecturaInvalida as exc:
            entradas.append(
                services.respuesta_invalida(str(exc), _id_recibido(evento))
            )
    return entradas


def _demasiado(motivo, error, segundos):
    respuesta = JsonResponse({"error": error, "motivo": motivo}, status=429)
    respuesta["Retry-After"] = str(segundos)
    return respuesta


def _lector_del_request(request):
    """`(dispositivo, None)` si el token es bueno, o `(None, respuesta)` con el
    401/403/429 listo. Compartido por las dos vistas de la API para que no
    puedan divergir en qué es un token válido ni en el freno por IP.

    **El freno por IP (`ingreso/limites.py`) es del camino de FALLO**: con la
    IP frenada, un token que no es de ningún lector activo recibe 429 sin
    tocar la base (el set vive en el cache); uno de un lector activo pasa
    siempre, porque una lectura online frenada es una puerta cerrada. El 403
    no suma fallas: solo lo alcanza un token válido. Y **no se mira `estado_cuenta`** (invariante de la Tarea 9).

    Todo request autenticado es además el latido del lector (no hay endpoint
    aparte): `update()` para no tocar `modificado`.
    """
    ip = limites.ip_de(request)
    token_hash = auth.hash_presentado(request)
    if limites.demasiados_intentos_fallidos(
        ip
    ) and not limites.puede_ser_un_lector(token_hash):
        return None, _demasiado(
            "intentos_fallidos",
            "demasiados intentos con un token inválido desde esta IP: revisá "
            "el token del lector y esperá un minuto",
            limites.SEGUNDOS_VENTANA_FALLOS,
        )
    try:
        dispositivo = auth.dispositivo_autenticado(request, token_hash=token_hash)
    except auth.LectorDesactivado:
        return None, JsonResponse({"error": "lector desactivado"}, status=403)
    except auth.ErrorDeAutenticacion:
        limites.anotar_fallo(ip)
        return None, JsonResponse({"error": "token invalido"}, status=401)
    Dispositivo.objects.filter(pk=dispositivo.pk).update(
        ultimo_contacto=timezone.now()
    )
    return dispositivo, None


def _etag_sin_envoltorio(etag):
    """`W/"abc"`, `"abc"` y `abc` son la misma versión. Cloudflare debilita
    (`W/`) el ETag de una respuesta que comprime, y un puente simple puede
    guardar la `version` del cuerpo y mandarla pelada."""
    if etag.startswith("W/"):
        etag = etag[2:]
    return etag.strip('"')


@method_decorator(csrf_exempt, name="dispatch")
class EventosApiView(View):
    """Lo que el molinete llama en cada lectura, y lo que le sube el lote
    cuando estuvo sin internet.

    `csrf_exempt` es el primero del proyecto y está acotado a la API: CSRF
    protege una sesión de navegador de ser montada por otro sitio, y acá no hay
    sesión ni cookie -- se autentica con un header que un form cross-site no
    puede setear. La vista **ignora `request.user` por completo**, y hay un test
    que lo fija.
    """

    http_method_names = ["post"]

    def post(self, request):
        dispositivo, rechazo = _lector_del_request(request)
        if rechazo is not None:
            return rechazo
        # Solo acá y no en `_lector_del_request`: el padrón no escribe, y
        # cortarle el poll a un lector hace que la puerta abra hoy y deje de
        # abrir mañana.
        if limites.cupo_diario_excedido(dispositivo):
            return _demasiado(
                "tope_diario",
                f"este lector ya registró {limites.MAX_EVENTOS_POR_DIA} "
                "ingresos hoy, el tope diario; se reinicia a la medianoche. "
                "Revisá que el puente no esté reenviando lecturas en bucle",
                limites.segundos_hasta_medianoche(),
            )

        try:
            entradas = _parsear(request.body)
        except RecursionError:
            # Un JSON anidado miles de niveles hace que `json.loads` agote la
            # pila: es un cuerpo inválido, no un 500.
            return JsonResponse({"error": "JSON demasiado anidado"}, status=400)
        except (KeyError, ValueError, TypeError) as exc:
            # `json.JSONDecodeError` y `UnicodeDecodeError` son `ValueError`.
            return JsonResponse(
                {"error": str(exc) or "cuerpo invalido"}, status=400
            )

        lecturas = [e for e in entradas if isinstance(e, Lectura)]
        decididas = iter(services.registrar_lote(dispositivo, lecturas))
        # Cada respuesta en la posición de su evento: el lector las
        # correlaciona por posición o por id.
        resultados = [
            next(decididas) if isinstance(e, Lectura) else e for e in entradas
        ]
        return JsonResponse({"resultados": resultados})


class PadronApiView(View):
    """El padrón que el lector guarda para abrir sin internet, y que el
    puente usa para dar de alta y de baja usuarios en un equipo facial.

    Sin `csrf_exempt`: es un GET, CSRF no lo mira. Igual que eventos, ignora
    `request.user` y no toca la sesión, así que el middleware de plataforma no
    le corta el paso ni le cuelga una cookie a un gimnasio con la cuenta
    suspendida: la puerta sigue abriendo aunque el gimnasio no pague la app.

    **`no-store` en el 200 y en el 304.** El sitio va proxeado por Cloudflare
    y esto son las llaves de la puerta: un 200 cacheado en el edge (una regla
    de caché de la zona alcanza) se lo serviría a cualquiera que pida la URL,
    con o sin token. No impide el `ETag`/304: el que guarda la copia es el
    lector, no un intermediario.
    """

    http_method_names = ["get"]

    def get(self, request):
        dispositivo, rechazo = _lector_del_request(request)
        if rechazo is not None:
            return rechazo

        padron = services.padron_de(dispositivo.gimnasio)
        etag = quote_etag(padron["version"])
        # A mano y no con `django.utils.http.parse_etags`, que descarta sin
        # avisar una versión mandada sin comillas.
        pedidos = {
            _etag_sin_envoltorio(e.strip())
            for e in request.META.get("HTTP_IF_NONE_MATCH", "").split(",")
        }
        if "*" in pedidos or padron["version"] in pedidos:
            respuesta = HttpResponseNotModified()
        else:
            respuesta = JsonResponse(padron)
        respuesta["ETag"] = etag
        add_never_cache_headers(respuesta)
        return respuesta


# ---------------------------------------------------------------------------
# Pantallas de staff
#
# A diferencia de la API, éstas SÍ pasan por `PlataformaMiddleware`: `ingreso`
# no está en `APPS_SIN_BLOQUEO`, así que con la cuenta congelada el dueño ve el
# cartel igual que en el resto del panel mientras la puerta sigue abriendo
# (`tests_vistas.py::PantallasConLaCuentaSuspendidaTests`). No se agrega
# ningún ítem al nav: se entra desde «Mi gimnasio» y desde el dashboard.
# ---------------------------------------------------------------------------


def _fecha_o_nada(texto):
    """Una fecha `AAAA-MM-DD` o `None`: un filtro mal tipeado se ignora en vez
    de dar 500 (mismo criterio que `pagos/views.py`)."""
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


# Vive en `presentacion` porque la comparte la tarjeta del panel: el listado
# y el «hoy» del panel tienen que cortar el día en el mismo lugar.
_inicio_del_dia = presentacion.inicio_del_dia


class EventoIngresoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Quién pasó por la puerta, y quién intentó y no pudo.

    **Costo fijo**: `select_related` del alumno y del lector (sin eso, cada
    fila son dos queries) más una para el desplegable de alumnos y las dos de
    la paginación. `ListView` pagina con `COUNT` + `LIMIT`, así que la tabla
    puede crecer sin techo sin que la pantalla lo pague.
    """

    model = EventoIngreso
    template_name = "ingreso/evento_list.html"
    context_object_name = "eventos"
    paginate_by = 50

    FILTRO_RECHAZADOS = "rechazados"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("alumno", "dispositivo")
        self.desde = self.request.GET.get("desde", "")
        self.hasta = self.request.GET.get("hasta", "")
        self.resultado = self.request.GET.get("resultado", "")
        self.alumno_id = self.request.GET.get("alumno", "")

        if (desde := _fecha_o_nada(self.desde)) is not None:
            queryset = queryset.filter(momento__gte=_inicio_del_dia(desde))
        if (hasta := _fecha_o_nada(self.hasta)) is not None:
            try:
                limite = _inicio_del_dia(hasta + timedelta(days=1))
            except OverflowError:
                # `?hasta=9999-12-31`: no hay día siguiente que representar,
                # y «hasta el fin de los tiempos» es lo mismo que sin tope.
                limite = None
            if limite is not None:
                queryset = queryset.filter(momento__lt=limite)
        if self.resultado == self.FILTRO_RECHAZADOS:
            queryset = queryset.exclude(resultado=EventoIngreso.Resultado.PERMITIDO)
        elif self.resultado in EventoIngreso.Resultado.values:
            queryset = queryset.filter(resultado=self.resultado)
        if self.alumno_id.isdigit():
            # Sobre el queryset YA acotado al gimnasio: el pk de un alumno
            # ajeno filtra a cero filas, no muestra las suyas.
            queryset = queryset.filter(alumno_id=int(self.alumno_id))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            desde_actual=self.desde,
            hasta_actual=self.hasta,
            resultado_actual=self.resultado,
            alumno_actual=self.alumno_id,
            resultados=EventoIngreso.Resultado.choices,
            filtro_rechazados=self.FILTRO_RECHAZADOS,
            # Una query, sin traer más que lo que muestra el desplegable.
            alumnos=Alumno.objects.for_gimnasio(self.gimnasio)
            .order_by("apellido", "nombre")
            .only("pk", "nombre", "apellido"),
            hay_filtros=any(
                (self.desde, self.hasta, self.resultado, self.alumno_id)
            ),
        )
        return context


class DispositivoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Los lectores del gimnasio, activos e inactivos: los inactivos se listan
    para poder reactivarlos. Sin `DeleteView`: se desactiva destildando
    `activo`, y el `PROTECT` de los eventos lo respalda."""

    model = Dispositivo
    template_name = "ingreso/dispositivo_list.html"
    context_object_name = "dispositivos"


def _render_token(request, dispositivo, token, modo):
    """La pantalla de «esto se ve una sola vez», compartida por el alta y la
    rotación. Mismo patrón que `alumnos/views.py::_render_credenciales`: un
    200 que NO redirige (el token no puede viajar por `messages`, que se
    serializa en la sesión, que vive en la base). `never_cache` ya está sobre
    el `dispatch` de las dos vistas; se repite acá para que ninguna salida que
    muestre un token pueda olvidarlo."""
    respuesta = render(
        request,
        "ingreso/dispositivo_token.html",
        {"dispositivo": dispositivo, "token": token, "modo": modo},
    )
    add_never_cache_headers(respuesta)
    return respuesta


@method_decorator(never_cache, name="dispatch")
class DispositivoCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    """El POST exitoso responde el token en un 200: es la única vez que se
    puede leer. El F5 sobre ese POST crea... nada: el nombre ya está tomado y
    el formulario lo dice."""

    model = Dispositivo
    form_class = DispositivoForm
    template_name = "ingreso/dispositivo_form.html"

    def form_valid(self, form):
        try:
            dispositivo, token = services.crear_dispositivo(
                self.gimnasio,
                nombre=form.cleaned_data["nombre"],
                tipo=form.cleaned_data["tipo"],
            )
        except IntegrityError:
            # La carrera del doble submit (el form va boosteado por htmx): el
            # `clean_nombre` no la ve y la base sí.
            form.add_error("nombre", ERROR_NOMBRE_REPETIDO)
            return self.form_invalid(form)
        return _render_token(self.request, dispositivo, token, "alta")


@method_decorator(never_cache, name="dispatch")
class DispositivoUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """Nombre, tipo y `activo`. El token NO se muestra acá nunca: la base solo
    tiene el hash. Para uno nuevo está «Generar un token nuevo»
    (`DispositivoRotarTokenView`)."""

    model = Dispositivo
    form_class = DispositivoForm
    template_name = "ingreso/dispositivo_form.html"
    success_url = reverse_lazy("ingreso:dispositivos")

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save()
        except IntegrityError:
            form.add_error("nombre", ERROR_NOMBRE_REPETIDO)
            return self.form_invalid(form)
        # Desactivar o reactivar cambia quién está en el set del freno por IP.
        limites.olvidar_lectores_activos()
        messages.success(self.request, f"Lector «{self.object.nombre}» actualizado.")
        return redirect(self.get_success_url())


@method_decorator(never_cache, name="dispatch")
class DispositivoRotarTokenView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """GET muestra la confirmación y SOLO el POST rota: la llave nunca cambia
    con un GET (un link o un prefetch del navegador la rotarían sin querer).

    Con confirmación, mismo espíritu que las pantallas de borrar: rotar deja
    la puerta sin abrir hasta que alguien vaya al equipo a cargar la clave
    nueva, y eso hay que leerlo antes de apretar."""

    model = Dispositivo
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "ingreso/dispositivo_rotar_confirmar.html",
            {"dispositivo": self.get_object()},
        )

    def post(self, request, *args, **kwargs):
        dispositivo = self.get_object()
        token = services.rotar_token(dispositivo)
        return _render_token(request, dispositivo, token, "rotacion")


class ConfiguracionIngresoView(StaffRequiredMixin, UpdateView):
    """La fila única de la puerta del gimnasio. Molde de
    `turnos/views.py::ConfiguracionTurnosView`: sin pk en la URL y sin
    `TenantScopedMixin`, porque `get_object` solo puede resolver la del propio
    gimnasio."""

    model = ConfiguracionIngreso
    form_class = ConfiguracionIngresoForm
    template_name = "ingreso/configuracion_form.html"
    success_url = reverse_lazy("ingreso:configuracion")

    @property
    def gimnasio(self):
        return self.request.user.perfil.gimnasio

    def get_object(self, queryset=None):
        return obtener_configuracion(self.gimnasio)

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Configuración de la puerta guardada.")
        return respuesta

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gimnasio = self.gimnasio
        # `pagos/acceso.py::_umbral` hace fail-open si falta cualquiera de las
        # dos: prender el bloqueo sin tolerancia es un interruptor muerto, sin
        # ningún síntoma. Se mira lo GUARDADO, no lo tildado en el POST.
        context["bloqueo_sin_efecto"] = self.object.bloquea_por_deuda and (
            gimnasio.dias_tolerancia_pago is None
            or gimnasio.fecha_activacion_bloqueo is None
        )
        return context


class _AlumnoDelGimnasioMixin(StaffRequiredMixin, TenantScopedMixin):
    """Resuelve el alumno de la URL acotado al gimnasio: el de otro da 404."""

    def get_alumno(self):
        return get_object_or_404(
            Alumno.objects.for_gimnasio(self.gimnasio), pk=self.kwargs["alumno_pk"]
        )

    def _a_la_ficha(self, alumno):
        return redirect("alumnos:detalle", pk=alumno.pk)


class CredencialQrView(_AlumnoDelGimnasioMixin, View):
    """«Generar código» y «Regenerar» desde la ficha. El valor no viaja por
    `messages`: la ficha lo dibuja como QR.

    El GET es la confirmación de REGENERAR, y solo el POST escribe (mismo
    criterio que `DispositivoRotarTokenView`). Regenerar deja sin abrir la
    tarjeta impresa del alumno y el código que tiene guardado en el celular,
    y no tiene vuelta atrás: eso se lee antes de apretar. «Generar código»
    (la primera vez) no pide confirmación porque no le quita nada a nadie:
    es un POST directo desde la ficha."""

    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        alumno = self.get_alumno()
        credencial = presentacion.qr_de(alumno)
        if credencial is None:
            # Nada que regenerar: el botón de la ficha es «Generar código».
            return self._a_la_ficha(alumno)
        return render(
            request,
            "ingreso/credencial_qr_regenerar_confirmar.html",
            {"alumno": alumno, "credencial": credencial},
        )

    def post(self, request, *args, **kwargs):
        alumno = self.get_alumno()
        regenerar = request.POST.get("regenerar") == "1"
        if not regenerar:
            actual = alumno.credenciales.filter(tipo=Credencial.Tipo.QR).first()
            if actual is not None and not actual.activa:
                # NO se reactiva el mismo valor: la baja pudo ser por una
                # tarjeta robada, y reactivarlo le devolvería la puerta a quien
                # la tiene. Un QR vuelve solo con un valor nuevo. (Un id facial
                # sí se reactiva volviéndolo a cargar: el equipo es el que
                # reconoce la cara, no hay nada al portador que se pueda robar.)
                return render(
                    request,
                    "ingreso/credencial_qr_dada_de_baja.html",
                    {"alumno": alumno},
                )
        services.generar_credencial_qr(alumno, regenerar=regenerar)
        if regenerar:
            messages.success(
                request,
                "Código nuevo generado. El anterior ya no abre la puerta: si "
                "estaba impreso, hay que imprimir la tarjeta de nuevo.",
            )
        else:
            messages.success(request, "Código de ingreso listo.")
        return self._a_la_ficha(alumno)


class CredencialesEnBloqueView(StaffRequiredMixin, TenantScopedMixin, View):
    """«Generar códigos para los alumnos activos». Un solo `bulk_create`
    (`services.generar_credenciales_en_bloque`) y saltea a los que ya tienen:
    apretarlo dos veces no cambia ningún código."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        creadas = services.generar_credenciales_en_bloque(self.gimnasio)
        if creadas:
            messages.success(
                request, f"Se generaron {creadas} códigos de ingreso nuevos."
            )
        else:
            messages.info(request, "Todos los alumnos activos ya tenían su código.")
        return redirect("alumnos:listado")


class CredencialFacialView(_AlumnoDelGimnasioMixin, FormView):
    """El número de usuario que el alumno tiene EN el lector facial. La app no
    ve ni guarda la cara: eso vive en el equipo."""

    form_class = CredencialFacialForm
    template_name = "ingreso/credencial_facial_form.html"

    def get_form_kwargs(self):
        # Form plano, no `TenantScopedModelForm`: el aislamiento lo da el
        # alumno ya resuelto contra el gimnasio (y de él sale el gimnasio que
        # usa `clean_valor`), no un `gimnasio` suelto.
        kwargs = super().get_form_kwargs()
        kwargs.pop("gimnasio", None)
        kwargs["alumno"] = self.alumno
        return kwargs

    def get(self, request, *args, **kwargs):
        self.alumno = self.get_alumno()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.alumno = self.get_alumno()
        return super().post(request, *args, **kwargs)

    def get_initial(self):
        actual = self.alumno.credenciales.filter(tipo=Credencial.Tipo.FACIAL).first()
        return {"valor": actual.valor} if actual else {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["alumno"] = self.alumno
        return context

    def form_valid(self, form):
        valor = form.cleaned_data["valor"]
        try:
            services.guardar_credencial_facial(self.alumno, valor)
        except IntegrityError:
            # Se vuelve a mirar QUÉ chocó. Si otro alumno tiene ese número, es
            # la carrera del `clean_valor`: error de campo. Si no, chocó la
            # unicidad (gimnasio, alumno, tipo): un doble submit del primer
            # alta de ESTE alumno, que el otro request ya guardó -- decirle
            # «ese número ya lo tiene otro» sería un error falso.
            otro = form.otro_alumno_con(valor)
            if otro is not None:
                form.add_error("valor", error_valor_repetido(otro))
                return self.form_invalid(form)
        messages.success(self.request, "Número del lector facial guardado.")
        return self._a_la_ficha(self.alumno)


class CredencialBajaView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Da de baja una credencial (QR o facial) sin borrarla.

    El GET es la confirmación y solo el POST escribe: un QR dado de baja no se
    reactiva nunca (la baja pudo ser por una tarjeta robada), así que el
    alumno necesita un código nuevo y una tarjeta nueva."""

    model = Credencial
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        credencial = self.get_object()
        return render(
            request,
            "ingreso/credencial_baja_confirmar.html",
            {"credencial": credencial, "alumno": credencial.alumno},
        )

    def post(self, request, *args, **kwargs):
        credencial = self.get_object()
        services.dar_de_baja_credencial(credencial)
        messages.success(request, "Credencial dada de baja: ya no abre la puerta.")
        return redirect("alumnos:detalle", pk=credencial.alumno_id)


@method_decorator(never_cache, name="dispatch")
class MiCodigoView(AlumnoRequiredMixin, TemplateView):
    """«Tu código para entrar»: el QR del alumno, grande, para mostrarlo en el
    lector de la puerta.

    - **Sin pk en la URL**: el alumno es el de la sesión, así que no hay forma
      de pedir el de otro.
    - **`never_cache` sobre el `dispatch` entero** (`no-store, private`) y
      `hx-history="false"` en el template: el QR es una llave al portador y no
      puede quedar ni en el caché del navegador ni en el historial de htmx,
      que guarda el `<body>` en `localStorage`.
    - **Bloqueado por deuda lo ve igual**: la puerta decide. Si ESTE gimnasio
      rechaza por deuda en la puerta, se le avisa, para que no crea que el
      código anda mal.
    - **Suplantación: el staff SÍ lo ve.** Ya lo ve y lo regenera desde la
      ficha, así que ocultarlo no protege nada, y le sacaría justo lo que vino
      a revisar cuando el alumno dice «no me aparece el código».
    - Con la cuenta del gimnasio congelada, `PlataformaMiddleware` corta esta
      pantalla como todo el portal: `ingreso` no está en su allowlist.

    Una sola pantalla del portal dibuja el QR, y no es el inicio: así el inicio
    sigue entrando en el historial de htmx y la llave vive en un solo lugar.
    """

    template_name = "ingreso/mi_codigo.html"

    def get(self, request, *args, **kwargs):
        # Documento propio, nunca un swap de htmx: ver
        # `presentacion.fuera_del_boost`.
        return presentacion.fuera_del_boost(request) or super().get(
            request, *args, **kwargs
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        alumno = self.alumno
        codigo = presentacion.codigo_de(alumno, presentacion.qr_de(alumno)) if alumno else None
        aviso = False
        if codigo is not None and codigo.vigente:
            # Primero la configuración (una lectura, sin crearla) y recién si
            # la puerta rechaza por deuda, la regla de `pagos/acceso.py`.
            aviso = presentacion.la_puerta_rechaza_por_deuda(self.gimnasio) and bool(
                acceso.bloqueo_de(alumno, hoy=timezone.localdate())
            )
        context.update(
            gimnasio=self.gimnasio,
            codigo=codigo,
            aviso_deuda_en_puerta=aviso,
            estados=presentacion.Estado,
        )
        return context


# ---------------------------------------------------------------------------
# Tarjetas imprimibles
# ---------------------------------------------------------------------------


def _credenciales_para_imprimir(gimnasio):
    """Las credenciales QR que abren la puerta: activas, de alumnos ACTIVOS,
    de ESTE gimnasio. Mismo criterio que `presentacion.codigo_de` (el QR se
    dibuja solo si abre): una tarjeta impresa de un código dado de baja o de un
    alumno inactivo se lee como «esto funciona» y la puerta la rechaza.

    Una query: el alumno viene en el mismo JOIN (`nombre_corto` lo lee) y el
    orden es estable, porque las tandas se cortan sobre él."""
    return (
        Credencial.objects.for_gimnasio(gimnasio)
        .filter(
            tipo=Credencial.Tipo.QR,
            activa=True,
            alumno__estado=Alumno.Estado.ACTIVO,
        )
        .select_related("alumno")
        .order_by("alumno__apellido", "alumno__nombre", "pk")
    )


def _respuesta_pdf(gimnasio, credenciales, nombre_archivo):
    """El PDF como descarga privada. La vista lleva `never_cache` sobre el
    `dispatch` entero (`no-store, private`): es un lote de llaves de la
    puerta y no puede quedar en ningún caché, ni del navegador ni de
    Cloudflare. El nombre del archivo no lleva datos del alumno: queda en la
    carpeta de Descargas de la computadora del mostrador."""
    pdf = tarjetas.generar_pdf_credenciales(
        gimnasio.nombre,
        [
            tarjetas.Tarjeta(
                nombre=services.nombre_corto(credencial.alumno),
                valor=credencial.valor,
            )
            for credencial in credenciales
        ],
    )
    respuesta = HttpResponse(pdf, content_type="application/pdf")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return respuesta


@method_decorator(never_cache, name="dispatch")
class TarjetaAlumnoPdfView(_AlumnoDelGimnasioMixin, View):
    """La tarjeta de UN alumno, desde su ficha.

    Solo POST, desde un form con `hx-boost="false"` (htmx se traga la
    descarga, mismo criterio que `ExportarDatosView`): un GET se prefetchea,
    queda en el historial y se comparte como link. Un alumno sin un código
    que abra la puerta no da un PDF vacío: vuelve a la ficha con el motivo.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_alumno()
        credencial = presentacion.qr_de(alumno)
        if not presentacion.codigo_de(alumno, credencial).vigente:
            messages.warning(
                request,
                f"{alumno.nombre} no tiene un código que abra la puerta: "
                "generale uno antes de imprimir la tarjeta.",
            )
            return self._a_la_ficha(alumno)
        return _respuesta_pdf(
            self.gimnasio,
            [credencial],
            f"tarjeta-ingreso-{self.gimnasio.slug}.pdf",
        )


class TarjetasView(StaffRequiredMixin, TenantScopedMixin, TemplateView):
    """«Imprimir tarjetas de ingreso»: un botón por tanda de
    `TARJETAS_POR_TANDA`. No muestra ningún código, solo cuántos hay."""

    template_name = "ingreso/tarjetas.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        total = _credenciales_para_imprimir(self.gimnasio).count()
        tandas = [
            {
                "numero": numero,
                "desde": inicio + 1,
                "hasta": min(inicio + TARJETAS_POR_TANDA, total),
            }
            for numero, inicio in enumerate(
                range(0, total, TARJETAS_POR_TANDA), start=1
            )
        ]
        context.update(total=total, tandas=tandas, por_hoja=tarjetas.TARJETAS_POR_HOJA)
        return context


@method_decorator(never_cache, name="dispatch")
class TarjetasPdfView(StaffRequiredMixin, TenantScopedMixin, View):
    """El PDF de UNA tanda. POST-only por lo mismo que la tarjeta suelta.

    El número de tanda viaja en el POST; uno que no existe (o que dejó de
    existir porque alguien dio de baja códigos entre la pantalla y el click)
    vuelve a la pantalla de tandas en vez de dar un PDF vacío."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            tanda = int(request.POST.get("tanda", ""))
        except ValueError:
            tanda = 0
        inicio = (tanda - 1) * TARJETAS_POR_TANDA
        credenciales = (
            list(
                _credenciales_para_imprimir(self.gimnasio)[
                    inicio : inicio + TARJETAS_POR_TANDA
                ]
            )
            if tanda >= 1
            else []
        )
        if not credenciales:
            messages.warning(
                request,
                "No hay tarjetas para imprimir en esa tanda. Revisá la lista "
                "y volvé a intentar.",
            )
            return redirect("ingreso:tarjetas")
        return _respuesta_pdf(
            self.gimnasio,
            credenciales,
            f"tarjetas-ingreso-{self.gimnasio.slug}-"
            f"{timezone.localdate().isoformat()}-{tanda}.pdf",
        )
