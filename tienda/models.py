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
    foto = models.ImageField(upload_to="tienda/productos/", blank=True)
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
    def foto_url_cacheable(self):
        """URL de la foto que el navegador puede cachear para siempre (nunca
        `foto.url`: en R2 sale firmada y distinta en cada render). La versión
        es `modificado` en ms, mismo criterio que `Gimnasio._url_de_archivo`."""
        if not self.foto:
            return ""
        version = int(self.modificado.timestamp() * 1000)
        return f"{reverse('tienda:tienda_foto_producto', args=[self.pk])}?v={version}"


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

    def clean(self):
        if self.gimnasio_id:
            validar_gimnasio_de(self.gimnasio, alumno=self.alumno)


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
