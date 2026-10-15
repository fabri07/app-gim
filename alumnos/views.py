"""Vistas de gestión (Fase 2) de los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El "activar/inactivar" es una acción POST-only
separada (nunca una vista de borrado): un `Alumno` nunca se borra, solo
cambia de estado (queda como historial para pagos y rutinas ya emitidos).

`CrearAccesoView`/`RegenerarPasswordView` reemplazan el magic-link original
del ROADMAP: el staff da de alta el acceso desde la ficha del alumno (ver
ISSUES.md 2026-07-01). El staff elige con qué dato entra el alumno (email o
teléfono) pero NO la contraseña: la genera la app y se muestra una sola vez.
Ninguna de las dos usa los hooks de form de `TenantScopedMixin`
(`get_form_kwargs`/`form_valid` esperan un `ModelForm` con `.instance`;
`CrearAccesoForm` es un `forms.Form` plano) — se maneja el form a mano, como
`AlumnoToggleEstadoView`, y se reutiliza `TenantScopedMixin` solo por
`get_queryset`/`self.gimnasio` (aislamiento de tenant vía `SingleObjectMixin.
get_object`).
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.cache import add_never_cache_headers
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from tenants.mixins import StaffRequiredMixin
from alumnos import identidad, lote
from alumnos import services as servicios
from alumnos.forms import AlumnoForm, CrearAccesoForm, LoteForm
from alumnos.models import Alumno


class AlumnoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Alumno
    template_name = "alumnos/alumno_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        queryset = super().get_queryset()
        self.estado_actual = self.request.GET.get("estado", "")
        if self.estado_actual in (Alumno.Estado.ACTIVO, Alumno.Estado.INACTIVO):
            queryset = queryset.filter(estado=self.estado_actual)
        self.q = self.request.GET.get("q", "").strip()
        # Cada palabra tiene que aparecer en alguno de los cuatro campos (AND
        # entre palabras, OR entre campos): "juan pérez" encuentra a Pérez,
        # Juan sin importar el orden.
        for palabra in self.q.split():
            queryset = queryset.filter(
                Q(nombre__icontains=palabra)
                | Q(apellido__icontains=palabra)
                | Q(email__icontains=palabra)
                | Q(telefono__icontains=palabra)
            )
        self.sin_fecha = self.request.GET.get("sin_fecha") == "1"
        if self.sin_fecha:
            queryset = queryset.filter(fecha_inicio_ciclo__isnull=True)
        return queryset

    def get_context_data(self, **kwargs):
        from rutinas.models import RutinaAsignada

        context = super().get_context_data(**kwargs)
        context["estado_actual"] = self.estado_actual
        context["q_actual"] = self.q
        context["sin_fecha"] = self.sin_fecha
        # Se cuenta sobre TODO el gimnasio, no sobre lo filtrado: el aviso
        # habla del padrón, y no tiene que cambiar al buscar.
        context["activos_sin_inicio"] = (
            Alumno.objects.for_gimnasio(self.gimnasio)
            .filter(estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo__isnull=True)
            .count()
        )
        context["filtros_qs"] = urlencode(
            {
                clave: valor
                for clave in lote.FILTROS_LISTADO
                if (valor := self.request.GET.get(clave, "").strip())
            }
        )
        # Un set de ids y no una consulta por fila: el template marca al
        # alumno con `a.pk in alumnos_por_vencer`, que en Django es una
        # búsqueda en memoria. Con una property en el modelo serían dos
        # queries por alumno del listado.
        context["alumnos_por_vencer"] = {
            rutina.alumno_id
            for rutina in RutinaAsignada.por_vencer_de(gimnasio=self.gimnasio)
        }
        return context


class AlumnoLoteView(StaffRequiredMixin, TenantScopedMixin, View):
    """Acciones en lote del listado: fijar fecha de inicio, dar de baja,
    reactivar y generar códigos de ingreso. Las escrituras viven en
    `alumnos/lote.py`; acá se decide qué mostrar.

    **Ningún error del usuario devuelve un 4xx**: bajo el `hx-boost` global es
    un click mudo. Selección vacía o inválida, ids de otro gimnasio y tope
    excedido vuelven al listado con un mensaje; una fecha inválida re-renderiza
    la confirmación (200) con el error debajo del campo. Un GET vuelve al
    listado.

    **Fijar fecha va en dos pasos, y la fecha se ve ANTES de ejecutar.** El
    POST de la barra (sin `fecha`) muestra la confirmación con hoy por
    defecto, quiénes cambian, quiénes se saltean por tener cuotas y cuántos
    activos nacerían con el acceso pausado con ESA fecha. La pantalla tiene
    dos forms: uno para cambiar la fecha (re-renderiza con el conteo nuevo) y
    otro que confirma la fecha ya revisada, que viaja en un hidden. Así no hay
    forma de confirmar una fecha distinta de la que generó el aviso.

    Dar de baja también pide confirmación (corta el acceso y saca al alumno
    del padrón del lector). Reactivar y generar códigos van directo: lo
    primero se deshace con una baja, y lo segundo es idempotente.
    """

    template_name = "alumnos/lote_confirmar.html"

    def get(self, request, *args, **kwargs):
        return redirect("alumnos:listado")

    def post(self, request, *args, **kwargs):
        self.filtros = lote.filtros_seguros(request.POST.get("filtros", ""))
        datos = request.POST.copy()
        # El POST de la barra no trae `fecha`: se propone hoy. Una `fecha`
        # presente pero vacía sí es un error (la borró el usuario).
        if datos.get("accion") == LoteForm.FIJAR_FECHA and "fecha" not in datos:
            datos["fecha"] = timezone.localdate().isoformat()
        form = LoteForm(datos)
        form.is_valid()
        for campo in ("accion", "ids"):
            if campo in form.errors:
                return self._volver(error=form.errors[campo][0])
        accion = form.cleaned_data["accion"]
        ids = form.cleaned_data["ids"]

        # El tope se cuenta sobre lo que es de este gimnasio: ids ajenos o
        # inexistentes se descartan en silencio y solo se informa cuántos.
        encontrados = list(
            Alumno.objects.for_gimnasio(self.gimnasio)
            .filter(pk__in=ids)
            .values_list("pk", flat=True)
        )
        self.no_encontrados = len(ids) - len(encontrados)
        if not encontrados:
            return self._volver(error="No se encontró ninguno de los alumnos seleccionados.")
        tope = (
            lote.MAX_LOTE_FECHA
            if accion == LoteForm.FIJAR_FECHA
            else lote.MAX_LOTE_ESTADO
        )
        if accion != LoteForm.GENERAR_CODIGOS and len(encontrados) > tope:
            return self._volver(
                error=(
                    f"Seleccionaste {len(encontrados)} alumnos y el máximo para "
                    f"esta acción es {tope}. Filtrá el listado y hacelo por partes."
                )
            )

        manejar = {
            LoteForm.FIJAR_FECHA: self._fijar_fecha,
            LoteForm.DAR_DE_BAJA: self._dar_de_baja,
            LoteForm.REACTIVAR: self._reactivar,
            LoteForm.GENERAR_CODIGOS: self._generar_codigos,
        }[accion]
        return manejar(form, encontrados)

    # -- Acciones ---------------------------------------------------------

    def _fijar_fecha(self, form, ids):
        from pagos.acceso import nace_bloqueada

        # Con error de rango, `cleaned_data` ya no trae la fecha.
        fecha = form.cleaned_data.get("fecha")
        if fecha is None and "fecha" not in form.errors:
            form.add_error("fecha", "Elegí la fecha de inicio.")
        if fecha is not None and form.cleaned_data.get("confirmado"):
            cambiados, salteados = lote.fijar_fecha_inicio(self.gimnasio, ids, fecha)
            texto = (
                f"Fecha fijada a {cambiados} alumno{'s' if cambiados != 1 else ''} "
                f"({fecha:%d/%m/%Y})."
            )
            if salteados:
                texto += (
                    f" {len(salteados)} se saltearon porque ya tienen cuotas: "
                    f"{lote.nombres(salteados)}."
                )
            nivel = messages.success if cambiados else messages.warning
            return self._volver(texto, nivel=nivel)

        cambian, saltean = lote.separar_por_cuotas(self.gimnasio, ids)
        hoy = timezone.localdate()
        # La regla es la del bloqueo real y depende solo de la fecha, no del
        # alumno: o nacen bloqueados todos los activos que cambian, o ninguno.
        # Los inactivos no cuentan: el cron no les emite cuotas.
        bloqueados = 0
        if fecha is not None and nace_bloqueada(self.gimnasio, fecha, hoy):
            bloqueados = sum(1 for a in cambian if a.estado == Alumno.Estado.ACTIVO)
        return self._confirmar(
            form,
            ids,
            fecha=fecha,
            cambian=cambian,
            saltean=saltean,
            nombres_cambian=lote.nombres(cambian),
            nombres_saltean=lote.nombres(saltean),
            bloqueados=bloqueados,
        )

    def _dar_de_baja(self, form, ids):
        if form.cleaned_data.get("confirmado"):
            cambiados = lote.cambiar_estado(self.gimnasio, ids, Alumno.Estado.INACTIVO)
            if not cambiados:
                return self._volver("Ninguno cambió: ya estaban dados de baja.", nivel=messages.info)
            return self._volver(
                f"{len(cambiados)} dado{'s' if len(cambiados) != 1 else ''} de baja: "
                f"{lote.nombres(cambiados)}."
            )
        alumnos = list(Alumno.objects.for_gimnasio(self.gimnasio).filter(pk__in=ids))
        cambian = [a for a in alumnos if a.estado != Alumno.Estado.INACTIVO]
        ya_estaban = [a for a in alumnos if a.estado == Alumno.Estado.INACTIVO]
        return self._confirmar(
            form,
            ids,
            cambian=cambian,
            ya_estaban=ya_estaban,
            nombres_cambian=lote.nombres(cambian),
            nombres_ya_estaban=lote.nombres(ya_estaban),
        )

    def _reactivar(self, form, ids):
        cambiados = lote.cambiar_estado(self.gimnasio, ids, Alumno.Estado.ACTIVO)
        if not cambiados:
            return self._volver("Ninguno cambió: ya estaban activos.", nivel=messages.info)
        texto = (
            f"{len(cambiados)} reactivado{'s' if len(cambiados) != 1 else ''}: "
            f"{lote.nombres(cambiados)}."
        )
        # El importado sin ancla vuelve sin ancla (la señal no lo toca): hay
        # que decirlo, o se reactiva creyendo que se le empieza a cobrar.
        sin_fecha = [a for a in cambiados if a.fecha_inicio_ciclo is None]
        if sin_fecha:
            uno = len(sin_fecha) == 1
            texto += (
                f" {len(sin_fecha)} {'sigue' if uno else 'siguen'} sin fecha de "
                f"inicio: no se {'le' if uno else 'les'} cobra hasta que se la "
                "cargues."
            )
        return self._volver(texto)

    def _generar_codigos(self, form, ids):
        from ingreso.services import generar_credenciales_en_bloque

        creadas = generar_credenciales_en_bloque(self.gimnasio, alumno_ids=ids)
        texto = f"Se generaron {creadas} código{'s' if creadas != 1 else ''} de ingreso."
        if creadas < len(ids):
            texto += " Los demás ya tenían uno o están dados de baja."
        return self._volver(texto)

    # -- Salidas ----------------------------------------------------------

    def _url_listado(self):
        url = reverse("alumnos:listado")
        return f"{url}?{self.filtros}" if self.filtros else url

    def _volver(self, texto=None, *, error=None, nivel=messages.success):
        if error:
            messages.error(self.request, error)
        elif texto:
            nivel(self.request, texto)
        no_encontrados = getattr(self, "no_encontrados", 0)
        if no_encontrados and not error:
            messages.warning(
                self.request,
                f"{no_encontrados} no se "
                f"{'encontró' if no_encontrados == 1 else 'encontraron'} "
                "(no son de tu gimnasio o ya no existen).",
            )
        return redirect(self._url_listado())

    def _confirmar(self, form, ids, **contexto):
        contexto.update(
            form=form,
            accion=form.cleaned_data["accion"],
            ids_csv=",".join(str(pk) for pk in ids),
            filtros=self.filtros,
            url_listado=self._url_listado(),
            no_encontrados=self.no_encontrados,
            tolerancia=self.gimnasio.dias_tolerancia_pago,
        )
        return render(self.request, self.template_name, contexto)


class AccesoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Vista de conjunto de los accesos del gimnasio.

    Cuelga del listado de alumnos y no del nav: el nav ya tiene 8 ítems y hubo
    un esfuerzo deliberado por acortarlo de 10 a 8 (mismo criterio que el
    importador de Excel, que también se accede desde su listado).

    `select_related` no es una micro-optimización: cada fila lee el username y
    el último ingreso, que viven dos saltos más allá (`alumno.perfil.usuario`),
    así que sin esto la vista hace una query por alumno. Cubierto por
    `PanelAccesosTests.test_no_hace_una_query_por_alumno`.
    """

    model = Alumno
    template_name = "alumnos/acceso_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        return super().get_queryset().select_related("perfil__usuario")


AVISO_NACE_BLOQUEADA = (
    "Con esta fecha la primera cuota ya pasó la tolerancia: el acceso se pausa "
    "esta noche."
)


def _avisar_si_nace_bloqueada(request, form, alumno):
    """`messages.warning` si la fecha de inicio que el staff ACABA de elegir
    hace que la cuota que emita el cron esta noche ya esté bloqueando.

    No es un error de form: la fecha es válida (está en el rango) y puede ser
    exactamente lo que se quiere -- el alumno empezó hace dos semanas y no
    pagó. Pero el staff tiene que enterarse ANTES de que el alumno llame
    porque no puede ver su rutina. Solo para activos (un inactivo no recibe
    cuotas) y solo si la fecha cambió: guardar el teléfono de un alumno con un
    ancla vieja no tiene nada que avisar. Misma regla que el preview del
    importador y el lote (`pagos.acceso.nace_bloqueada`), sin queries.
    """
    from pagos.acceso import nace_bloqueada

    if "fecha_inicio_ciclo" not in form.changed_data:
        return
    if alumno.estado != Alumno.Estado.ACTIVO:
        return
    if nace_bloqueada(alumno.gimnasio, alumno.fecha_inicio_ciclo, timezone.localdate()):
        messages.warning(request, AVISO_NACE_BLOQUEADA)


class AlumnoCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno creado correctamente.")
        _avisar_si_nace_bloqueada(self.request, form, self.object)
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


class AlumnoUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno actualizado correctamente.")
        _avisar_si_nace_bloqueada(self.request, form, self.object)
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


@method_decorator(never_cache, name="dispatch")
class AlumnoDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """La "ficha" del alumno: sus datos + secciones de solo lectura de sus
    pagos (app `pagos`) y su rutina activa (app `rutinas`).

    **`never_cache`**: la ficha dibuja el QR de ingreso del alumno, que es una
    llave al portador (el template le agrega `hx-history="false"` para que
    htmx tampoco la guarde en `localStorage`). Va sobre toda la vista y no
    solo cuando hay QR: la ficha es un dato personal de todas formas.
    """

    model = Alumno
    template_name = "alumnos/alumno_detail.html"
    context_object_name = "alumno"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from rutinas.models import RutinaAsignada

        context["pagos"] = self.object.pagos.all()
        context["rutina_actual"] = RutinaAsignada.vigente_de(alumno=self.object)
        context["rutina_proxima"] = RutinaAsignada.proxima_de(alumno=self.object)
        # El historial: hasta ahora la ficha era el ÚNICO acceso a una rutina
        # asignada y solo linkeaba la actual. Desde que los planes conviven en
        # vez de archivarse, sin esta lista no habría forma de llegar a los
        # anteriores.
        context["rutinas_del_alumno"] = self.object.rutinas_asignadas.all()[:10]
        context.update(self._control_de_ingreso())
        return context

    ULTIMOS_INGRESOS = 5

    def render_to_response(self, context, **response_kwargs):
        # Si la ficha dibuja el QR, tiene que ser un documento propio y no un
        # swap de htmx adentro de otra página (que quedaría en el bfcache con
        # la llave en el DOM): ver `ingreso.presentacion.fuera_del_boost`.
        from ingreso import presentacion

        if context["codigo_ingreso"].vigente:
            fuera = presentacion.fuera_del_boost(self.request)
            if fuera is not None:
                return fuera
        return super().render_to_response(context, **response_kwargs)

    def _control_de_ingreso(self):
        """La tarjeta «Credencial de ingreso». Costo fijo: una query por las
        credenciales (a lo sumo dos filas, una por tipo) y una por los últimos
        ingresos, con el lector en el mismo JOIN -- sin el `select_related`
        cada fila del listado es una query más."""
        from ingreso import presentacion
        from ingreso.models import Credencial

        por_tipo = {c.tipo: c for c in self.object.credenciales.all()}
        return {
            "codigo_ingreso": presentacion.codigo_de(
                self.object, por_tipo.get(Credencial.Tipo.QR)
            ),
            "credencial_facial": por_tipo.get(Credencial.Tipo.FACIAL),
            # `gimnasio` en el filtro: es el que abre el índice
            # `ingreso_antipassback_idx` (gimnasio, alumno, -momento), y deja
            # el aislamiento explícito en vez de heredado del alumno.
            "ultimos_ingresos": self.object.eventos_ingreso.filter(
                gimnasio=self.object.gimnasio_id
            ).select_related("dispositivo")[: self.ULTIMOS_INGRESOS],
            "estados_codigo": presentacion.Estado,
        }


class AlumnoDeleteView(
    StaffRequiredMixin, TenantScopedMixin, BorrarConExplicacionView
):
    """Sirve para el alumno cargado por error o de prueba. Uno con historial
    queda bloqueado por `Cuota` y `RutinaAsignada` (`PROTECT`) -- y eso
    es deliberado: los pagos son el registro de lo que el gimnasio facturó, y
    borrarlos para deshacerse de una ficha sería destruir contabilidad. Para
    ese caso ya existe la baja, que además le corta el acceso al portal (ver
    `alumnos/signals.py::sincronizar_acceso_con_estado`)."""

    model = Alumno
    alternativa = (
        "Para sacarlo del día a día sin perder su historial de cobros, usá "
        "«Inactivar alumno» en su ficha: deja de contar como activo y pierde "
        "el acceso al portal."
    )

    def get_titulo(self):
        return f"Eliminar a {self.object.nombre} {self.object.apellido}"

    def get_mensaje_exito(self):
        return "Alumno eliminado."

    def get_url_exito(self):
        return reverse("alumnos:listado")


class AlumnoToggleEstadoView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Flip activo <-> inactivo. POST-only: muta estado, nunca debe
    dispararse con un GET (link, prefetch, etc).

    El acceso del alumno es un ESPEJO de su estado: dar de baja apaga
    `User.is_active` y reactivar lo devuelve. Esa sincronización NO se hace
    acá sino en `alumnos/signals.py::sincronizar_acceso_con_estado`, porque
    `estado` también se escribe desde el form de la ficha y desde
    `crear_acceso` — repetirla en cada vista garantiza que alguna se olvide.

    No hace falta invalidar sesiones a mano: `ModelBackend.get_user()` llama a
    `user_can_authenticate()` en CADA request, así que apagar `is_active`
    también mata la sesión que el alumno ya tuviera abierta.
    """

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        alumno.estado = (
            Alumno.Estado.ACTIVO
            if alumno.estado != Alumno.Estado.ACTIVO
            else Alumno.Estado.INACTIVO
        )
        # El `atomic` sigue haciendo falta aunque la sincronización se haya
        # mudado a la señal: son dos escrituras (el estado y el `is_active`
        # del receiver) y si la segunda falla no puede quedar commiteada la
        # primera — sería exactamente la divergencia que este frente elimina.
        #
        # `update_fields` tiene que incluir lo que el `pre_save` escribe
        # (`registrar_fecha_de_baja` estampa `fecha_baja` y re-ancla
        # `fecha_inicio_ciclo` al reactivar): con `["estado"]` a secas Django
        # calcula esos valores y los TIRA, porque el UPDATE solo lleva las
        # columnas listadas. Así estuvo un release entero, y era el único
        # camino de UI para reactivar -- el que volvía de tres meses recibía
        # esa noche una cuota retroactiva ya vencida.
        with transaction.atomic():
            alumno.save(
                update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo", "modificado"]
            )


        messages.success(
            request, f"{alumno} ahora está {alumno.get_estado_display().lower()}."
        )
        return redirect("alumnos:detalle", pk=alumno.pk)


def _render_credenciales(request, alumno, password, modo):
    """Pantalla de "esto se ve una sola vez", compartida por el alta y la
    regeneración.

    Vive a nivel de módulo y no como método de una de las dos vistas para que
    ninguna tenga que alcanzar dentro de la otra.

    `never_cache` no es paranoia: esta pantalla se abre en la computadora del
    mostrador del gimnasio, que es compartida. Sin `no-store`, la contraseña
    queda recuperable con el botón "atrás" del navegador después de que el
    staff siguió con otra cosa.
    """
    respuesta = render(
        request,
        "alumnos/acceso_credenciales.html",
        {
            "alumno": alumno,
            "usuario": alumno.perfil.usuario.username,
            "password": password,
            "modo": modo,
        },
    )
    add_never_cache_headers(respuesta)
    return respuesta


class CrearAccesoView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Alta del login del alumno: solo para alumnos SIN `Alumno.perfil`.

    El POST exitoso NO redirige: renderiza la credencial en un 200. Es a
    propósito y no es un descuido de PRG. `messages` se serializa en la
    SESIÓN, que en este proyecto vive en la base de datos, así que mandar la
    contraseña por ahí la deja escrita en una tabla hasta que se renderiza.
    Un 200 directo la deja solo en esa respuesta.

    La contrapartida de romper PRG es que un F5 re-postea, y eso ya está
    cubierto: el guard de abajo redirige sin tocar nada cuando el alumno ya
    tiene acceso.
    """

    model = Alumno
    template_name = "alumnos/acceso_form.html"

    def get(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        form = CrearAccesoForm(initial=self._inicial(alumno))
        return self._render(request, alumno, form)

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        form = CrearAccesoForm(request.POST)
        if form.is_valid():
            try:
                password = servicios.crear_acceso(
                    alumno,
                    form.cleaned_data["tipo"],
                    form.cleaned_data["identificador"],
                )
            except servicios.AccesoYaExiste:
                # Otro request ganó la carrera (doble submit). El dato que
                # cargó el staff estaba bien, así que no corresponde el
                # mensaje de "probá con el otro".
                messages.error(request, "Este alumno ya tiene un acceso creado.")
                return redirect("alumnos:detalle", pk=alumno.pk)
            except servicios.IdentificadorEnUso:
                # Mensaje deliberadamente genérico: confirmar que ese email ya
                # existe convertiría este form en un enumerador de usuarios de
                # toda la plataforma, y ahora los usuarios SON emails reales.
                form.add_error(
                    "identificador",
                    "No se puede usar ese dato. Probá con el otro: si pusiste "
                    "el email, cargá el teléfono, o al revés.",
                )
            else:
                alumno.refresh_from_db()
                return _render_credenciales(request, alumno, password, "crear")
        return self._render(request, alumno, form)

    @staticmethod
    def _inicial(alumno):
        """Precarga el dato de contacto que la ficha ya tiene, para que el
        staff no lo vuelva a tipear (y no lo tipee distinto)."""
        if alumno.email:
            return {"tipo": identidad.TIPO_EMAIL, "identificador": alumno.email}
        if alumno.telefono:
            return {"tipo": identidad.TIPO_TELEFONO, "identificador": alumno.telefono}
        return {}

    def _render(self, request, alumno, form):
        return render(request, self.template_name, {"form": form, "alumno": alumno})


class RegenerarPasswordView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Contraseña nueva al azar para un alumno que YA tiene acceso.

    POST-only: muta credenciales, nunca debe dispararse con un GET (link,
    prefetch del navegador, etc). Mismo criterio que `AlumnoToggleEstadoView`.

    Expulsa al alumno de sus sesiones vivas como efecto colateral de que
    cambie el hash de la contraseña — ver `alumnos/services.py`.
    """

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is None:
            messages.error(request, "Este alumno todavía no tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        password = servicios.regenerar_password(alumno)
        return _render_credenciales(request, alumno, password, "regenerar")
