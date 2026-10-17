"""Modelos de la tienda del gimnasio (add-on).

`Variante` e `ItemPedido` NO son `TenantOwnedModel`: cuelgan de un padre que
sí lo es (`Producto`, `Pedido`), igual que `RutinaPlantillaItem`. Se acotan por
la cadena de FK: caen en cascada con su padre en `vaciar_gimnasio`, no van a
`_ensuciar` por separado, pero SÍ tienen hoja propia en la exportación.

`Variante.stock` es el DISPONIBLE, no lo que hay en el depósito: una unidad
reservada por un pedido ya no está en `stock`. Por eso el staff nunca escribe
el stock en forma absoluta (ver `services.ajustar_stock`).
"""

from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse

from core.models import TenantOwnedModel, validar_gimnasio_de
from pagos.models import EXTENSIONES_COMPROBANTE_PERMITIDAS

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

    class Meta:
        verbose_name = "configuración de la tienda"
        verbose_name_plural = "configuraciones de la tienda"
        constraints = [
            models.UniqueConstraint(fields=["gimnasio"], name="config_tienda_unica_por_gimnasio")
        ]

    def __str__(self):
        return f"Configuración de la tienda de {self.gimnasio}"


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


MAX_FOTOS_POR_PRODUCTO = 5


class FotoProducto(models.Model):
    """Hija de `Producto` como `Variante`: no es `TenantOwnedModel`, se acota
    por la FK. Borrar la fila NO borra el archivo de R2 (como logos y
    comprobantes). La de `posicion` más baja es la principal."""

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="fotos")
    imagen = models.ImageField(upload_to="tienda/productos/")
    posicion = models.PositiveSmallIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)

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
    nombre = models.CharField(
        max_length=60, blank=True,
        help_text="Talle, color o sabor. Vacío si el producto no tiene variantes.",
    )
    stock = models.PositiveIntegerField(default=0, verbose_name="stock disponible")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "variante"
        verbose_name_plural = "variantes"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["producto", "nombre"], name="variante_unica_por_producto"),
            models.CheckConstraint(condition=models.Q(stock__gte=0), name="variante_stock_no_negativo"),
        ]

    def __str__(self):
        return f"{self.producto.nombre} · {self.nombre}" if self.nombre else self.producto.nombre


class Pedido(TenantOwnedModel):
    class Estado(models.TextChoices):
        PENDIENTE_PAGO = "pendiente_pago", "Esperando el pago"
        EN_REVISION = "en_revision", "Comprobante en revisión"
        PAGADO = "pagado", "Pagado, para retirar"
        ENTREGADO = "entregado", "Entregado"
        VENCIDO = "vencido", "Vencido"
        CANCELADO = "cancelado", "Cancelado"
        DEVUELTO = "devuelto", "Devuelto"

    class Medio(models.TextChoices):
        TRANSFERENCIA = "transferencia", "Transferencia"

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

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-creado"]
        indexes = [models.Index(fields=["gimnasio", "estado"], name="pedido_gimnasio_estado")]

    def __str__(self):
        return f"Pedido #{self.pk}"

    @property
    def badge_clase(self):
        """Variante de `.badge` para el estado (única fuente: la usan los
        cuatro templates que muestran el estado de un pedido)."""
        return _BADGE_POR_ESTADO[self.estado]

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


_E = Pedido.Estado
_BADGE_POR_ESTADO = {
    _E.PENDIENTE_PAGO: "badge--alerta",
    _E.EN_REVISION: "badge--alerta",
    _E.PAGADO: "badge--ok",
    _E.ENTREGADO: "badge--ok",
    _E.VENCIDO: "badge--neutro",
    _E.CANCELADO: "badge--neutro",
    _E.DEVUELTO: "badge--neutro",
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
