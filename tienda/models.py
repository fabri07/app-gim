"""Modelos de la tienda del gimnasio (add-on).

`Variante` e `ItemPedido` NO son `TenantOwnedModel`: cuelgan de un padre que
sí lo es (`Producto`, `Pedido`), igual que `RutinaPlantillaItem`. Se acotan por
la cadena de FK: caen en cascada con su padre en `vaciar_gimnasio`, no van a
`_ensuciar` por separado, pero SÍ tienen hoja propia en la exportación.

`Variante.stock` es el DISPONIBLE, no lo que hay en el depósito: una unidad
reservada por un pedido ya no está en `stock`. Por eso el staff nunca escribe
el stock en forma absoluta (ver `services.ajustar_stock`).
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse

from calendario.fields import EncryptedTextField
from core.models import TenantOwnedModel, validar_gimnasio_de
from pagos.models import EXTENSIONES_COMPROBANTE_PERMITIDAS
from tienda.codigos import LARGO_MAXIMO as LARGO_MAXIMO_CODIGO
from tienda.codigos import codigo_interno, normalizar
from tienda.variantes import LARGO_MAXIMO_NOMBRE, componer_nombre

MAX_LARGO_MOTIVO = 300


class ConfiguracionTienda(TenantOwnedModel):
    """Separada de `Gimnasio` a propósito, igual que `ConfiguracionIngreso`:
    guardar el gimnasio cambia su `modificado`, que versiona logo, fondo e
    ícono PWA."""

    publicada = models.BooleanField(
        default=False,
        verbose_name="mostrar la tienda a los alumnos",
    )
    horas_reserva_transferencia = models.PositiveSmallIntegerField(
        default=24,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        verbose_name="horas para mandar el comprobante",
        help_text="Si el alumno no sube el comprobante en este plazo, el pedido vence y el stock vuelve.",
    )
    max_pedidos_activos = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        verbose_name="pedidos sin pagar por alumno",
        help_text="Cuántos pedidos esperando pago o revisión puede tener un alumno a la vez.",
    )
    instrucciones_retiro = models.TextField(
        blank=True,
        max_length=500,
        verbose_name="instrucciones para retirar",
        help_text="Ej.: «Retirás en recepción, de lunes a viernes de 8 a 21».",
    )

    # Los tres campos de abajo llevan `db_default` además de `default`: el
    # `migrate` corre en el `buildCommand` mientras la versión vieja sigue
    # atendiendo, y sus INSERT no conocen las columnas nuevas.
    acepta_transferencia = models.BooleanField(
        default=True, db_default=True,
        verbose_name="aceptar transferencia",
    )
    acepta_mercadopago = models.BooleanField(
        default=False, db_default=False,
        verbose_name="aceptar Mercado Pago",
    )
    minutos_reserva_mp = models.PositiveSmallIntegerField(
        default=30, db_default=30,
        validators=[MinValueValidator(20), MaxValueValidator(120)],
        verbose_name="minutos para pagar con Mercado Pago",
        help_text="Si el alumno no paga en este plazo, el pedido vence y el stock vuelve.",
    )
    #: Cuándo se le avisó por última vez a la plataforma que hay algo para
    #: moderar. Dato de plataforma: lo escribe el sistema, no el staff.
    ultimo_aviso_moderacion = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "configuración de la tienda"
        verbose_name_plural = "configuraciones de la tienda"
        constraints = [
            models.UniqueConstraint(fields=["gimnasio"], name="config_tienda_unica_por_gimnasio")
        ]

    def __str__(self):
        return f"Configuración de la tienda de {self.gimnasio}"


class CuentaMercadoPago(TenantOwnedModel):
    """La cuenta de Mercado Pago del PROPIO gimnasio (OAuth): la plata de la
    tienda va directo a ella, la plataforma nunca la toca. Una por gimnasio.

    Los tokens van cifrados con `MP_TOKEN_ENCRYPTION_KEY` (no la de Google).
    Un vacío no se cifra, así que los tests y `_ensuciar` los dejan en `""`
    sin necesitar la clave. Sin la clave, o con una rotada, leerlos da `None`:
    la cuenta se trata como «hay que reconectar». Nunca se exporta ni se
    muestra en el admin."""

    class Estado(models.TextChoices):
        CONECTADA = "conectada", "Conectada"
        RECONECTAR = "reconectar", "Hay que reconectarla"
        DESCONECTADA = "desconectada", "Desconectada"

    access_token = EncryptedTextField(
        clave_setting="MP_TOKEN_ENCRYPTION_KEY", blank=True, default=""
    )
    refresh_token = EncryptedTextField(
        clave_setting="MP_TOKEN_ENCRYPTION_KEY", blank=True, default=""
    )
    mp_user_id = models.CharField(max_length=40, blank=True)
    token_expira = models.DateTimeField(null=True, blank=True)
    #: Identifica el webhook de ESTA cuenta en la URL (no es secreto: la
    #: autenticidad sale de leer el pago con el token del gimnasio).
    uuid_webhook = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    es_de_prueba = models.BooleanField(default=False)
    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.CONECTADA
    )
    conectada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    conectada_en = models.DateTimeField(null=True, blank=True)
    desconectada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "cuenta de Mercado Pago"
        verbose_name_plural = "cuentas de Mercado Pago"
        constraints = [
            models.UniqueConstraint(fields=["gimnasio"], name="cuenta_mp_unica_por_gimnasio")
        ]

    def __str__(self):
        return f"Mercado Pago de {self.gimnasio}"


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config: garantiza que la fila exista (mismo
    patrón que `turnos.models.obtener_configuracion`)."""
    config, _ = ConfiguracionTienda.objects.get_or_create(gimnasio=gimnasio)
    return config


class CategoriaProducto(TenantOwnedModel):
    nombre = models.CharField(max_length=60)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "categoría de producto"
        verbose_name_plural = "categorías de producto"
        ordering = ["orden", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "nombre"], name="categoria_producto_unica_por_gimnasio"
            )
        ]

    def __str__(self):
        return self.nombre


class Producto(TenantOwnedModel):
    categoria = models.ForeignKey(
        CategoriaProducto, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="productos",
    )
    nombre = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True, max_length=1000, verbose_name="descripción")
    precio = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    #: MUERTA desde `tienda/0002`: se copió a `FotoProducto` (la galería) y
    #: NO se vació, para que revertir el código alcance como vuelta atrás
    #: (mismo criterio que `Cuota.mes`). Nadie la escribe. No leer de acá.
    foto = models.ImageField(upload_to="tienda/productos/", blank=True, editable=False)
    publicado = models.BooleanField(
        default=True,
        help_text="Destildalo para sacarlo de la tienda sin perder su historial de ventas.",
    )
    orden = models.PositiveSmallIntegerField(default=0)
    #: Hasta dos características por producto, texto libre («Talle»,
    #: «Color»): los valores de cada variante arman su nombre (ver
    #: `tienda/variantes.py`). Vacías: el producto tiene una sola variante
    #: sin nombre. `db_default`: los INSERT de la versión vieja durante el
    #: deploy no conocen las columnas.
    caracteristica_1 = models.CharField(
        max_length=30, blank=True, default="", db_default="",
        verbose_name="característica 1",
    )
    caracteristica_2 = models.CharField(
        max_length=30, blank=True, default="", db_default="",
        verbose_name="característica 2",
    )

    class Moderacion(models.TextChoices):
        PENDIENTE = "pendiente", "Esperando aprobación"
        APROBADO = "aprobado", "Aprobado"
        BAJADO = "bajado", "Dado de baja por la plataforma"

    #: Dato de la PLATAFORMA (lo escribe el superadmin, nunca el staff del
    #: gimnasio): `editable=False` lo deja fuera de `ProductoForm`. Lleva
    #: `db_default` porque el `migrate` corre en el `buildCommand` mientras la
    #: versión vieja sigue atendiendo, y sus INSERT no nombran la columna. El
    #: default es `pendiente` a propósito: si se cuela un INSERT viejo, lo
    #: seguro es que quede sin vender hasta que alguien lo apruebe. Lo que ya
    #: existía lo deja aprobado `tienda/0009`.
    moderacion = models.CharField(
        max_length=10, choices=Moderacion.choices,
        default=Moderacion.PENDIENTE, db_default=Moderacion.PENDIENTE, editable=False,
    )
    #: Un producto aprobado cuyo texto o fotos cambiaron y esperan revisión.
    cambio_pendiente = models.BooleanField(default=False, db_default=False, editable=False)
    #: Aviso al staff (foto rechazada, baja): se limpia al aprobar.
    moderacion_motivo = models.CharField(
        max_length=300, blank=True, default="", db_default="", editable=False
    )
    moderacion_desde = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        if self.gimnasio_id:
            validar_gimnasio_de(self.gimnasio, categoria=self.categoria)

    @property
    def foto_principal(self):
        """La primera de la galería (orden `posicion`, `pk`), o `None`. Sale
        del `prefetch_related("fotos")` de la vista: sin él, una query."""
        return next(iter(self.fotos.all()), None)


def q_vendible(prefijo=""):
    """Lo que se puede vender y mostrar: publicado Y aprobado. Todo el que
    filtre productos por "a la venta" pasa por acá; `prefijo` es el camino
    desde otro modelo (`q_vendible("producto__")` para `Variante`)."""
    return Q(**{
        f"{prefijo}publicado": True,
        f"{prefijo}moderacion": Producto.Moderacion.APROBADO,
    })


MAX_FOTOS_POR_PRODUCTO = 5


class FotoProducto(models.Model):
    """Hija de `Producto` como `Variante`: no es `TenantOwnedModel`, se acota
    por la FK. Borrar la fila NO borra el archivo de R2 (como logos y
    comprobantes). La de `posicion` más baja es la principal."""

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="fotos")
    imagen = models.ImageField(upload_to="tienda/productos/")
    posicion = models.PositiveSmallIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)
    #: Sólo se sirve en público una foto aprobada por la plataforma. Mismo
    #: criterio que `Producto.moderacion`: `db_default` por la ventana del
    #: deploy; lo existente lo aprueba `tienda/0009`.
    aprobada = models.BooleanField(default=False, db_default=False, editable=False)

    class Meta:
        verbose_name = "foto"
        verbose_name_plural = "fotos"
        ordering = ["posicion", "pk"]

    def __str__(self):
        return f"Foto {self.posicion + 1} de {self.producto_id}"

    @property
    def url_cacheable(self):
        """URL que el navegador cachea para siempre (nunca `imagen.url`: en R2
        sale firmada y distinta en cada render). La versión es el nombre del
        archivo (uuid de `preparar_foto`): una foto guardada nunca se
        reemplaza, así que la respuesta `immutable` es inmutable de verdad."""
        version = self.imagen.name.rsplit("/", 1)[-1]
        url = reverse("tienda:tienda_foto_producto", args=[self.producto_id, self.pk])
        return f"{url}?v={version}"


class Variante(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="variantes")
    #: CALCULADO de `valor_1` y `valor_2` («M · Negro»; ver `save()`). Se
    #: queda como columna porque todo lo demás lo lee (catálogo, carrito,
    #: `str()` del snapshot del pedido, exportación). Sin `UniqueConstraint`
    #: desde `tienda/0008`: el guardado fila por fila del formset chocaba al
    #: intercambiar o reusar nombres; la unicidad la validan el formset del
    #: staff y el del admin (riesgo aceptado, ver ISSUES.md).
    nombre = models.CharField(
        max_length=LARGO_MAXIMO_NOMBRE, blank=True,
        help_text="Se arma con los valores de las características. Vacío si el producto no tiene variantes.",
    )
    valor_1 = models.CharField(max_length=60, blank=True, default="", db_default="")
    valor_2 = models.CharField(max_length=60, blank=True, default="", db_default="")
    stock = models.PositiveIntegerField(default=0, verbose_name="stock disponible")
    activa = models.BooleanField(default=True)
    #: Código de barras: el de fábrica o el interno que inventa la app (ver
    #: `tienda/codigos.py`). Sin constraint de unicidad en la base: `Variante`
    #: no tiene `gimnasio` y la unicidad es por gimnasio, así que la valida el
    #: formset del staff (riesgo aceptado de dos guardados simultáneos, ver
    #: ISSUES.md). `db_default`: los INSERT de la versión vieja durante el
    #: deploy no conocen la columna; esas quedan en «» y se completan al guardar.
    codigo = models.CharField(
        max_length=LARGO_MAXIMO_CODIGO, blank=True, default="", db_default="",
        verbose_name="código de barras",
        help_text="Vacío: la app inventa uno.",
    )

    class Meta:
        verbose_name = "variante"
        verbose_name_plural = "variantes"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(stock__gte=0), name="variante_stock_no_negativo"),
        ]

    def __str__(self):
        return f"{self.producto.nombre} · {self.nombre}" if self.nombre else self.producto.nombre

    def clean(self):
        # Corre en todo ModelForm (staff y admin) vía `full_clean`. El error
        # va en `valor_1`, que es campo de los dos forms (sobre `nombre`, que
        # no es campo, el form tira `ValueError`). Sin esto un nombre de más
        # de 60 llega a Postgres como `DataError` (SQLite no valida largos).
        nombre = componer_nombre(self.valor_1, self.valor_2)
        if len(nombre) > LARGO_MAXIMO_NOMBRE:
            raise ValidationError({
                "valor_1": (
                    f"El nombre de la variante («{nombre}») pasa de {LARGO_MAXIMO_NOMBRE} "
                    "caracteres: acortá los valores."
                )
            })

    def save(self, *args, **kwargs):
        # Antes del INSERT/UPDATE (la demo usa `str(variante)` enseguida). Sin
        # valores no toca `nombre`: así se puede seguir creando con `nombre=`
        # (tests, datos viejos). El form del staff, en cambio, lo asigna
        # siempre (también «»).
        if self.valor_1 or self.valor_2:
            self.nombre = componer_nombre(self.valor_1, self.valor_2)
        # Normalizar acá y no sólo en el form: el admin también guarda.
        self.codigo = normalizar(self.codigo)
        super().save(*args, **kwargs)
        if not self.codigo:
            # El interno sale del `pk`, que recién existe después del INSERT.
            # También en memoria: un `save(update_fields=[..., "codigo"])`
            # posterior sobre esta instancia no lo pisa con «».
            self.codigo = codigo_interno(self.pk)
            Variante.objects.filter(pk=self.pk).update(codigo=self.codigo)


class Pedido(TenantOwnedModel):
    class Estado(models.TextChoices):
        PENDIENTE_PAGO = "pendiente_pago", "Esperando el pago"
        EN_REVISION = "en_revision", "Comprobante en revisión"
        PAGADO = "pagado", "Pagado, para retirar"
        ENTREGADO = "entregado", "Entregado"
        VENCIDO = "vencido", "Vencido"
        CANCELADO = "cancelado", "Cancelado"
        DEVUELTO = "devuelto", "Devuelto"
        #: Mercado Pago cobró pero el pedido no se pudo dejar en «Pagado»
        #: (monto distinto, sin stock, pago tardío sobre uno cancelado). NO
        #: retiene stock: lo resuelve el staff.
        REVISAR = "revisar", "Pagado, a revisar"

    class Medio(models.TextChoices):
        TRANSFERENCIA = "transferencia", "Transferencia"
        MERCADOPAGO = "mercadopago", "Mercado Pago"

    alumno = models.ForeignKey("alumnos.Alumno", on_delete=models.PROTECT, related_name="pedidos")
    medio = models.CharField(max_length=20, choices=Medio.choices, default=Medio.TRANSFERENCIA)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE_PAGO)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    reservado_hasta = models.DateTimeField(null=True, blank=True)
    comprobante = models.FileField(
        upload_to="tienda/comprobantes/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=EXTENSIONES_COMPROBANTE_PERMITIDAS)],
    )
    comprobante_subido_en = models.DateTimeField(null=True, blank=True)
    pagado_en = models.DateTimeField(null=True, blank=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    motivo = models.CharField(max_length=MAX_LARGO_MOTIVO, blank=True)
    cancelacion_pedida_en = models.DateTimeField(null=True, blank=True)
    cancelacion_resuelta_en = models.DateTimeField(null=True, blank=True)

    # --- Mercado Pago -------------------------------------------------------
    # `null=True`: Postgres admite varios NULL bajo `unique`, así un INSERT del
    # código viejo durante el deploy no rompe. El código exige que no sea nulo
    # al crear un link; la migración llena los existentes.
    referencia_externa = models.UUIDField(
        null=True, unique=True, default=uuid.uuid4, editable=False
    )
    preference_id = models.TextField(blank=True, default="", db_default="")
    init_point = models.TextField(blank=True, default="", db_default="")
    payment_id = models.TextField(blank=True, default="", db_default="")
    estado_mp = models.TextField(blank=True, default="", db_default="")
    #: Sólo `resumen_de_pago` (nunca la respuesta cruda de MP) y la lista
    #: `pagos_adicionales`. Nunca se exporta.
    respuesta_mp = models.JSONField(null=True, blank=True)
    #: «Hay algún pago adicional sin resolver»: alimenta la pestaña «Revisar MP».
    revision_mp_pendiente = models.BooleanField(default=False, db_default=False)
    #: Lo canceló el staff (`cancelar_por_staff`), no el alumno: un pago de
    #: Mercado Pago tardío NO lo revive (va a REVISAR). No alcanza con mirar
    #: `motivo`: el staff puede cancelar sin escribir ninguno.
    cancelado_por_staff = models.BooleanField(default=False, db_default=False, editable=False)

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-creado"]
        indexes = [models.Index(fields=["gimnasio", "estado"], name="pedido_gimnasio_estado")]
        constraints = [
            # Un pago de MP acredita un solo pedido. Parcial: los pedidos sin
            # pago (todos los de transferencia) tienen `""`.
            models.UniqueConstraint(
                fields=["payment_id"], condition=~models.Q(payment_id=""),
                name="pedido_payment_id_unico",
            ),
        ]

    def __str__(self):
        return f"Pedido #{self.pk}"

    @property
    def estado_mp_legible(self):
        """El `status` crudo de Mercado Pago en castellano para el staff; uno
        que no conocemos se muestra tal cual (mejor crudo que nada)."""
        return _ESTADO_MP_LEGIBLE.get(self.estado_mp, self.estado_mp)

    @property
    def badge_clase(self):
        """Variante de `.badge` para el estado (única fuente: la usan los
        cuatro templates que muestran el estado de un pedido)."""
        return _BADGE_POR_ESTADO[self.estado]

    @property
    def vence_preferencia(self):
        """Hasta cuándo es pagable el link de MP: un margen ANTES de la
        reserva, así un pago nunca llega con el stock ya devuelto."""
        if self.reservado_hasta is None:
            return None
        return self.reservado_hasta - MARGEN_PREFERENCIA

    @property
    def cancelacion_pendiente(self):
        return self.cancelacion_pedida_en is not None and self.cancelacion_resuelta_en is None

    @property
    def cancelacion_rechazada(self):
        return (
            self.cancelacion_resuelta_en is not None
            and self.estado != self.Estado.DEVUELTO
        )

    def clean(self):
        if self.gimnasio_id:
            validar_gimnasio_de(self.gimnasio, alumno=self.alumno)


#: Cuánto antes de `reservado_hasta` vence la preferencia de Mercado Pago.
MARGEN_PREFERENCIA = timedelta(minutes=10)

#: Los `status` de un pago de Mercado Pago (API v1/payments).
_ESTADO_MP_LEGIBLE = {
    "approved": "aprobado",
    "authorized": "autorizado",
    "pending": "pendiente",
    "in_process": "en proceso",
    "in_mediation": "en mediación",
    "rejected": "rechazado",
    "cancelled": "cancelado",
    "refunded": "devuelto",
    "charged_back": "contracargo",
}

_E = Pedido.Estado
_BADGE_POR_ESTADO = {
    _E.PENDIENTE_PAGO: "badge--alerta",
    _E.EN_REVISION: "badge--alerta",
    _E.PAGADO: "badge--ok",
    _E.ENTREGADO: "badge--ok",
    _E.VENCIDO: "badge--neutro",
    _E.CANCELADO: "badge--neutro",
    _E.DEVUELTO: "badge--neutro",
    _E.REVISAR: "badge--alerta",
}

#: Estados cuyas unidades están descontadas de `Variante.stock`.
ESTADOS_QUE_RETIENEN = (
    Pedido.Estado.PENDIENTE_PAGO,
    Pedido.Estado.EN_REVISION,
    Pedido.Estado.PAGADO,
)
#: Pedidos que todavía esperan algo del alumno o del staff antes de cobrarse
#: (los que cuenta el tope por alumno).
ESTADOS_EN_CURSO = (Pedido.Estado.PENDIENTE_PAGO, Pedido.Estado.EN_REVISION)
#: Pedidos «sin cerrar»: retienen stock o esperan que el staff decida qué
#: hacer con plata que Mercado Pago ya cobró (REVISAR). Los usan `vivos` del
#: panel y el acceso del alumno a sus pedidos con la tienda apagada.
ESTADOS_SIN_CERRAR = ESTADOS_QUE_RETIENEN + (Pedido.Estado.REVISAR,)


class ItemPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="items")
    variante = models.ForeignKey(Variante, on_delete=models.PROTECT, related_name="items")
    #: Snapshot: el pedido muestra lo que se compró aunque después cambien el
    #: nombre o el precio del producto.
    nombre = models.CharField(max_length=190)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "ítem de pedido"
        verbose_name_plural = "ítems de pedido"
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gte=1), name="item_pedido_cantidad_positiva"),
        ]

    def __str__(self):
        return f"{self.cantidad} × {self.nombre}"

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad


class DenunciaProducto(TenantOwnedModel):
    """Una denuncia de un alumno sobre un producto de la tienda. La lee la
    plataforma en la cola de moderación; el staff del gimnasio no la ve.

    `alumno` es SET_NULL a propósito: con PROTECT o CASCADE, la pantalla de
    borrar un alumno le diría al staff «tiene 1 denuncia» y delataría al
    denunciante (y PROTECT impediría borrarlo)."""

    class Motivo(models.TextChoices):
        DROGAS = "drogas", "Drogas o medicamentos sin receta"
        SEXUAL = "sexual", "Contenido o servicios sexuales"
        ARMAS = "armas", "Armas"
        TRATA = "trata", "Trata o explotación de personas"
        ESTAFA = "estafa", "Estafa o producto falso"
        OTRO = "otro", "Otro"

    class Estado(models.TextChoices):
        ABIERTA = "abierta", "Abierta"
        RESUELTA = "resuelta", "Resuelta"

    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name="denuncias"
    )
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.SET_NULL, null=True,
        related_name="denuncias_tienda",
    )
    motivo = models.CharField(max_length=20, choices=Motivo)
    detalle = models.TextField(blank=True, max_length=500)
    estado = models.CharField(max_length=10, choices=Estado, default=Estado.ABIERTA)

    class Meta:
        verbose_name = "denuncia de producto"
        verbose_name_plural = "denuncias de productos"
        constraints = [
            models.UniqueConstraint(
                fields=["producto", "alumno"],
                condition=Q(estado="abierta"),
                name="denuncia_abierta_unica",
            ),
        ]

    def __str__(self):
        return f"Denuncia de {self.producto_id} ({self.motivo})"

    def clean(self):
        if self.gimnasio_id:
            validar_gimnasio_de(self.gimnasio, producto=self.producto, alumno=self.alumno)


class FotoRetenida(models.Model):
    """Copia del registro de una foto de producto que se borró: qué gimnasio
    la subió, para qué producto, cuándo y por qué se fue. El archivo es el
    MISMO de R2 (nadie borra archivos), así que no ocupa espacio de más.

    Dato de la PLATAFORMA, no `TenantOwnedModel`: lo escribe una señal (ver
    `tienda/retencion.py`), lo lee sólo el superadmin, no va a la exportación
    del gimnasio ni lo borra `vaciar_gimnasio`. Existe por protección de
    datos y como evidencia: un gimnasio que sube algo ilegal y lo quita antes
    de la revisión deja el rastro igual (pedido del dueño del producto,
    2026-10-20). `gimnasio` es PROTECT a propósito: con fotos retenidas, el
    gimnasio no se borra por accidente.

    Sin FK a `Producto`: el producto puede haberse borrado; `producto_original`
    es su pk de entonces y `producto_nombre` su nombre de entonces."""

    class Motivo(models.TextChoices):
        RECHAZADA = "rechazada", "Rechazada por la plataforma"
        QUITADA = "quitada", "Quitada por el gimnasio al editar"
        PRODUCTO_BORRADO = "producto_borrado", "El gimnasio borró el producto"
        OTRO = "otro", "Borrada por otro camino (admin, consola)"

    gimnasio = models.ForeignKey(
        "tenants.Gimnasio", on_delete=models.PROTECT, related_name="fotos_retenidas"
    )
    producto_original = models.PositiveBigIntegerField()
    producto_nombre = models.CharField(max_length=120)
    imagen = models.ImageField(upload_to="tienda/productos/")
    estaba_aprobada = models.BooleanField()
    subida = models.DateTimeField()
    motivo = models.CharField(max_length=20, choices=Motivo.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    retenida = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "foto retenida"
        verbose_name_plural = "fotos retenidas"
        ordering = ["-retenida", "-pk"]

    def __str__(self):
        return f"Foto retenida de «{self.producto_nombre}» ({self.get_motivo_display()})"
