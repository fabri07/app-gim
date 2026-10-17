# Tienda del gimnasio — Hito 1 (transferencia) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El staff de un gimnasio con el add-on carga productos con variantes y stock; el alumno compra desde su portal con carrito y paga por transferencia subiendo el comprobante dentro de un plazo; si el plazo vence, el stock vuelve. Sin una sola línea de Mercado Pago (eso es el Hito 2).

**Architecture:** App nueva `tienda/` con modelos tenant-owned, toda la lógica de stock en `tienda/services.py` (updates condicionales con `F()`, transiciones por `filter(estado__in=…).update()`), vistas de staff y de alumno en `tienda/views.py`, avisos push en `notificaciones/services.py` (donde viven todos los `notificar_*`) y el vencimiento como cron dentro de un workflow existente más un camino perezoso.

**Tech Stack:** Django 5.2, templates + htmx (`hx-boost` global) + Tailwind compilado, Pillow, `django-storages` (R2 en producción, `InMemoryStorage` en tests).

**Spec:** `docs/superpowers/specs/2026-10-17-tienda-design.md` (léela entera antes de la Tarea 1; la sección «Ajustes del 17-10» manda sobre lo anterior).

**Tareas:** 1 Modelos y los TRES lugares · 2 Carrito, reserva y vencimiento · 3 Transiciones, reintento del comprobante y arrepentimiento · 4 Staff: productos · 5 Staff: pedidos, configuración, tarjeta y barra · 6 Estadísticas de ventas en el panel · 7 Alumno · 8 Push, superadmin y cron · 9 Demo, docs y verificación.

## Global Constraints

- Todo en castellano rioplatense: nombres de modelos, campos, funciones, mensajes y commits (`feat(tienda): …`).
- Correr con el venv del repo principal: `PY=/Users/fabriziosola/app_gim/.venv/bin/python`. El `.env` ya está copiado en el worktree (la suite NO es hermética sin él). Baseline al arrancar: **2673 tests OK**.
- Fechas de dominio con `timezone.localdate()`, nunca `timezone.now().date()`. Momentos con `timezone.now()`.
- `QuerySet.update()` no dispara `auto_now`: en `Pedido`/`Producto` pasar `modificado=ahora` explícito; en `Gimnasio` **nunca** tocar `modificado`.
- Nada de N+1: el costo en queries depende del catálogo, nunca de la cantidad de pedidos. Contadores del panel = UNA query agregada.
- `hx-boost="false"` en todo form que suba archivos. Un 4xx bajo boost es un click mudo: los errores de dominio van como `messages.error` + redirect, o cartel con 200.
- Cada campo renderizado a mano lleva su error (`{% include 'partials/campo_form.html' with campo=form.x %}` o `{% if form.x.errors %}<p class="config-error">`); `core.tests.ErroresDeFormularioVisiblesTests` barre todos los templates.
- `{# #}` es de una sola línea; para varias, `{% comment %}`.
- Tailwind 4 escanea `templates/` (`@source` en `styles/input.css`): **al terminar cada tarea que toca templates (4, 5, 6, 7, 8 y 9) correr `npm run build:css` y sumar `static/css/app.css` al `git add` del commit** (si no cambió, git no agrega nada). Una clase utilitaria nueva en `templates/tienda/*` no existe en producción sin esto.
- Antes de cada commit: `git status --short` — ningún archivo NUEVO sin agregar (migraciones, templates).
- La suite no sale a la red. Nada de Mercado Pago en este hito.
- Regla de los TRES lugares: todo `TenantOwnedModel` nuevo va en el mismo commit a `tenants/demo.py::vaciar_gimnasio`, `_ensuciar` de `tenants/tests.py` y `HOJAS`/`EXCLUIDOS` de `tenants/exportacion.py`.

## Review Focus

1. **Dos alumnos compran la última unidad a la vez** → uno obtiene el pedido y el otro «sin stock de X»; el stock nunca queda negativo. Test: `CrearPedidoTests.test_la_segunda_compra_de_la_ultima_unidad_falla_sin_tocar_nada` (Tarea 2).
2. **El comprobante llega 1 minuto después del plazo mientras otro alumno navega el catálogo** (lo que dispara el vencimiento perezoso) → se acepta, porque el vencimiento perezoso también respeta la gracia. Test: `ComprobanteTests.test_el_vencimiento_perezoso_respeta_la_gracia` (Tarea 2).
3. **El staff edita el nombre de una variante mientras hay unidades reservadas** → el stock disponible no cambia. Test: `ProductoFormTests.test_editar_el_nombre_no_pisa_el_stock_reservado` (Tarea 4).
4. **Se apaga el add-on con pedidos pagados sin entregar** → el staff todavía los ve y los entrega; el alumno ve «Mis pedidos». Test: `TiendaApagadaTests` (Tareas 5 y 7).
5. **Una sesión vieja trae en el carrito la variante de un producto archivado o de otro gimnasio** → se descarta en silencio al leer el carrito y nunca llega a reservar. Test: `CarritoTests.test_lineas_descarta_variantes_ajenas_y_archivadas` (Tarea 2).
6. **El alumno resube el comprobante rechazado cuando el plazo nuevo ya venció** → se rechaza la subida y el comprobante anterior sigue en R2 y en el pedido (es lo único que el staff tenía para mirar). Test: `ReintentoDelComprobanteTests.test_resubir_tarde_no_borra_el_comprobante_anterior` (Tarea 3).
7. **El alumno retira el pedido dos semanas después de pagarlo** → todavía puede arrepentirse durante 10 días desde el retiro (art. 34: lo último entre contrato y entrega). Tests: `CancelacionTests.test_el_plazo_se_cuenta_desde_el_retiro` y `test_el_filtro_en_la_base_coincide_con_la_regla` (Tarea 3).
8. **Una venta pagada el último día del mes a las 22:30** → cuenta en ese mes, no en el siguiente (UTC ya es el día 1). Test: `VentasPorMesTests.test_pagado_a_las_2230_del_ultimo_dia_cae_en_ese_mes` (Tarea 6).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `tienda/models.py` | `ConfiguracionTienda`, `CategoriaProducto`, `Producto`, `Variante`, `Pedido`, `ItemPedido` |
| `tienda/services.py` | toda regla de negocio: carrito, reserva, transiciones, vencimiento, ajuste de stock, contadores |
| `tienda/analitica.py` | indicadores de ventas para el dashboard y «Vendido este mes» (molde: `tenants/analitica.py`) |
| `tienda/forms.py` | forms de staff (configuración, categoría, producto + formset de variantes, ajuste, motivo) y de alumno (comprobante) |
| `tienda/fotos.py` | validar y reescalar la foto del producto |
| `tienda/views.py` | vistas de staff, de alumno y la de la foto |
| `tienda/urls.py` | `app_name = "tienda"` |
| `tienda/admin.py` | registro en `/admin/` |
| `tienda/management/commands/vencer_pedidos.py` | respaldo del vencimiento perezoso + avisos |
| `tienda/tests_modelos.py`, `tests_services.py`, `tests_analitica.py`, `tests_vistas_staff.py`, `tests_vistas_alumno.py`, `tests_comandos.py` | tests por capa |
| `templates/tienda/*.html` | pantallas |
| Modificados | `config/settings.py`, `config/urls.py`, `tenants/models.py` (+migración), `tenants/admin.py`, `tenants/demo.py`, `tenants/exportacion.py`, `tenants/tests.py`, `tenants/tests_exportacion.py`, `tenants/views.py` (panel + portal), `templates/base.html`, `templates/tenants/home.html`, `plataforma/views.py`, `plataforma/urls.py`, `plataforma/middleware.py`, `templates/plataforma/gimnasio_detalle.html`, `notificaciones/models.py` (+migración), `notificaciones/services.py`, `.github/workflows/enviar-recordatorios.yml`, `tenants/management/commands/sembrar_demo.py`, `templates/tenants/privacidad.html`, docs |

---

### Task 1: Modelos, migraciones, admin y los TRES lugares

**Files:**
- Create: `tienda/__init__.py`, `tienda/apps.py`, `tienda/models.py`, `tienda/admin.py`, `tienda/migrations/0001_initial.py` (generada), `tienda/tests_modelos.py`
- Modify: `config/settings.py` (INSTALLED_APPS + comentario), `tenants/models.py` (campo + property), `tenants/migrations/0015_gimnasio_tienda_habilitada.py` (generada), `tenants/admin.py:10-12`, `tenants/demo.py` (`borrar_demo` y `vaciar_gimnasio`), `tenants/tests.py` (`_ensuciar` + test de form), `tenants/exportacion.py` (HOJAS, LEEME), `tenants/tests_exportacion.py`

**Interfaces:**
- Produces: modelos `tienda.models.{ConfiguracionTienda, CategoriaProducto, Producto, Variante, Pedido, ItemPedido}`; `Pedido.Estado` (`PENDIENTE_PAGO="pendiente_pago"`, `EN_REVISION="en_revision"`, `PAGADO="pagado"`, `ENTREGADO="entregado"`, `VENCIDO="vencido"`, `CANCELADO="cancelado"`, `DEVUELTO="devuelto"`); `Pedido.Medio.TRANSFERENCIA`; constantes `ESTADOS_QUE_RETIENEN`, `ESTADOS_EN_CURSO`; `Gimnasio.tienda_habilitada` y `Gimnasio.puede_usar_tienda`; `Producto.foto_url_cacheable`.

- [ ] **Step 1: Esqueleto de la app y registro**

```bash
mkdir -p tienda/migrations tienda/management/commands templates/tienda
touch tienda/__init__.py tienda/migrations/__init__.py tienda/management/__init__.py tienda/management/commands/__init__.py
```

`tienda/apps.py`:

```python
from django.apps import AppConfig


class TiendaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tienda"
    verbose_name = "Tienda"
```

En `config/settings.py`, agregar `'tienda',` en `INSTALLED_APPS` **inmediatamente después de `'suscripcion',`**, cambiar en el párrafo de `suscripcion` «va última de todas» por «va antes de `tienda`», y sumar debajo:

```python
    #   tienda     -> add-on de venta de productos a los alumnos (Producto,
    #                 Variante, Pedido). Lee `pagos.MedioCobro` (datos de
    #                 transferencia) y, desde el Hito 2, el cliente de
    #                 `suscripcion/mercadopago.py`, así que va después de las dos.
    #                 `tenants.views`, `tenants.demo` y `plataforma.views` la leen
    #                 con import tardío (dentro de la función).
```

(Verificar con `grep -n "'suscripcion'" config/settings.py` dónde está la línea; el default de `DEFAULT_AUTO_FIELD` del proyecto manda si `apps.py` de otras apps no lo declara — copiar exactamente lo que hace `turnos/apps.py`.)

- [ ] **Step 2: Tests de modelo que fallan** — `tienda/tests_modelos.py`

```python
"""Modelos de la tienda: restricciones que tiene que hacer cumplir la base,
no la vista."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
)


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("25000")
        )
        self.variante = Variante.objects.create(producto=self.producto, stock=3)


class VarianteTests(_Base):
    def test_el_stock_no_puede_quedar_negativo_en_la_base(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variante.objects.filter(pk=self.variante.pk).update(stock=-1)

    def test_nombre_unico_por_producto(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variante.objects.create(producto=self.producto, stock=1)

    def test_str_sin_variantes_es_el_producto(self):
        self.assertEqual(str(self.variante), "Proteína")
        talle = Variante.objects.create(producto=self.producto, nombre="M", stock=1)
        self.assertEqual(str(talle), "Proteína · M")


class ItemPedidoTests(_Base):
    def test_la_cantidad_cero_la_rechaza_la_base(self):
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, total=Decimal("0"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ItemPedido.objects.create(
                pedido=pedido, variante=self.variante, nombre="x",
                precio_unitario=Decimal("1"), cantidad=0,
            )

    def test_la_variante_vendida_no_se_puede_borrar(self):
        from django.db.models import ProtectedError

        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(gimnasio=self.gimnasio, alumno=alumno, total=1)
        ItemPedido.objects.create(
            pedido=pedido, variante=self.variante, nombre="x",
            precio_unitario=Decimal("1"), cantidad=1,
        )
        with self.assertRaises(ProtectedError):
            self.producto.delete()


class PedidoTests(_Base):
    def test_clean_rechaza_un_alumno_de_otro_gimnasio(self):
        ajeno = Alumno.objects.create(gimnasio=self.otro, nombre="Bruno", apellido="P")
        pedido = Pedido(gimnasio=self.gimnasio, alumno=ajeno, total=1)
        with self.assertRaises(ValidationError):
            pedido.clean()

    def test_estados_que_retienen(self):
        from tienda.models import ESTADOS_QUE_RETIENEN

        self.assertEqual(
            set(ESTADOS_QUE_RETIENEN),
            {Pedido.Estado.PENDIENTE_PAGO, Pedido.Estado.EN_REVISION, Pedido.Estado.PAGADO},
        )


class ConfiguracionTests(_Base):
    def test_obtener_configuracion_crea_una_sola(self):
        a = obtener_configuracion(self.gimnasio)
        b = obtener_configuracion(self.gimnasio)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(a.horas_reserva_transferencia, 24)
        self.assertEqual(a.max_pedidos_activos, 2)
        self.assertFalse(a.publicada)

    def test_una_por_gimnasio(self):
        obtener_configuracion(self.gimnasio)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionTienda.objects.create(gimnasio=self.gimnasio)


class CategoriaTests(_Base):
    def test_borrar_la_categoria_deja_el_producto_sin_categoria(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.producto.categoria = cat
        self.producto.save()
        cat.delete()
        self.producto.refresh_from_db()
        self.assertIsNone(self.producto.categoria)


class AislamientoTests(_Base):
    """Un test de aislamiento por modelo tenant-owned (regla de `CLAUDE.md`)."""

    def test_producto(self):
        Producto.objects.create(gimnasio=self.otro, nombre="Ajeno", precio=1)
        self.assertEqual(list(Producto.objects.for_gimnasio(self.gimnasio)), [self.producto])

    def test_categoria(self):
        propia = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        CategoriaProducto.objects.create(gimnasio=self.otro, nombre="Ropa")
        self.assertEqual(list(CategoriaProducto.objects.for_gimnasio(self.gimnasio)), [propia])

    def test_configuracion(self):
        propia = obtener_configuracion(self.gimnasio)
        obtener_configuracion(self.otro)
        self.assertEqual(list(ConfiguracionTienda.objects.for_gimnasio(self.gimnasio)), [propia])

    def test_pedido(self):
        ana = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        bruno = Alumno.objects.create(gimnasio=self.otro, nombre="Bruno", apellido="P")
        propio = Pedido.objects.create(gimnasio=self.gimnasio, alumno=ana, total=1)
        Pedido.objects.create(gimnasio=self.otro, alumno=bruno, total=1)
        self.assertEqual(list(Pedido.objects.for_gimnasio(self.gimnasio)), [propio])


class GimnasioTiendaTests(TestCase):
    def test_puede_usar_tienda(self):
        real = Gimnasio.objects.create(nombre="R", slug="r")
        demo = Gimnasio.objects.create(nombre="D", slug="d", es_demo=True)
        self.assertFalse(real.puede_usar_tienda)
        self.assertTrue(demo.puede_usar_tienda)
        real.tienda_habilitada = True
        self.assertTrue(real.puede_usar_tienda)
```

Run: `$PY manage.py test tienda.tests_modelos -v 2`
Expected: ERROR (`ModuleNotFoundError: tienda.models` o `cannot import`).

- [ ] **Step 3: Modelos** — `tienda/models.py`

```python
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
```

(`condition=` y no `check=`: `check` está deprecado desde Django 5.1 y avisa `RemovedInDjango60Warning`; `plataforma/models.py` ya usa `condition=`.)

- [ ] **Step 4: Campo en `Gimnasio`** — en `tenants/models.py`, debajo de `exportacion_ultima_descarga`:

```python
    tienda_habilitada = models.BooleanField(
        default=False,
        verbose_name="tienda habilitada",
        help_text=(
            "Add-on pago: deja que el gimnasio venda productos a sus alumnos. "
            "Lo prende el dueño del producto desde el panel de plataforma. La "
            "cuenta de demostración la tiene siempre."
        ),
    )
```

y debajo de `puede_exportar`:

```python
    @property
    def puede_usar_tienda(self):
        """Único lugar donde vive la regla de quién tiene la tienda: la demo
        siempre (es lo que se muestra para vender), el resto con el add-on."""
        return self.es_demo or self.tienda_habilitada
```

En `tenants/admin.py`, sumar `"tienda_habilitada"` a `list_display` y `list_filter` junto a `exportacion_habilitada`.

- [ ] **Step 5: Admin de la tienda** — `tienda/admin.py`

```python
from django.contrib import admin

from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
)


class VarianteInline(admin.TabularInline):
    model = Variante
    extra = 0
    # Stock disponible: escribirlo en absoluto pisa reservas (ver models.py).
    readonly_fields = ("stock",)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "precio", "publicado")
    list_filter = ("gimnasio", "publicado")
    inlines = [VarianteInline]


class ItemPedidoInline(admin.TabularInline):
    model = ItemPedido
    extra = 0
    readonly_fields = ("variante", "nombre", "precio_unitario", "cantidad")


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "gimnasio", "alumno", "estado", "total", "creado")
    list_filter = ("gimnasio", "estado")
    raw_id_fields = ("alumno",)  # un select con los alumnos de TODA la plataforma no
    inlines = [ItemPedidoInline]


@admin.register(CategoriaProducto)
class CategoriaProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "orden")
    list_filter = ("gimnasio",)


@admin.register(ConfiguracionTienda)
class ConfiguracionTiendaAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "publicada", "horas_reserva_transferencia")
```

- [ ] **Step 6: Migraciones y tests de modelo**

```bash
$PY manage.py makemigrations tenants tienda
$PY manage.py test tienda.tests_modelos -v 2
```
Expected: migraciones `tenants/0015_gimnasio_tienda_habilitada.py` y `tienda/0001_initial.py`; tests PASS. `makemigrations` NO debe tocar ninguna otra app.

- [ ] **Step 7: Los TRES lugares — tests primero.** Correr:

```bash
$PY manage.py test tenants.tests.VaciarGimnasioTests tenants.tests_exportacion -v 2
```
Expected: FAIL en `test_ensuciar_cubre_todos_los_modelos_tenant_owned` (faltan `tienda.ConfiguracionTienda`, `tienda.CategoriaProducto`, `tienda.Producto`, `tienda.Pedido`) y en `test_todo_modelo_tenant_owned_esta_exportado_o_excluido_a_proposito`.

- [ ] **Step 8: `_ensuciar`** — en `tenants/tests.py`, dentro de `_ensuciar`, agregar el import `from tienda.models import CategoriaProducto, ConfiguracionTienda, ItemPedido, Pedido, Producto, Variante` y antes del `return alumno`:

```python
    # La tienda: el pedido protege a la variante (PROTECT) y al alumno, así
    # que ejercita el orden de borrado de `vaciar_gimnasio` y `borrar_demo`.
    ConfiguracionTienda.objects.create(gimnasio=gimnasio)
    categoria_tienda = CategoriaProducto.objects.create(gimnasio=gimnasio, nombre=f"Ropa {marca}")
    producto = Producto.objects.create(
        gimnasio=gimnasio, categoria=categoria_tienda,
        nombre=f"Remera {marca}", precio=Decimal("15000"),
    )
    variante = Variante.objects.create(producto=producto, nombre="M", stock=5)
    pedido = Pedido.objects.create(
        gimnasio=gimnasio, alumno=alumno, total=Decimal("15000"),
        reservado_hasta=timezone.now() + timedelta(hours=24),
    )
    ItemPedido.objects.create(
        pedido=pedido, variante=variante, nombre=f"Remera {marca} · M",
        precio_unitario=Decimal("15000"), cantidad=1,
    )
```

(Verificar que `Decimal` ya está importado en `tenants/tests.py`; si no, agregar `from decimal import Decimal` arriba.)

Y el test hermano del de exportación, que vive en `tenants/tests_exportacion.py:332-334` (`test_el_staff_no_puede_habilitarsela_desde_su_panel`): crear `tienda/tests_modelos.py::TiendaNoSeAutohabilitaTests` con el mismo par de aserciones (`Meta.fields` y `GimnasioForm().fields`) más un POST a `gimnasio_editar` con `tienda_habilitada=on` que no la cambia (molde: `tenants/tests.py:240-258`). Leer esos tests y copiar su forma exacta (qué datos mínimos postea el form).

- [ ] **Step 9: `vaciar_gimnasio` y `borrar_demo`** — en `tenants/demo.py`:

En `vaciar_gimnasio`, sumar el import `from tienda.models import CategoriaProducto, ConfiguracionTienda, Pedido, Producto` y, **antes de** `alumnos.delete()` (junto a `Cuota.objects.for_gimnasio(gimnasio).delete()`):

```python
        # PROTECT hacia `Alumno`. Los ítems caen en cascada con el pedido, y
        # recién sin ítems se pueden borrar los productos (ItemPedido.variante
        # es PROTECT).
        Pedido.objects.for_gimnasio(gimnasio).delete()
```

y en el bloque de catálogo (después de `CategoriaEjercicio...delete()`):

```python
        # Producto arrastra sus variantes; va antes que la categoría por
        # prolijidad (la FK es SET_NULL, no protege).
        Producto.objects.for_gimnasio(gimnasio).delete()
        CategoriaProducto.objects.for_gimnasio(gimnasio).delete()
```

y en «Sin relaciones entrantes»: `ConfiguracionTienda.objects.for_gimnasio(gimnasio).delete()`. Actualizar el párrafo del docstring sobre el orden: «`Pedido` protege a `Alumno` y, por sus ítems, a `Variante`».

En `borrar_demo`, junto a `Cuota.objects.filter(alumno__in=alumnos).delete()`:

```python
        Pedido.objects.filter(alumno__in=alumnos).delete()
```

(con el import local correspondiente en esa función, siguiendo cómo importa `Cuota` ahí).

- [ ] **Step 10: Exportación** — en `tenants/exportacion.py`, agregar al final de `HOJAS` (antes del paréntesis que cierra la tupla):

```python
    Hoja(
        "tienda_configuracion", "tienda.ConfiguracionTienda", "gimnasio",
        "Configuración de la tienda (plazo para el comprobante, tope de pedidos).",
        (
            ("publicada", "publicada"),
            ("horas_reserva_transferencia", "horas_reserva_transferencia"),
            ("max_pedidos_activos", "max_pedidos_activos"),
            ("instrucciones_retiro", "instrucciones_retiro"),
        ),
    ),
    Hoja(
        "tienda_categorias", "tienda.CategoriaProducto", "gimnasio",
        "Categorías de la tienda.",
        (("id", "id"), ("nombre", "nombre"), ("orden", "orden")),
    ),
    Hoja(
        "tienda_productos", "tienda.Producto", "gimnasio",
        "Productos de la tienda. `foto_archivo` es el nombre del archivo; las "
        "fotos en sí no van en esta exportación.",
        (
            ("id", "id"), ("categoria_id", "categoria_id"), ("nombre", "nombre"),
            ("descripcion", "descripcion"), ("precio", "precio"),
            ("publicado", "publicado"), ("foto_archivo", "foto"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "tienda_variantes", "tienda.Variante", "producto__gimnasio",
        "Variantes (talle, color, sabor) de cada producto, con su stock disponible.",
        (
            ("id", "id"), ("producto_id", "producto_id"), ("nombre", "nombre"),
            ("stock_disponible", "stock"), ("activa", "activa"),
        ),
    ),
    Hoja(
        "tienda_pedidos", "tienda.Pedido", "gimnasio",
        "Pedidos de los alumnos. `comprobante_archivo` es el nombre del archivo subido.",
        (
            ("id", "id"), ("alumno_id", "alumno_id"),
            ("alumno_apellido", "alumno__apellido"), ("alumno_nombre", "alumno__nombre"),
            ("medio", "medio"), ("estado", "estado"), ("total", "total"),
            ("comprobante_archivo", "comprobante"), ("pagado_en", "pagado_en"),
            ("entregado_en", "entregado_en"), ("motivo", "motivo"), ("creado", "creado"),
        ),
    ),
    Hoja(
        "tienda_pedidos_items", "tienda.ItemPedido", "pedido__gimnasio",
        "Qué se compró en cada pedido, con el precio de ese momento.",
        (
            ("id", "id"), ("pedido_id", "pedido_id"), ("variante_id", "variante_id"),
            ("nombre", "nombre"), ("precio_unitario", "precio_unitario"),
            ("cantidad", "cantidad"),
        ),
    ),
```

Ojo: si las columnas de fecha/hora (`pagado_en`) o decimales necesitan formateo especial, revisar cómo `exportacion.py` trata `creado` y `monto` y no tocar nada más: debería funcionar con los mismos tipos. Cambiar la línea del LEEME:

```python
        "  - Los archivos subidos (comprobantes de pago, logo, fotos de productos): figura su nombre.",
```

En `tenants/tests_exportacion.py::test_no_trae_nada_de_otro_gimnasio`, agregar (números fijos, uno por gimnasio):

```python
        self.assertEqual(len(_filas(zf, "para-importar/tienda_variantes.csv", ",")), 1)
        self.assertEqual(len(_filas(zf, "para-importar/tienda_pedidos_items.csv", ",")), 1)
```

- [ ] **Step 11: Correr y verificar que los tests de cobertura detectan el olvido.** Correr `$PY manage.py test tenants tienda -v 1` → PASS. Después, **mutar a mano**: comentar la línea `Pedido.objects.for_gimnasio(gimnasio).delete()` de `vaciar_gimnasio`, correr `$PY manage.py test tenants.tests.VaciarGimnasioTests` → debe FALLAR (ProtectedError o restos); restaurar. Comentar la línea de `borrar_demo` y correr `$PY manage.py test tenants` → si ningún test falla, agregar a la clase de tests de `borrar_demo` (buscar `def test_` que llame `borrar_demo`) un test que siembre un `Pedido` para un alumno con `MARCA` y verifique que `borrar_demo` no levanta `ProtectedError`. Restaurar.

- [ ] **Step 12: Suite completa y commit**

```bash
$PY manage.py makemigrations --check --dry-run
$PY manage.py test --parallel 4 2>&1 | grep -E "^Ran|^OK|FAILED"
git status --short
git add tienda config/settings.py tenants templates/tienda 2>/dev/null; git add tienda config/settings.py tenants
git commit -m "feat(tienda): modelos, migraciones y los tres lugares"
```
Expected: `No changes detected`; `OK` con más de 2673 tests.

---

### Task 2: Carrito, reserva de stock y vencimiento

**Files:**
- Create: `tienda/services.py`, `tienda/testing.py`, `tienda/tests_services.py`

**Interfaces:**
- Consumes: modelos de la Tarea 1.
- Produces (firmas exactas, las usan las Tareas 3-9):
  - `class ErrorDeTienda(Exception)` con `.motivo: str`, `.mensaje: str`; `MENSAJES: dict[str, str]` (completo para todo el hito: la Tarea 3 no agrega claves).
  - `MAX_UNIDADES_POR_ITEM = 20`, `GRACIA_COMPROBANTE = timedelta(minutes=2)`, `DIAS_ARREPENTIMIENTO = 10`, `CLAVE_CARRITO = "tienda_carrito"`, `UMBRAL_POCAS_UNIDADES = 3`.
  - `tienda_visible_para_alumnos(gimnasio) -> bool`
  - `leer_carrito(session) -> dict[int, int]`; `agregar_al_carrito(session, *, gimnasio, variante_id: int, cantidad: int) -> None`; `quitar_del_carrito(session, variante_id: int) -> None`; `vaciar_carrito(session) -> None`; `lineas_del_carrito(session, *, gimnasio) -> tuple[list[Linea], Decimal]` donde `Linea = namedtuple("Linea", "variante cantidad subtotal")`.
  - `crear_pedido(*, gimnasio, alumno, carrito: dict[int, int], suplantando: bool, ahora=None) -> Pedido`
  - `vencer_pedidos(*, gimnasio=None, ahora=None) -> list[int]`
  - `subir_comprobante(pedido, *, alumno, archivo, ahora=None) -> None` (la Tarea 3 la reemplaza por la versión que soporta el reintento; misma firma)
  - `ajustar_stock(variante, delta: int) -> None`
  - `reservado_por_variante(gimnasio) -> dict[int, int]`
  - Helpers internos que usa la Tarea 3: `_ahora`, `_devolver_stock(pedido_ids)`, `_E = Pedido.Estado`.
  - En los tests: `_Base` con `comprar(carrito, alumno=None, ahora=AHORA)`, `stock(variante)` y `subir(pedido, *, ahora=AHORA, alumno=None)`.

- [ ] **Step 1: Helper de tests** — `tienda/testing.py` (molde: `pagos/testing.py`):

```python
"""Helpers compartidos por los tests de la tienda."""

import io

from PIL import Image


def _png():
    """PNG de 1x1 generado por Pillow: uno escrito a mano en bytes falla el
    `verify()` del form del comprobante."""
    salida = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(salida, "PNG")
    return salida.getvalue()


PNG = _png()
```

- [ ] **Step 1b: Tests que fallan** — `tienda/tests_services.py` (escribir completo; la Tarea 3 le agrega clases al final):

```python
"""Reglas de negocio de la tienda. Los nombres dicen el comportamiento; cada
test de regresión se confirmó rojo sin su arreglo (ver el plan)."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda import services
from tienda.models import ItemPedido, Pedido, Producto, Variante, obtener_configuracion
from tienda.services import ErrorDeTienda
from tienda.testing import PNG

AHORA = timezone.make_aware(datetime(2026, 10, 20, 12, 0))



class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.alumno2 = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        self.m = Variante.objects.create(producto=self.producto, nombre="M", stock=3)
        self.l = Variante.objects.create(producto=self.producto, nombre="L", stock=1)
        ajeno = Producto.objects.create(gimnasio=self.otro, nombre="Ajena", precio=1)
        self.ajena = Variante.objects.create(producto=ajeno, stock=10)

    def comprar(self, carrito, alumno=None, ahora=AHORA):
        return services.crear_pedido(
            gimnasio=self.gimnasio, alumno=alumno or self.alumno,
            carrito=carrito, suplantando=False, ahora=ahora,
        )

    def stock(self, variante):
        variante.refresh_from_db()
        return variante.stock

    def subir(self, pedido, *, ahora=AHORA, alumno=None):
        services.subir_comprobante(
            pedido, alumno=alumno or self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"), ahora=ahora,
        )


class CarritoTests(_Base):
    def setUp(self):
        super().setUp()
        self.session = SessionStore()

    def test_agregar_suma_y_respeta_el_tope(self):
        services.agregar_al_carrito(self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=2)
        services.agregar_al_carrito(self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=1)
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 3})
        with self.assertRaises(ErrorDeTienda) as e:
            services.agregar_al_carrito(
                self.session, gimnasio=self.gimnasio, variante_id=self.m.pk,
                cantidad=services.MAX_UNIDADES_POR_ITEM,
            )
        self.assertEqual(e.exception.motivo, "cantidad_invalida")

    def test_cantidad_cero_o_negativa_se_rechaza(self):
        for cantidad in (0, -5):
            with self.assertRaises(ErrorDeTienda):
                services.agregar_al_carrito(
                    self.session, gimnasio=self.gimnasio, variante_id=self.m.pk, cantidad=cantidad
                )
        self.assertEqual(services.leer_carrito(self.session), {})

    def test_no_se_agrega_la_variante_de_otro_gimnasio(self):
        with self.assertRaises(ErrorDeTienda) as e:
            services.agregar_al_carrito(
                self.session, gimnasio=self.gimnasio, variante_id=self.ajena.pk, cantidad=1
            )
        self.assertEqual(e.exception.motivo, "producto_no_disponible")

    def test_leer_tolera_basura_en_la_sesion(self):
        self.session[services.CLAVE_CARRITO] = {"x": "y", str(self.m.pk): "2", "9": -3}
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 2})

    def test_lineas_descarta_variantes_ajenas_y_archivadas(self):
        self.l.activa = False
        self.l.save()
        self.session[services.CLAVE_CARRITO] = {
            str(self.m.pk): 2, str(self.l.pk): 1, str(self.ajena.pk): 1,
        }
        lineas, total = services.lineas_del_carrito(self.session, gimnasio=self.gimnasio)
        self.assertEqual([l.variante.pk for l in lineas], [self.m.pk])
        self.assertEqual(total, Decimal("30000"))
        self.assertEqual(services.leer_carrito(self.session), {self.m.pk: 2})


class CrearPedidoTests(_Base):
    def test_reserva_descuenta_stock_y_guarda_snapshot(self):
        pedido = self.comprar({self.m.pk: 2, self.l.pk: 1})
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)
        self.assertEqual(pedido.total, Decimal("45000"))
        self.assertEqual(pedido.reservado_hasta, AHORA + timedelta(hours=24))
        self.assertEqual((self.stock(self.m), self.stock(self.l)), (1, 0))
        nombres = sorted(pedido.items.values_list("nombre", "cantidad"))
        self.assertEqual(nombres, [("Remera · L", 1), ("Remera · M", 2)])

    def test_la_segunda_compra_de_la_ultima_unidad_falla_sin_tocar_nada(self):
        self.comprar({self.l.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1, self.l.pk: 1}, alumno=self.alumno2)
        self.assertEqual(e.exception.motivo, "sin_stock")
        self.assertIn("Remera · L", e.exception.mensaje)
        # Todo o nada: la M no quedó descontada por la compra fallida.
        self.assertEqual(self.stock(self.m), 3)
        self.assertEqual(Pedido.objects.filter(alumno=self.alumno2).count(), 0)

    def test_variante_de_otro_gimnasio_no_toca_su_stock(self):
        with self.assertRaises(ErrorDeTienda):
            self.comprar({self.ajena.pk: 1})
        self.assertEqual(self.stock(self.ajena), 10)

    def test_cantidad_negativa_no_suma_stock(self):
        with self.assertRaises(ErrorDeTienda):
            self.comprar({self.m.pk: -5})
        self.assertEqual(self.stock(self.m), 3)

    def test_carrito_vacio(self):
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({})
        self.assertEqual(e.exception.motivo, "carrito_vacio")

    def test_producto_despublicado_no_se_vende(self):
        self.producto.publicado = False
        self.producto.save()
        with self.assertRaises(ErrorDeTienda):
            self.comprar({self.m.pk: 1})
        self.assertEqual(self.stock(self.m), 3)

    def test_tope_de_pedidos_en_curso(self):
        obtener_configuracion(self.gimnasio)  # default 2
        self.comprar({self.m.pk: 1})
        self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1})
        self.assertEqual(e.exception.motivo, "tope_pedidos")

    def test_no_se_compra_suplantando(self):
        with self.assertRaises(ErrorDeTienda) as e:
            services.crear_pedido(
                gimnasio=self.gimnasio, alumno=self.alumno,
                carrito={self.m.pk: 1}, suplantando=True, ahora=AHORA,
            )
        self.assertEqual(e.exception.motivo, "suplantando")
        self.assertEqual(self.stock(self.m), 3)

    def test_tienda_sin_add_on(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        self.gimnasio.refresh_from_db()
        with self.assertRaises(ErrorDeTienda) as e:
            self.comprar({self.m.pk: 1})
        self.assertEqual(e.exception.motivo, "tienda_cerrada")


class VencimientoTests(_Base):
    def test_vence_despues_de_la_gracia_y_devuelve_el_stock(self):
        pedido = self.comprar({self.m.pk: 2})
        limite = pedido.reservado_hasta + services.GRACIA_COMPROBANTE
        self.assertEqual(services.vencer_pedidos(ahora=limite), [])
        self.assertEqual(services.vencer_pedidos(ahora=limite + timedelta(seconds=1)), [pedido.pk])
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.VENCIDO)
        self.assertEqual(self.stock(self.m), 3)

    def test_vencer_dos_veces_no_devuelve_dos_veces(self):
        self.comprar({self.m.pk: 2})
        despues = AHORA + timedelta(days=2)
        services.vencer_pedidos(ahora=despues)
        services.vencer_pedidos(ahora=despues)
        self.assertEqual(self.stock(self.m), 3)

    def test_en_revision_no_vence(self):
        pedido = self.comprar({self.m.pk: 1})
        services.subir_comprobante(
            pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"), ahora=AHORA,
        )
        services.vencer_pedidos(ahora=AHORA + timedelta(days=30))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_acotado_a_un_gimnasio(self):
        pedido = self.comprar({self.m.pk: 1})
        self.assertEqual(services.vencer_pedidos(gimnasio=self.otro, ahora=AHORA + timedelta(days=2)), [])
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_si_otro_proceso_movio_un_pedido_no_libera_su_stock(self):
        """La carrera de SQLite (donde `select_for_update` no hace nada): el
        SELECT vio dos pedidos pendientes, pero antes del UPDATE el alumno de
        `b` subió el comprobante (commiteado por otro proceso). Se simula con
        un SELECT viejo; un UPDATE dentro del savepoint se desharía con el
        rollback de `_Carrera` y el test no probaría nada."""
        a = self.comprar({self.m.pk: 1})
        b = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=b.pk).update(estado=Pedido.Estado.EN_REVISION)
        with patch.object(services, "_candidatos_a_vencer", lambda *args, **kwargs: [a.pk, b.pk]):
            vencidos = services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        self.assertEqual(vencidos, [a.pk])
        self.assertEqual(self.stock(self.m), 2)  # 3 - 2 + 1 (sólo el de `a`)

    def test_el_costo_no_crece_con_la_cantidad_de_pedidos(self):
        Variante.objects.filter(pk=self.m.pk).update(stock=100)
        self.comprar({self.m.pk: 1})
        with CaptureQueriesContext(connection) as uno:
            services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        config = obtener_configuracion(self.gimnasio)
        config.max_pedidos_activos = 10
        config.save()
        for _ in range(8):
            self.comprar({self.m.pk: 1})
        with CaptureQueriesContext(connection) as ocho:
            services.vencer_pedidos(ahora=AHORA + timedelta(days=2))
        self.assertEqual(len(uno), len(ocho))


class ComprobanteTests(_Base):
    def archivo(self):
        return SimpleUploadedFile("c.png", PNG, content_type="image/png")

    def test_pasa_a_revision(self):
        pedido = self.comprar({self.m.pk: 1})
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)
        self.assertTrue(pedido.comprobante.name.startswith("tienda/comprobantes/"))
        self.assertEqual(pedido.comprobante_subido_en, AHORA)

    def test_dentro_de_la_gracia_se_acepta(self):
        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=1)
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_el_vencimiento_perezoso_respeta_la_gracia(self):
        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=1)
        services.vencer_pedidos(ahora=tarde)  # otro alumno abrió el catálogo
        services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)

    def test_fuera_de_plazo_se_rechaza_y_no_deja_el_archivo(self):
        from django.core.files.storage import default_storage

        pedido = self.comprar({self.m.pk: 1})
        tarde = pedido.reservado_hasta + timedelta(minutes=3)
        with patch.object(default_storage, "delete", wraps=default_storage.delete) as borrar:
            with self.assertRaises(ErrorDeTienda) as e:
                services.subir_comprobante(pedido, alumno=self.alumno, archivo=self.archivo(), ahora=tarde)
        self.assertEqual(e.exception.motivo, "plazo_vencido")
        borrar.assert_called_once()

    def test_otro_alumno_no_sube(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda):
            services.subir_comprobante(pedido, alumno=self.alumno2, archivo=self.archivo(), ahora=AHORA)


class StockTests(_Base):
    def test_ajuste_positivo_y_negativo(self):
        services.ajustar_stock(self.m, 5)
        self.assertEqual(self.stock(self.m), 8)
        services.ajustar_stock(self.m, -8)
        self.assertEqual(self.stock(self.m), 0)
        with self.assertRaises(ErrorDeTienda):
            services.ajustar_stock(self.m, -1)

    def test_ajuste_concurrente_con_una_reserva_no_pisa(self):
        self.comprar({self.m.pk: 2})  # el staff había visto 3
        services.ajustar_stock(self.m, 1)
        self.assertEqual(self.stock(self.m), 2)

    def test_reservado_por_variante(self):
        self.comprar({self.m.pk: 2})
        pagado = self.comprar({self.l.pk: 1}, alumno=self.alumno2)
        # `marcar_pagado` llega en la Tarea 3: acá alcanza con el estado.
        Pedido.objects.filter(pk=pagado.pk).update(estado=Pedido.Estado.PAGADO)
        self.assertEqual(services.reservado_por_variante(self.gimnasio), {self.m.pk: 2, self.l.pk: 1})


class MensajesTests(TestCase):
    def test_cada_motivo_tiene_su_mensaje(self):
        import inspect

        fuente = inspect.getsource(services)
        import re

        usados = set(re.findall(r'ErrorDeTienda\("(\w+)"', fuente))
        self.assertTrue(usados)
        self.assertEqual(usados - set(services.MENSAJES), set())
```

Run: `$PY manage.py test tienda.tests_services -v 2`
Expected: ERROR (`ImportError: cannot import name 'services'`).

- [ ] **Step 2: Implementación** — `tienda/services.py` (primera parte; la Tarea 3 agrega las transiciones al final y cambia el import de `django.db.models` a `Count, F, Q, Sum`):

```python
"""Reglas de negocio de la tienda.

Tres decisiones que sostienen todo el archivo (ver la spec):

1. **El stock nunca se lee-y-resta.** Se descuenta con un UPDATE condicional
   (`stock__gte=n`) y se mira cuántas filas tocó.
2. **Toda transición es `filter(pk, estado__in=ORIGEN).update(...)`** y el
   stock se devuelve sólo si tocó una fila. Así no hay doble devolución ni en
   Postgres ni en SQLite (donde `select_for_update` no hace nada).
3. **El camino perezoso no toca la red ni manda push.** `vencer_pedidos` corre
   dentro de GETs; los avisos los manda el cron.
"""

from collections import namedtuple
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from tienda.models import (
    ESTADOS_EN_CURSO,
    ESTADOS_QUE_RETIENEN,
    MAX_LARGO_MOTIVO,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Variante,
    obtener_configuracion,
)

MAX_UNIDADES_POR_ITEM = 20
GRACIA_COMPROBANTE = timedelta(minutes=2)
DIAS_ARREPENTIMIENTO = 10
CLAVE_CARRITO = "tienda_carrito"
#: «Quedan N» en el catálogo y «Stock bajo» en la lista del staff.
UMBRAL_POCAS_UNIDADES = 3

_E = Pedido.Estado

Linea = namedtuple("Linea", "variante cantidad subtotal")


class ErrorDeTienda(Exception):
    """Algo que el usuario tiene que leer. `motivo` es una clave de
    `MENSAJES`; `detalle` completa el mensaje (p. ej. el producto sin stock)."""

    def __init__(self, motivo, detalle=""):
        self.motivo = motivo
        self.detalle = detalle
        super().__init__(motivo)

    @property
    def mensaje(self):
        return MENSAJES[self.motivo].format(detalle=self.detalle)


MENSAJES = {
    "tienda_cerrada": "La tienda no está disponible en este momento.",
    "suplantando": "Mientras estás viendo la app como un alumno no se puede comprar en su nombre.",
    "carrito_vacio": "Tu carrito está vacío.",
    "cantidad_invalida": f"Podés llevar entre 1 y {MAX_UNIDADES_POR_ITEM} unidades de cada producto.",
    "producto_no_disponible": "Ese producto ya no está disponible.",
    "sin_stock": "No queda stock suficiente de {detalle}. Ajustá la cantidad y probá de nuevo.",
    "tope_pedidos": (
        "Ya tenés pedidos esperando el pago. Pagalos o cancelalos antes de hacer otro."
    ),
    "plazo_vencido": (
        "El plazo para mandar el comprobante venció: el pedido se da de baja y el stock "
        "vuelve a la tienda."
    ),
    "estado_invalido": "Ese pedido ya cambió de estado. Recargá la pantalla.",
    "no_es_tuyo": "Ese pedido no es tuyo.",
    "fuera_de_plazo_arrepentimiento": (
        f"El plazo para pedir la cancelación es de {DIAS_ARREPENTIMIENTO} días desde que "
        "retiraste el pedido."
    ),
    "motivo_largo": f"El motivo puede tener hasta {MAX_LARGO_MOTIVO} caracteres.",
    "stock_negativo": "No hay tantas unidades disponibles para descontar.",
}


def tienda_visible_para_alumnos(gimnasio):
    # `exists()` y no `obtener_configuracion`: lo llama el portal en cada GET y
    # no tiene por qué hacer un INSERT.
    return gimnasio.puede_usar_tienda and ConfiguracionTienda.objects.filter(
        gimnasio=gimnasio, publicada=True
    ).exists()


def _ahora(ahora):
    return ahora or timezone.now()


def _variantes_vendibles(gimnasio):
    return Variante.objects.filter(
        producto__gimnasio=gimnasio, activa=True, producto__publicado=True
    )


# --- Carrito (en la sesión, sin reservar) ---------------------------------


def leer_carrito(session):
    """`{variante_id: cantidad}` saneado: la sesión es del cliente y puede
    traer cualquier cosa."""
    crudo = session.get(CLAVE_CARRITO) or {}
    limpio = {}
    if not isinstance(crudo, dict):
        return limpio
    for clave, valor in crudo.items():
        try:
            variante_id, cantidad = int(clave), int(valor)
        except (TypeError, ValueError):
            continue
        if 1 <= cantidad <= MAX_UNIDADES_POR_ITEM:
            limpio[variante_id] = cantidad
    return limpio


def _guardar_carrito(session, carrito):
    session[CLAVE_CARRITO] = {str(k): v for k, v in carrito.items()}
    session.modified = True


def agregar_al_carrito(session, *, gimnasio, variante_id, cantidad):
    if not _variantes_vendibles(gimnasio).filter(pk=variante_id).exists():
        raise ErrorDeTienda("producto_no_disponible")
    carrito = leer_carrito(session)
    nueva = carrito.get(variante_id, 0) + cantidad
    if cantidad < 1 or nueva > MAX_UNIDADES_POR_ITEM:
        raise ErrorDeTienda("cantidad_invalida")
    carrito[variante_id] = nueva
    _guardar_carrito(session, carrito)


def quitar_del_carrito(session, variante_id):
    carrito = leer_carrito(session)
    carrito.pop(variante_id, None)
    _guardar_carrito(session, carrito)


def vaciar_carrito(session):
    session.pop(CLAVE_CARRITO, None)
    session.modified = True


def lineas_del_carrito(session, *, gimnasio):
    """Las líneas que se pueden comprar, en UNA query. Lo ajeno o archivado se
    descarta en silencio (sesión vieja)."""
    carrito = leer_carrito(session)
    variantes = (
        _variantes_vendibles(gimnasio)
        .filter(pk__in=carrito)
        .select_related("producto")
        .order_by("producto__nombre", "pk")
    )
    lineas = [
        Linea(v, carrito[v.pk], v.producto.precio * carrito[v.pk]) for v in variantes
    ]
    if len(lineas) != len(carrito):
        # Se saca de la sesión lo que ya no se puede comprar: si no, el alumno
        # no lo ve (no está en las líneas), no lo puede quitar y el checkout
        # falla para siempre con «producto no disponible».
        _guardar_carrito(session, {l.variante.pk: l.cantidad for l in lineas})
    return lineas, sum((l.subtotal for l in lineas), Decimal("0"))


# --- Pedido ----------------------------------------------------------------


def crear_pedido(*, gimnasio, alumno, carrito, suplantando, ahora=None):
    ahora = _ahora(ahora)
    if not gimnasio.puede_usar_tienda:
        raise ErrorDeTienda("tienda_cerrada")
    if suplantando:
        raise ErrorDeTienda("suplantando")
    if not carrito:
        raise ErrorDeTienda("carrito_vacio")
    if any(not 1 <= n <= MAX_UNIDADES_POR_ITEM for n in carrito.values()):
        raise ErrorDeTienda("cantidad_invalida")
    config = obtener_configuracion(gimnasio)
    en_curso = Pedido.objects.for_gimnasio(gimnasio).filter(
        alumno=alumno, estado__in=ESTADOS_EN_CURSO
    ).count()
    if en_curso >= config.max_pedidos_activos:
        # Sin lock a propósito: un doble envío simultáneo puede pasar el tope
        # por uno. Es un freno contra acaparar, no una regla de plata.
        raise ErrorDeTienda("tope_pedidos")

    variantes = {
        v.pk: v
        for v in _variantes_vendibles(gimnasio)
        .filter(pk__in=carrito)
        .select_related("producto")
    }
    if len(variantes) != len(carrito):
        raise ErrorDeTienda("producto_no_disponible")

    try:
        return _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora)
    except IntegrityError:
        # Cinturón: la CHECK `stock >= 0` de la base. No debería pasar con el
        # UPDATE sin JOIN de abajo, pero un 500 en el checkout es lo peor.
        raise ErrorDeTienda("sin_stock", "uno de los productos")


def _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora):
    with transaction.atomic():
        # Orden por id: dos compras concurrentes toman los locks de fila en el
        # mismo orden y no se bloquean en cruz.
        for variante_id in sorted(carrito):
            cantidad = carrito[variante_id]
            # SIN JOIN a propósito: con `producto__...` en el filtro Django arma
            # `WHERE id IN (subquery)` y Postgres, después de esperar el lock
            # de fila, no re-evalúa `stock >= n` (sobreventa -> CHECK -> 500).
            # `variantes` ya viene acotado al gimnasio y a lo vendible.
            tocadas = Variante.objects.filter(
                pk=variante_id, stock__gte=cantidad
            ).update(stock=F("stock") - cantidad)
            if tocadas != 1:
                raise ErrorDeTienda("sin_stock", str(variantes[variante_id]))
        total = sum(
            (variantes[i].producto.precio * n for i, n in carrito.items()), Decimal("0")
        )
        pedido = Pedido.objects.create(
            gimnasio=gimnasio,
            alumno=alumno,
            total=total,
            reservado_hasta=ahora + timedelta(hours=config.horas_reserva_transferencia),
        )
        ItemPedido.objects.bulk_create(
            ItemPedido(
                pedido=pedido,
                variante=variantes[i],
                nombre=str(variantes[i])[:190],
                precio_unitario=variantes[i].producto.precio,
                cantidad=n,
            )
            for i, n in sorted(carrito.items())
        )
    return pedido


def _devolver_stock(pedido_ids):
    """Suma a cada variante lo que retenían esos pedidos: una query agregada
    más un UPDATE por variante (el costo depende del catálogo, no de cuántos
    pedidos se devuelven)."""
    por_variante = (
        ItemPedido.objects.filter(pedido_id__in=pedido_ids)
        .values("variante_id")
        .annotate(n=Sum("cantidad"))
        .order_by("variante_id")
    )
    for fila in por_variante:
        Variante.objects.filter(pk=fila["variante_id"]).update(stock=F("stock") + fila["n"])


def _candidatos_a_vencer(gimnasio, limite):
    qs = Pedido.objects.select_for_update(skip_locked=True).filter(
        estado=_E.PENDIENTE_PAGO, reservado_hasta__lt=limite
    )
    if gimnasio is not None:
        qs = qs.filter(gimnasio=gimnasio)
    return list(qs.order_by("pk").values_list("pk", flat=True))


class _Carrera(Exception):
    pass


def vencer_pedidos(*, gimnasio=None, ahora=None):
    """Vence los pedidos sin comprobante cuyo plazo (más la gracia) pasó y
    devuelve su stock. Devuelve los ids vencidos POR ESTE llamado.

    El camino rápido es un UPDATE masivo; si tocó menos filas de las que
    seleccionó (otro proceso movió alguna en el medio), se deshace y se vence
    de a uno, que es lento pero exacto."""
    ahora = _ahora(ahora)
    limite = ahora - GRACIA_COMPROBANTE
    try:
        with transaction.atomic():
            ids = _candidatos_a_vencer(gimnasio, limite)
            if not ids:
                return []
            # `motivo=""`: el de un comprobante rechazado ya no explica nada.
            tocadas = Pedido.objects.filter(pk__in=ids, estado=_E.PENDIENTE_PAGO).update(
                estado=_E.VENCIDO, motivo="", modificado=ahora
            )
            if tocadas != len(ids):
                raise _Carrera
            _devolver_stock(ids)
            return ids
    except _Carrera:
        vencidos = []
        for pk in ids:
            with transaction.atomic():
                if Pedido.objects.filter(pk=pk, estado=_E.PENDIENTE_PAGO).update(
                    estado=_E.VENCIDO, motivo="", modificado=ahora
                ):
                    _devolver_stock([pk])
                    vencidos.append(pk)
        return vencidos


def subir_comprobante(pedido, *, alumno, archivo, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if pedido.medio != Pedido.Medio.TRANSFERENCIA or pedido.estado != _E.PENDIENTE_PAGO:
        raise ErrorDeTienda("estado_invalido")
    # Primero el archivo (red, fuera de cualquier lock), después la transición
    # condicional. Si no entró, se borra el archivo: nada huérfano en R2.
    pedido.comprobante.save(archivo.name, archivo, save=False)
    nombre = pedido.comprobante.name
    tocadas = Pedido.objects.filter(
        pk=pedido.pk,
        estado=_E.PENDIENTE_PAGO,
        reservado_hasta__gte=ahora - GRACIA_COMPROBANTE,
    ).update(
        estado=_E.EN_REVISION, comprobante=nombre,
        comprobante_subido_en=ahora, modificado=ahora,
    )
    if not tocadas:
        pedido.comprobante.storage.delete(nombre)
        pedido.refresh_from_db()
        raise ErrorDeTienda("plazo_vencido")
    pedido.refresh_from_db()


# --- Stock ----------------------------------------------------------------


def ajustar_stock(variante, delta):
    """Suma o resta unidades al disponible sin pisar las reservas que entren
    mientras el staff tenía la pantalla abierta."""
    if delta == 0:
        return
    qs = Variante.objects.filter(pk=variante.pk)
    if delta < 0:
        qs = qs.filter(stock__gte=-delta)
    if not qs.update(stock=F("stock") + delta):
        raise ErrorDeTienda("stock_negativo")
    variante.refresh_from_db()


def reservado_por_variante(gimnasio):
    return dict(
        ItemPedido.objects.filter(
            pedido__gimnasio=gimnasio, pedido__estado__in=ESTADOS_QUE_RETIENEN
        )
        .values("variante_id")
        .annotate(n=Sum("cantidad"))
        .values_list("variante_id", "n")
    )
```

- [ ] **Step 3: Correr** `$PY manage.py test tienda.tests_services -v 2` → PASS. Si `test_el_costo_no_crece_con_la_cantidad_de_pedidos` falla, el culpable es un loop por pedido: corregir el código, no el test.

- [ ] **Step 4: Confirmar que los tests de regresión muerden.** Una por una, aplicar la mutación, correr el test indicado (debe FALLAR) y revertir:
  - En `_reservar_y_crear`, sacar `stock__gte=cantidad` → `test_la_segunda_compra_de_la_ultima_unidad_falla_sin_tocar_nada`.
  - En `crear_pedido`, sacar la validación `1 <= n` → `test_cantidad_negativa_no_suma_stock`.
  - En `_variantes_vendibles`, sacar `producto__gimnasio=gimnasio` → `test_variante_de_otro_gimnasio_no_toca_su_stock` (la variante ajena entra en `variantes` y se descuenta).
  - En `vencer_pedidos`, cambiar `if tocadas != len(ids): raise _Carrera` por `pass` → `test_si_otro_proceso_movio_un_pedido_no_libera_su_stock`.
  - En `_candidatos_a_vencer`, usar `ahora` en lugar de `limite` → `test_el_vencimiento_perezoso_respeta_la_gracia`.

- [ ] **Step 5: Commit**

```bash
git status --short
git add tienda/services.py tienda/testing.py tienda/tests_services.py
git commit -m "feat(tienda): carrito, reserva de stock y vencimiento"
```

---

### Task 3: Transiciones de staff y alumno, reintento del comprobante y arrepentimiento

**Files:**
- Modify: `tienda/services.py` (agregar al final; reemplazar `subir_comprobante`; import de `django.db.models`), `tienda/tests_services.py` (agregar clases al final)

**Interfaces:**
- Consumes: todo lo de la Tarea 2 (incluidos `_ahora`, `_devolver_stock`, `_E`, `obtener_configuracion`, y `_Base.subir` en los tests).
- Produces (las usan las Tareas 5-9):
  - `cancelar_por_alumno(pedido, *, alumno, ahora=None) -> None`
  - `marcar_pagado(pedido, *, ahora=None) -> None`
  - `rechazar_comprobante(pedido, *, motivo: str, ahora=None) -> None` — **`EN_REVISION → PENDIENTE_PAGO`** con `reservado_hasta = ahora + horas_reserva_transferencia`, `motivo` guardado y el stock **sigue reservado** (ya no cancela).
  - `entregar(pedido, *, ahora=None) -> None`
  - `cancelar_por_staff(pedido, *, motivo: str, ahora=None) -> None`
  - `puede_pedir_cancelacion(pedido, *, hoy=None) -> bool` — `PAGADO` sin pedir → siempre; `ENTREGADO` → hasta `localdate(entregado_en) + 10`.
  - `q_puede_pedir_cancelacion(*, hoy=None) -> Q` — la misma regla como filtro de la base (la usa el «Botón de arrepentimiento» de la Tarea 7).
  - `pedir_cancelacion(pedido, *, alumno, ahora=None) -> None`
  - `resolver_cancelacion(pedido, *, aceptar: bool, motivo: str = "", ahora=None) -> None`
  - `tiene_ventas(producto) -> bool`
  - `Q_CANCELACION_PENDIENTE: Q`
  - `contadores_panel(gimnasio) -> dict` con claves `a_revisar`, `a_entregar`, `cancelaciones`, `vivos`.
  - `subir_comprobante` (misma firma): si el pedido ya tenía un comprobante (rechazado), borra el archivo viejo **sólo si la transición entró** y limpia `motivo` en el mismo `UPDATE`; si no entró, borra el nuevo y deja el viejo.

Decisión aceptada (documentarla en `ARQUITECTURA.md`, Tarea 9): el recordatorio de «comprobante sin revisar hace 24 h» sale **una sola vez por pedido** (dedup de `RecordatorioEnviado` por `objeto_id`): si un comprobante se rechaza y el reenviado vuelve a demorarse, no hay segundo recordatorio. La tarjeta del panel lo sigue contando.

- [ ] **Step 1: Tests que fallan** — agregar al final de `tienda/tests_services.py` (los imports ya están):

```python
class TransicionesDelStaffTests(_Base):
    def test_marcar_pagado_desde_pendiente_o_revision(self):
        a = self.comprar({self.m.pk: 1})
        services.marcar_pagado(a, ahora=AHORA)
        a.refresh_from_db()
        self.assertEqual((a.estado, a.pagado_en), (Pedido.Estado.PAGADO, AHORA))
        self.assertEqual(self.stock(self.m), 2)  # sigue retenido

    def test_rechazar_vuelve_a_esperar_el_pago_sin_devolver_stock(self):
        pedido = self.comprar({self.m.pk: 2})
        self.subir(pedido)
        despues = AHORA + timedelta(hours=3)
        services.rechazar_comprobante(pedido, motivo="No se lee el monto", ahora=despues)
        pedido.refresh_from_db()
        self.assertEqual(
            (pedido.estado, pedido.motivo), (Pedido.Estado.PENDIENTE_PAGO, "No se lee el monto")
        )
        self.assertEqual(pedido.reservado_hasta, despues + timedelta(hours=24))
        self.assertEqual(self.stock(self.m), 1)  # sigue reservado

    def test_el_plazo_nuevo_sale_de_la_configuracion_del_gimnasio(self):
        config = obtener_configuracion(self.gimnasio)
        config.horas_reserva_transferencia = 48
        config.save()
        pedido = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.reservado_hasta, AHORA + timedelta(hours=48))

    def test_rechazar_dos_veces_falla_la_segunda(self):
        pedido = self.comprar({self.m.pk: 2})
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        with self.assertRaises(ErrorDeTienda):
            services.rechazar_comprobante(pedido, motivo="x", ahora=AHORA)
        self.assertEqual(self.stock(self.m), 1)

    def test_cancelar_un_pagado_lo_devuelve_y_libera(self):
        pedido = self.comprar({self.m.pk: 2})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.cancelar_por_staff(pedido, motivo="Se arrepintió", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.DEVUELTO)
        self.assertEqual(self.stock(self.m), 3)

    def test_entregar_solo_desde_pagado(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda):
            services.entregar(pedido, ahora=AHORA)
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        self.assertEqual(self.stock(self.m), 2)  # entregado: no vuelve

    def test_motivo_largo_se_rechaza(self):
        pedido = self.comprar({self.m.pk: 1})
        with self.assertRaises(ErrorDeTienda) as e:
            services.cancelar_por_staff(pedido, motivo="x" * 301, ahora=AHORA)
        self.assertEqual(e.exception.motivo, "motivo_largo")


class ReintentoDelComprobanteTests(_Base):
    def rechazado(self):
        pedido = self.comprar({self.m.pk: 1})
        self.subir(pedido)
        pedido.refresh_from_db()
        self.viejo = pedido.comprobante.name
        services.rechazar_comprobante(pedido, motivo="Borroso", ahora=AHORA)
        return pedido

    def test_resubir_reemplaza_borra_el_viejo_y_limpia_el_motivo(self):
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        self.subir(pedido, ahora=AHORA + timedelta(hours=1))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.EN_REVISION)
        self.assertNotEqual(pedido.comprobante.name, self.viejo)
        self.assertEqual(pedido.motivo, "")
        self.assertEqual(pedido.comprobante_subido_en, AHORA + timedelta(hours=1))
        self.assertFalse(default_storage.exists(self.viejo))
        self.assertTrue(default_storage.exists(pedido.comprobante.name))

    def test_resubir_tarde_no_borra_el_comprobante_anterior(self):
        """El viejo es lo único que el staff tiene para mirar: se borra sólo
        si el nuevo entró."""
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        tarde = pedido.reservado_hasta + timedelta(minutes=3)
        with self.assertRaises(ErrorDeTienda):
            self.subir(pedido, ahora=tarde)
        pedido.refresh_from_db()
        self.assertEqual(pedido.comprobante.name, self.viejo)
        self.assertTrue(default_storage.exists(self.viejo))
        self.assertEqual(pedido.motivo, "Borroso")

    def test_si_vence_despues_del_rechazo_no_queda_el_motivo(self):
        pedido = self.rechazado()
        services.vencer_pedidos(ahora=pedido.reservado_hasta + timedelta(days=1))
        pedido.refresh_from_db()
        self.assertEqual((pedido.estado, pedido.motivo), (Pedido.Estado.VENCIDO, ""))

    def test_si_el_alumno_cancela_despues_del_rechazo_no_queda_el_motivo(self):
        pedido = self.rechazado()
        services.cancelar_por_alumno(pedido, alumno=self.alumno, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual((pedido.estado, pedido.motivo), (Pedido.Estado.CANCELADO, ""))

    def test_con_la_instancia_vieja_borra_el_archivo_que_dice_la_base(self):
        """Dos subidas casi juntas: la instancia del request trae un nombre
        viejo. Se borra el que la base tenía al momento del UPDATE."""
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        pedido = self.rechazado()
        otro = default_storage.save("tienda/comprobantes/otro.png", ContentFile(PNG))
        Pedido.objects.filter(pk=pedido.pk).update(comprobante=otro)
        self.subir(pedido, ahora=AHORA + timedelta(hours=1))  # `pedido` todavía dice `self.viejo`
        self.assertFalse(default_storage.exists(otro))

    def test_si_no_resube_vence_y_devuelve_el_stock_una_sola_vez(self):
        pedido = self.rechazado()
        despues = pedido.reservado_hasta + timedelta(days=1)
        self.assertEqual(services.vencer_pedidos(ahora=despues), [pedido.pk])
        services.vencer_pedidos(ahora=despues)
        self.assertEqual(self.stock(self.m), 3)


class CancelacionTests(_Base):
    def test_el_alumno_cancela_su_pendiente(self):
        pedido = self.comprar({self.m.pk: 2})
        services.cancelar_por_alumno(pedido, alumno=self.alumno, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.CANCELADO)
        self.assertEqual(self.stock(self.m), 3)

    def test_mientras_no_lo_retira_siempre_puede_pedir(self):
        """Art. 34: el plazo corre desde la entrega o el contrato, lo último.
        Sin retirar, todavía no hubo entrega."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        lejos = timezone.localdate(AHORA) + timedelta(days=60)
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=lejos))

    def test_el_plazo_se_cuenta_desde_el_retiro(self):
        """Pagó el 20/10 y retiró 15 días después: contar desde el pago lo
        dejaba sin derecho el mismo día del retiro."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        retiro = AHORA + timedelta(days=15)
        services.entregar(pedido, ahora=retiro)
        pedido.refresh_from_db()
        dia_retiro = timezone.localdate(retiro)
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro))
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro + timedelta(days=10)))
        self.assertFalse(services.puede_pedir_cancelacion(pedido, hoy=dia_retiro + timedelta(days=11)))
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=retiro + timedelta(days=10))
        with self.assertRaises(ErrorDeTienda):  # no se pide dos veces
            services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=retiro + timedelta(days=10))

    def test_fuera_de_plazo_explica_desde_cuando_se_cuenta(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        with self.assertRaises(ErrorDeTienda) as e:
            services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA + timedelta(days=11))
        self.assertIn("desde que retiraste", e.exception.mensaje)

    def test_el_filtro_en_la_base_coincide_con_la_regla(self):
        config = obtener_configuracion(self.gimnasio)
        config.max_pedidos_activos = 10
        config.save()
        Variante.objects.filter(pk=self.m.pk).update(stock=20)
        hoy = timezone.localdate(AHORA) + timedelta(days=20)  # 9/11

        pagado = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pagado, ahora=AHORA)
        justo = self.comprar({self.m.pk: 1})  # retirado el 30/10: 30/10 + 10 = 9/11
        services.marcar_pagado(justo, ahora=AHORA)
        services.entregar(justo, ahora=AHORA + timedelta(days=10))
        tarde = self.comprar({self.m.pk: 1})  # retirado el 29/10: ya pasó
        services.marcar_pagado(tarde, ahora=AHORA)
        services.entregar(tarde, ahora=AHORA + timedelta(days=9))
        self.comprar({self.m.pk: 1})  # pendiente
        ya_pedida = self.comprar({self.m.pk: 1})
        services.marcar_pagado(ya_pedida, ahora=AHORA)
        services.pedir_cancelacion(ya_pedida, alumno=self.alumno, ahora=AHORA)

        regla = {p.pk for p in Pedido.objects.all() if services.puede_pedir_cancelacion(p, hoy=hoy)}
        base = set(
            Pedido.objects.filter(services.q_puede_pedir_cancelacion(hoy=hoy)).values_list("pk", flat=True)
        )
        self.assertEqual(regla, {pagado.pk, justo.pk})
        self.assertEqual(base, regla)

    def test_el_plazo_cuenta_en_fecha_argentina_no_utc(self):
        """Retirado el 20/10 a las 22:30 (Argentina) = 21/10 01:30 UTC. El día
        10 es el 30/10, no el 31/10. Con `now().date()` daría un día de más."""
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        noche = timezone.make_aware(datetime(2026, 10, 20, 22, 30))
        services.entregar(pedido, ahora=noche)
        pedido.refresh_from_db()
        self.assertTrue(services.puede_pedir_cancelacion(pedido, hoy=datetime(2026, 10, 30).date()))
        self.assertFalse(services.puede_pedir_cancelacion(pedido, hoy=datetime(2026, 10, 31).date()))

    def test_aceptar_cancelacion_de_un_pagado_libera_stock(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=True, ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.DEVUELTO)
        self.assertEqual(self.stock(self.m), 3)

    def test_aceptar_cancelacion_de_un_entregado_no_toca_stock(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.entregar(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=True, ahora=AHORA)
        self.assertEqual(self.stock(self.m), 2)

    def test_cancelar_por_staff_cierra_la_cancelacion_pedida(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.cancelar_por_staff(pedido, motivo="Devuelto", ahora=AHORA)
        self.assertEqual(services.contadores_panel(self.gimnasio)["cancelaciones"], 0)
        # El contador ya da 0 porque DEVUELTO no está en Q_CANCELACION_PENDIENTE:
        # lo que fija el UPDATE de la resolución es este campo.
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.cancelacion_resuelta_en)

    def test_rechazar_cancelacion_deja_el_pedido_y_la_cierra(self):
        pedido = self.comprar({self.m.pk: 1})
        services.marcar_pagado(pedido, ahora=AHORA)
        services.pedir_cancelacion(pedido, alumno=self.alumno, ahora=AHORA)
        services.resolver_cancelacion(pedido, aceptar=False, motivo="Ya usado", ahora=AHORA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PAGADO)
        self.assertIsNotNone(pedido.cancelacion_resuelta_en)


class TieneVentasTests(_Base):
    def test_tiene_ventas(self):
        self.assertFalse(services.tiene_ventas(self.producto))
        self.comprar({self.m.pk: 1})
        self.assertTrue(services.tiene_ventas(self.producto))


class ContadoresTests(_Base):
    def test_una_sola_query(self):
        a = self.comprar({self.m.pk: 1})
        Pedido.objects.filter(pk=a.pk).update(estado=Pedido.Estado.EN_REVISION)
        b = self.comprar({self.l.pk: 1}, alumno=self.alumno2)
        services.marcar_pagado(b, ahora=AHORA)
        with self.assertNumQueries(1):
            c = services.contadores_panel(self.gimnasio)
        self.assertEqual(c, {"a_revisar": 1, "a_entregar": 1, "cancelaciones": 0, "vivos": 2})
```

Run: `$PY manage.py test tienda.tests_services -v 2`
Expected: FAIL/ERROR (`AttributeError: module 'tienda.services' has no attribute 'marcar_pagado'`; los de reintento fallan además porque `subir_comprobante` todavía no borra el viejo ni limpia `motivo`).

- [ ] **Step 2: Implementación.**
  1. En `tienda/services.py`, cambiar `from django.db.models import F, Sum` por `from django.db.models import Count, F, Q, Sum` y `from datetime import timedelta` por `from datetime import datetime, time, timedelta`.
  2. Reemplazar `subir_comprobante` entera por:

```python
def subir_comprobante(pedido, *, alumno, archivo, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if pedido.medio != Pedido.Medio.TRANSFERENCIA or pedido.estado != _E.PENDIENTE_PAGO:
        raise ErrorDeTienda("estado_invalido")
    # Primero el archivo (red, fuera de cualquier lock), después la transición
    # condicional. Si no entró, se borra el archivo NUEVO: nada huérfano en R2.
    pedido.comprobante.save(archivo.name, archivo, save=False)
    nombre = pedido.comprobante.name
    almacen = pedido.comprobante.storage
    with transaction.atomic():
        # Un pedido con el comprobante rechazado ya tiene archivo. Su nombre
        # sale de la BASE con la fila bloqueada, no de la instancia del
        # request, que puede ser vieja (dos subidas casi juntas).
        anterior = (
            Pedido.objects.select_for_update()
            .values_list("comprobante", flat=True)
            .get(pk=pedido.pk)
        ) or ""
        tocadas = Pedido.objects.filter(
            pk=pedido.pk,
            estado=_E.PENDIENTE_PAGO,
            reservado_hasta__gte=ahora - GRACIA_COMPROBANTE,
        ).update(
            estado=_E.EN_REVISION, comprobante=nombre, comprobante_subido_en=ahora,
            motivo="", modificado=ahora,
        )
    if not tocadas:
        almacen.delete(nombre)
        pedido.refresh_from_db()
        raise ErrorDeTienda("plazo_vencido")
    if anterior and anterior != nombre:
        # Recién ahora: si la transición no hubiera entrado, el viejo seguía
        # siendo lo único que el staff tenía para revisar.
        almacen.delete(anterior)
    pedido.refresh_from_db()
```

  3. Agregar al final del archivo:

```python
# --- Transiciones ----------------------------------------------------------


def _transicionar(pedido, origen, ahora, **campos):
    """UPDATE condicional sobre el estado. True si este proceso hizo la
    transición (y por lo tanto le toca devolver el stock, si corresponde)."""
    tocadas = Pedido.objects.filter(pk=pedido.pk, estado__in=origen).update(
        modificado=ahora, **campos
    )
    if tocadas:
        pedido.refresh_from_db()
    return tocadas == 1


def _validar_motivo(motivo):
    if len(motivo) > MAX_LARGO_MOTIVO:
        raise ErrorDeTienda("motivo_largo")


def cancelar_por_alumno(pedido, *, alumno, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    with transaction.atomic():
        if not _transicionar(pedido, [_E.PENDIENTE_PAGO], ahora, estado=_E.CANCELADO, motivo=""):
            raise ErrorDeTienda("estado_invalido")
        _devolver_stock([pedido.pk])


def marcar_pagado(pedido, *, ahora=None):
    """El staff confirma la plata: desde la revisión del comprobante, o
    directo desde pendiente (pagó en el mostrador o transfirió sin subirlo)."""
    ahora = _ahora(ahora)
    if not _transicionar(
        pedido, [_E.PENDIENTE_PAGO, _E.EN_REVISION], ahora, estado=_E.PAGADO, pagado_en=ahora
    ):
        raise ErrorDeTienda("estado_invalido")


def rechazar_comprobante(pedido, *, motivo, ahora=None):
    """El comprobante no sirve (borroso, otro monto): el pedido vuelve a
    esperar el pago con un plazo nuevo y el stock SIGUE reservado. El archivo
    viejo queda hasta que el alumno suba otro. Para cortar del todo está
    `cancelar_por_staff`. Si no resube, vence como cualquier pendiente."""
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    horas = obtener_configuracion(pedido.gimnasio).horas_reserva_transferencia
    if not _transicionar(
        pedido, [_E.EN_REVISION], ahora,
        estado=_E.PENDIENTE_PAGO, motivo=motivo,
        reservado_hasta=ahora + timedelta(hours=horas),
    ):
        raise ErrorDeTienda("estado_invalido")


def entregar(pedido, *, ahora=None):
    ahora = _ahora(ahora)
    if not _transicionar(pedido, [_E.PAGADO], ahora, estado=_E.ENTREGADO, entregado_en=ahora):
        raise ErrorDeTienda("estado_invalido")


def cancelar_por_staff(pedido, *, motivo, ahora=None):
    """Sin pagar → CANCELADO; pagado sin entregar → DEVUELTO (el staff le
    devolvió la plata por fuera). Las dos devuelven el stock."""
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    with transaction.atomic():
        hecho = _transicionar(
            pedido, [_E.PENDIENTE_PAGO, _E.EN_REVISION], ahora,
            estado=_E.CANCELADO, motivo=motivo,
        ) or _transicionar(pedido, [_E.PAGADO], ahora, estado=_E.DEVUELTO, motivo=motivo)
        if not hecho:
            raise ErrorDeTienda("estado_invalido")
        _devolver_stock([pedido.pk])
        # Si el alumno había pedido la cancelación, esto la resuelve: si no,
        # queda contada para siempre en la tarjeta del panel.
        Pedido.objects.filter(
            pk=pedido.pk, cancelacion_pedida_en__isnull=False, cancelacion_resuelta_en__isnull=True
        ).update(cancelacion_resuelta_en=ahora)
    pedido.refresh_from_db()


def puede_pedir_cancelacion(pedido, *, hoy=None):
    """Ley 24.240 art. 34: 10 días corridos desde la entrega del bien o la
    celebración del contrato, LO ÚLTIMO. Con retiro en el gimnasio: mientras
    no lo retiró se puede siempre; retirado, hasta 10 días después del retiro
    (en fecha argentina)."""
    hoy = hoy or timezone.localdate()
    if pedido.cancelacion_pedida_en is not None:
        return False
    if pedido.estado == _E.PAGADO:
        return True
    return (
        pedido.estado == _E.ENTREGADO
        and pedido.entregado_en is not None
        and hoy <= timezone.localdate(pedido.entregado_en) + timedelta(days=DIAS_ARREPENTIMIENTO)
    )


def q_puede_pedir_cancelacion(*, hoy=None):
    """`puede_pedir_cancelacion` como filtro de la base: localdate(entregado)
    >= hoy - 10 equivale a entregado_en >= el inicio (en hora argentina) de
    ese día. Lo fija `test_el_filtro_en_la_base_coincide_con_la_regla`."""
    hoy = hoy or timezone.localdate()
    desde = timezone.make_aware(
        datetime.combine(hoy - timedelta(days=DIAS_ARREPENTIMIENTO), time.min)
    )
    return Q(cancelacion_pedida_en__isnull=True) & (
        Q(estado=_E.PAGADO) | Q(estado=_E.ENTREGADO, entregado_en__gte=desde)
    )


def pedir_cancelacion(pedido, *, alumno, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if not puede_pedir_cancelacion(pedido, hoy=timezone.localdate(ahora)):
        raise ErrorDeTienda("fuera_de_plazo_arrepentimiento")
    tocadas = Pedido.objects.filter(
        pk=pedido.pk, estado__in=[_E.PAGADO, _E.ENTREGADO], cancelacion_pedida_en__isnull=True
    ).update(cancelacion_pedida_en=ahora, modificado=ahora)
    if not tocadas:
        raise ErrorDeTienda("estado_invalido")
    pedido.refresh_from_db()


def resolver_cancelacion(pedido, *, aceptar, motivo="", ahora=None):
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    base = Pedido.objects.filter(
        pk=pedido.pk, cancelacion_pedida_en__isnull=False, cancelacion_resuelta_en__isnull=True
    )
    with transaction.atomic():
        if not aceptar:
            if not base.update(cancelacion_resuelta_en=ahora, motivo=motivo, modificado=ahora):
                raise ErrorDeTienda("estado_invalido")
        elif base.filter(estado=_E.PAGADO).update(
            estado=_E.DEVUELTO, cancelacion_resuelta_en=ahora, motivo=motivo, modificado=ahora
        ):
            _devolver_stock([pedido.pk])
        elif not base.filter(estado=_E.ENTREGADO).update(
            estado=_E.DEVUELTO, cancelacion_resuelta_en=ahora, motivo=motivo, modificado=ahora
        ):
            raise ErrorDeTienda("estado_invalido")
    pedido.refresh_from_db()


# --- Ventas y contadores ---------------------------------------------------


def tiene_ventas(producto):
    return ItemPedido.objects.filter(variante__producto=producto).exists()


#: Cancelación pedida por el alumno y todavía sin resolver. La comparten el
#: contador del panel y el filtro del listado del staff.
Q_CANCELACION_PENDIENTE = Q(
    estado__in=[_E.PAGADO, _E.ENTREGADO],
    cancelacion_pedida_en__isnull=False,
    cancelacion_resuelta_en__isnull=True,
)


def contadores_panel(gimnasio):
    return Pedido.objects.for_gimnasio(gimnasio).aggregate(
        a_revisar=Count("pk", filter=Q(estado=_E.EN_REVISION)),
        a_entregar=Count("pk", filter=Q(estado=_E.PAGADO)),
        cancelaciones=Count("pk", filter=Q_CANCELACION_PENDIENTE),
        vivos=Count("pk", filter=Q(estado__in=ESTADOS_QUE_RETIENEN)),
    )
```

- [ ] **Step 3: Correr** `$PY manage.py test tienda.tests_services -v 2` → PASS (todas las clases, también las de la Tarea 2).

- [ ] **Step 4: Confirmar que los tests de regresión muerden.** Una por una, aplicar la mutación, correr el test indicado (debe FALLAR) y revertir:
  - En `rechazar_comprobante`, agregar `_devolver_stock([pedido.pk])` después de la transición → `test_rechazar_vuelve_a_esperar_el_pago_sin_devolver_stock`.
  - En `subir_comprobante`, mover `almacen.delete(anterior)` antes del `UPDATE` → `test_resubir_tarde_no_borra_el_comprobante_anterior`.
  - En `subir_comprobante`, tomar `anterior = pedido.comprobante.name` (antes del `save`) en vez de leerlo de la base → `test_con_la_instancia_vieja_borra_el_archivo_que_dice_la_base`.
  - En `vencer_pedidos` (camino rápido), sacar `motivo=""` → `test_si_vence_despues_del_rechazo_no_queda_el_motivo`.
  - En `q_puede_pedir_cancelacion`, usar `days=DIAS_ARREPENTIMIENTO + 1` → `test_el_filtro_en_la_base_coincide_con_la_regla`.
  - En `subir_comprobante`, sacar `motivo=""` del `UPDATE` → `test_resubir_reemplaza_borra_el_viejo_y_limpia_el_motivo`.
  - En `puede_pedir_cancelacion`, contar desde `pedido.pagado_en` en vez de `entregado_en` → `test_el_plazo_se_cuenta_desde_el_retiro`.
  - En `puede_pedir_cancelacion`, usar `pedido.entregado_en.date()` en vez de `timezone.localdate(...)` → `test_el_plazo_cuenta_en_fecha_argentina_no_utc`.
  - En `cancelar_por_staff`, sacar el `UPDATE` de `cancelacion_resuelta_en` → `test_cancelar_por_staff_cierra_la_cancelacion_pedida`.

- [ ] **Step 5: Commit**

```bash
git status --short
git add tienda/services.py tienda/tests_services.py
git commit -m "feat(tienda): transiciones, reintento del comprobante y arrepentimiento desde el retiro"
```

---

### Task 4: Staff — categorías, productos, variantes, foto

**Files:**
- Create: `tienda/fotos.py`, `tienda/forms.py`, `tienda/views.py`, `tienda/urls.py`, `tienda/tests_vistas_staff.py`, `templates/tienda/categoria_list.html`, `templates/tienda/categoria_form.html`, `templates/tienda/producto_list.html`, `templates/tienda/producto_form.html`, `templates/tienda/producto_borrar.html` (si `core/confirmar_borrado.html` no alcanza)
- Modify: `config/urls.py`, `plataforma/middleware.py:104` (`URLS_SIN_BLOQUEO`)

**Interfaces:**
- Consumes: `services.ajustar_stock`, `services.reservado_por_variante`, `services.tiene_ventas`, `services.ErrorDeTienda`.
- Produces: URLs `tienda:productos`, `tienda:producto_crear`, `tienda:producto_editar <pk>`, `tienda:producto_borrar <pk>`, `tienda:variante_ajustar <pk>`, `tienda:categorias`, `tienda:categoria_crear`, `tienda:categoria_editar <pk>`, `tienda:categoria_borrar <pk>`, `tienda:tienda_foto_producto <pk>`; mixin `TiendaStaffMixin` en `tienda/views.py`.

- [ ] **Step 1: Tests que fallan** — `tienda/tests_vistas_staff.py` (parte de productos):

```python
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import CategoriaProducto, Pedido, Producto, Variante


def jpeg(ancho=1600, alto=1200):
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), "red").save(salida, "JPEG")
    return SimpleUploadedFile("foto.jpg", salida.getvalue(), content_type="image/jpeg")


class _BaseStaff(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)

    def crear_producto(self, gimnasio=None, stock=3):
        producto = Producto.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        variante = Variante.objects.create(producto=producto, nombre="M", stock=stock)
        return producto, variante


def datos_producto(**extra):
    datos = {
        "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
        "orden": "0", "categoria": "",
        "variantes-TOTAL_FORMS": "2", "variantes-INITIAL_FORMS": "0",
        "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
        "variantes-0-nombre": "Chocolate", "variantes-0-stock_inicial": "4", "variantes-0-activa": "on",
        "variantes-1-nombre": "Vainilla", "variantes-1-stock_inicial": "2", "variantes-1-activa": "on",
    }
    datos.update(extra)
    return datos


class ProductoFormTests(_BaseStaff):
    def test_crear_con_variantes_y_foto_reescalada(self):
        datos = datos_producto()
        datos["foto"] = jpeg()
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        self.assertEqual(
            sorted(producto.variantes.values_list("nombre", "stock")),
            [("Chocolate", 4), ("Vainilla", 2)],
        )
        with producto.foto.open("rb") as f:
            self.assertLessEqual(max(Image.open(f).size), 1200)

    def test_sin_ninguna_variante_es_error_visible(self):
        datos = datos_producto(**{"variantes-0-nombre": "", "variantes-0-stock_inicial": "",
                                   "variantes-1-nombre": "", "variantes-1-stock_inicial": ""})
        datos.pop("variantes-0-activa")
        datos.pop("variantes-1-activa")
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "al menos una variante")

    def test_editar_el_nombre_no_pisa_el_stock_reservado(self):
        producto, variante = self.crear_producto(stock=3)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 2}, suplantando=False)
        datos = {
            "nombre": "Remera Dry", "descripcion": "", "precio": "15000", "publicado": "on",
            "orden": "0", "categoria": "",
            "variantes-TOTAL_FORMS": "1", "variantes-INITIAL_FORMS": "1",
            "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
            "variantes-0-id": str(variante.pk), "variantes-0-nombre": "M",
            "variantes-0-activa": "on",
        }
        r = self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 1)

    def test_errores_del_formset_se_ven(self):
        """`core.tests.ErroresDeFormularioVisiblesTests` sólo mira `form.<campo>`:
        los campos del formset de variantes no los cubre, así que se prueban acá."""
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-1-nombre": "Chocolate"}),
        )
        self.assertContains(r, "mismo nombre")
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-0-stock_inicial": "-3"}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_categoria_de_otro_gimnasio_no_se_puede_elegir(self):
        ajena = CategoriaProducto.objects.create(gimnasio=self.otro, nombre="Ajena")
        r = self.client.post(reverse("tienda:producto_crear"), datos_producto(categoria=str(ajena.pk)))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_no_edita_un_producto_ajeno(self):
        producto, _ = self.crear_producto(gimnasio=self.otro)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertEqual(r.status_code, 404)


class CategoriaTests(_BaseStaff):
    def test_nombre_repetido_es_error_visible_no_500(self):
        CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        r = self.client.post(reverse("tienda:categoria_crear"), {"nombre": "ropa", "orden": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ya hay una categoría")

    def test_editar_no_crea_otra(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.client.post(
            reverse("tienda:categoria_editar", args=[cat.pk]), {"nombre": "Indumentaria", "orden": "0"}
        )
        self.assertEqual(CategoriaProducto.objects.for_gimnasio(self.gimnasio).count(), 1)
        cat.refresh_from_db()
        self.assertEqual(cat.nombre, "Indumentaria")


class AjusteStockTests(_BaseStaff):
    def test_suma_y_resta(self):
        _, variante = self.crear_producto(stock=3)
        url = reverse("tienda:variante_ajustar", args=[variante.pk])
        self.client.post(url, {"delta": "5"})
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)
        r = self.client.post(url, {"delta": "-20"}, follow=True)
        self.assertContains(r, "No hay tantas unidades")
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)

    def test_variante_ajena_404(self):
        _, ajena = self.crear_producto(gimnasio=self.otro)
        r = self.client.post(reverse("tienda:variante_ajustar", args=[ajena.pk]), {"delta": "5"})
        self.assertEqual(r.status_code, 404)


class BorrarProductoTests(_BaseStaff):
    def test_sin_ventas_se_borra(self):
        producto, _ = self.crear_producto()
        self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertFalse(Producto.objects.filter(pk=producto.pk).exists())

    def test_con_ventas_no_se_borra_y_lo_explica(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 1}, suplantando=False)
        r = self.client.get(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertContains(r, "pedidos")
        r = self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertContains(r, "Destildá «Publicado»")


class FotoTests(_BaseStaff):
    def test_sirve_la_foto_cacheable_sin_cookie(self):
        producto, _ = self.crear_producto()
        producto.foto.save("f.jpg", jpeg(400, 400))
        self.client.logout()
        r = self.client.get(producto.foto_url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r["Cache-Control"])
        self.assertNotIn("sessionid", r.cookies)

    def test_logueado_tampoco_lleva_cookie_ni_registra_actividad(self):
        """El anónimo corta antes en el middleware y no prueba nada: con sesión
        es donde `URLS_SIN_BLOQUEO` evita la cookie en una respuesta que se
        cachea un año (molde: `plataforma/tests_actividad.py::ArchivosCacheablesTests`)."""
        from plataforma.models import ActividadDiaria

        producto, _ = self.crear_producto()
        producto.foto.save("f.jpg", jpeg(400, 400))
        r = self.client.get(producto.foto_url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.cookies.keys()), [])
        self.assertFalse(ActividadDiaria.objects.exists())

    def test_gimnasio_sin_tienda_404(self):
        producto, _ = self.crear_producto(gimnasio=Gimnasio.objects.create(nombre="C", slug="c"))
        producto.foto.save("f.jpg", jpeg(400, 400))
        self.assertEqual(self.client.get(producto.foto_url_cacheable).status_code, 404)


class AccesoStaffTests(_BaseStaff):
    def test_sin_add_on_muestra_cartel_200(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:productos"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no está habilitada")

    def test_alumno_no_entra(self):
        u = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=u, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("tienda:productos")).status_code, 403)


class StockBajoTests(_BaseStaff):
    def test_badge_de_stock_bajo_hasta_el_umbral(self):
        _, variante = self.crear_producto(stock=services.UMBRAL_POCAS_UNIDADES)
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")
        Variante.objects.filter(pk=variante.pk).update(stock=services.UMBRAL_POCAS_UNIDADES + 1)
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")

    def test_sin_stock_no_es_stock_bajo(self):
        self.crear_producto(stock=0)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, "Sin stock")
        self.assertNotContains(r, "data-stock-bajo")
```

Run: `$PY manage.py test tienda.tests_vistas_staff -v 2` → ERROR (`NoReverseMatch`).

- [ ] **Step 2: Fotos** — `tienda/fotos.py`

```python
"""Foto de producto: se valida con el mismo helper que el logo del gimnasio
y se reescala a ≤1200 px en JPEG antes de guardarla, para que la vista que la
sirve nunca mande una foto de 6 MB de celular."""

import io
import uuid

from django import forms
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from tenants.forms import _validar_imagen

LADO_MAXIMO = 1200
TAMANIO_MAXIMO = 8 * 1024 * 1024


def preparar_foto(archivo):
    _validar_imagen(
        archivo,
        resolucion_referencia=(300, 300),
        lado_minimo=300,
        tamanio_maximo_bytes=TAMANIO_MAXIMO,
        formatos_validos={"JPEG", "PNG"},
        etiqueta="La foto",
        mensaje_tamanio="La foto pesa más de 8 MB.",
    )
    try:
        imagen = ImageOps.exif_transpose(Image.open(archivo)).convert("RGB")
    except Exception:
        raise forms.ValidationError("El archivo no es una imagen válida.")
    imagen.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    salida = io.BytesIO()
    imagen.save(salida, "JPEG", quality=85, optimize=True)
    return ContentFile(salida.getvalue(), name=f"{uuid.uuid4().hex}.jpg")
```

Nota para el implementer: `_validar_imagen` es «privado» de `tenants/forms.py`; se reusa a propósito (mismas reglas que el logo) con un comentario. No copiarlo.

- [ ] **Step 3: Forms de staff** — `tienda/forms.py` (primera parte; las Tareas 5 y 7 agregan más):

```python
from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from core.forms import TenantScopedModelForm
from tienda.fotos import preparar_foto
from tienda.models import (
    MAX_LARGO_MOTIVO,
    CategoriaProducto,
    ConfiguracionTienda,
    Producto,
    Variante,
)


class CategoriaProductoForm(TenantScopedModelForm):
    """Valida a mano el nombre único por gimnasio: `gimnasio` no es campo del
    form, así que el `UniqueConstraint` explotaría como `IntegrityError` (500).
    Mismo patrón que `ejercicios/forms.py` (`clean_nombre` de la categoría)."""

    class Meta:
        model = CategoriaProducto
        fields = ["nombre", "orden"]

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        chocan = CategoriaProducto.objects.for_gimnasio(self.gimnasio).filter(nombre__iexact=nombre)
        if self.instance.pk:
            chocan = chocan.exclude(pk=self.instance.pk)
        if chocan.exists():
            raise forms.ValidationError("Ya hay una categoría con ese nombre.")
        return nombre


class ProductoForm(TenantScopedModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "categoria", "descripcion", "precio", "foto", "publicado", "orden"]
        widgets = {"foto": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"})}

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "content_type"):  # recién subida, no la guardada
            return preparar_foto(foto)
        return foto


class VarianteForm(forms.ModelForm):
    """`stock` NO es un campo del form: escribirlo en forma absoluta pisaría
    las reservas que entren mientras la pantalla está abierta. En el alta se
    carga como `stock_inicial`; después sólo con «ajuste ±»."""

    stock_inicial = forms.IntegerField(
        min_value=0, max_value=100000, required=False, label="Stock inicial"
    )

    class Meta:
        model = Variante
        fields = ["nombre", "activa"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["stock_inicial"]

    def save(self, commit=True):
        variante = super().save(commit=False)
        if variante.pk is None:
            variante.stock = self.cleaned_data.get("stock_inicial") or 0
            if commit:
                variante.save()
        elif commit:
            variante.save(update_fields=["nombre", "activa"])
        return variante


class BaseVarianteFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        vivas = [
            f for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
            and (f.instance.pk or f.cleaned_data.get("nombre") or f.cleaned_data.get("stock_inicial") is not None)
        ]
        if not vivas:
            raise forms.ValidationError("El producto tiene que tener al menos una variante.")
        nombres = [f.cleaned_data.get("nombre", "").strip().lower() for f in vivas]
        if len(nombres) != len(set(nombres)):
            raise forms.ValidationError("Hay dos variantes con el mismo nombre.")

    def _should_delete_form(self, form):
        # Una variante con ventas no se borra (ItemPedido la protege): se
        # desactiva. El template no muestra la casilla en ese caso; esto es el
        # guard por si se postea a mano.
        borrar = super()._should_delete_form(form)
        if borrar and form.instance.pk and form.instance.items.exists():
            form.instance.activa = False
            return False
        return borrar


VarianteFormSet = inlineformset_factory(
    Producto, Variante, form=VarianteForm, formset=BaseVarianteFormSet,
    extra=2, min_num=0, max_num=50, can_delete=True,
)


class AjusteStockForm(forms.Form):
    delta = forms.IntegerField(
        min_value=-100000, max_value=100000,
        label="Sumar o restar unidades",
        help_text="Ej.: 10 si llegaron 10; -1 si se rompió una.",
    )
```

Nota: el `test_sin_ninguna_variante_es_error_visible` arma el POST con dos formularios vacíos; Django marca vacío (`has_changed() == False`) un form extra sin datos y no lo valida: por eso `vivas` cuenta sólo los que tienen datos. Verificar en el template que `formset.non_form_errors` se renderiza.

- [ ] **Step 4: Vistas de staff (productos)** — `tienda/views.py`

```python
"""Vistas de la tienda: staff (gestión), alumno (compra) y la foto pública.

Los errores de dominio (`ErrorDeTienda`) se muestran con `messages.error` y
redirect: bajo `hx-boost` un 4xx es un click mudo.
"""

import mimetypes

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from tenants.mixins import StaffRequiredMixin
from tienda import services
from tienda.forms import (
    AjusteStockForm,
    CategoriaProductoForm,
    ProductoForm,
    VarianteFormSet,
)
from tienda.models import CategoriaProducto, Producto, Variante


class TiendaHabilitadaMixin:
    """Cartel con 200 si el gimnasio no tiene el add-on (no 403: bajo boost
    sería un click mudo). Mismo molde que `tenants.mixins.BloqueadoEnCuentaDemoMixin`:
    va DESPUÉS de `StaffRequiredMixin` en las bases (el rol ya está validado
    cuando llega acá) y se cuida solo del anónimo y del usuario sin Perfil.
    `requiere_add_on = False` en las vistas de pedidos: con la tienda apagada
    el staff tiene que poder cerrar lo que quedó vivo."""

    requiere_add_on = True

    def dispatch(self, request, *args, **kwargs):
        if self.requiere_add_on and request.user.is_authenticated:
            try:
                gimnasio = request.user.perfil.gimnasio
            except ObjectDoesNotExist:
                gimnasio = None
            if gimnasio is not None and not gimnasio.puede_usar_tienda:
                return render(request, "tienda/no_habilitada.html")
        return super().dispatch(request, *args, **kwargs)


class TiendaStaffMixin(StaffRequiredMixin, TiendaHabilitadaMixin, TenantScopedMixin):
    pass


class _CategoriaFormMixin:
    model = CategoriaProducto
    form_class = CategoriaProductoForm
    template_name = "tienda/categoria_form.html"
    success_url = reverse_lazy("tienda:categorias")

    def form_valid(self, form):
        messages.success(self.request, "Categoría guardada.")
        return super().form_valid(form)


# Alta y edición como hermanas sobre un mixin común, NO `Update(Create,
# UpdateView)`: con esa herencia `BaseCreateView.get/post` quedan antes en el
# MRO, ponen `self.object = None` y la «edición» crea un registro nuevo (y no
# da 404 para lo ajeno).
class CategoriaCreateView(TiendaStaffMixin, _CategoriaFormMixin, CreateView):
    pass


class CategoriaUpdateView(TiendaStaffMixin, _CategoriaFormMixin, UpdateView):
    pass


class CategoriaBorrarView(TiendaStaffMixin, BorrarConExplicacionView):
    model = CategoriaProducto

    def get_url_exito(self):
        return reverse("tienda:categorias")

    def get_mensaje_exito(self):
        return "Categoría eliminada. Sus productos quedaron sin categoría."


class ProductoListView(TiendaStaffMixin, ListView):
    model = Producto
    template_name = "tienda/producto_list.html"

    def get_queryset(self):
        return super().get_queryset().select_related("categoria").prefetch_related("variantes")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["reservado"] = services.reservado_por_variante(self.gimnasio)
        context["form_ajuste"] = AjusteStockForm()
        context["umbral_pocas"] = services.UMBRAL_POCAS_UNIDADES
        return context


class _ProductoFormMixin:
    model = Producto
    form_class = ProductoForm
    template_name = "tienda/producto_form.html"
    success_url = reverse_lazy("tienda:productos")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "formset" not in context:
            context["formset"] = VarianteFormSet(
                self.request.POST or None, instance=self.object or Producto()
            )
        return context

    def form_valid(self, form):
        form.instance.gimnasio = self.gimnasio
        formset = VarianteFormSet(self.request.POST, instance=form.instance)
        if not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        with transaction.atomic():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            # Las desactivadas en `_should_delete_form` (tenían ventas).
            for f in formset.forms:
                if f.instance.pk and f.instance.activa is False and f.cleaned_data.get("DELETE"):
                    f.instance.save(update_fields=["activa"])
        messages.success(self.request, "Producto guardado.")
        return redirect(self.success_url)


class ProductoCreateView(TiendaStaffMixin, _ProductoFormMixin, CreateView):
    pass


class ProductoUpdateView(TiendaStaffMixin, _ProductoFormMixin, UpdateView):
    pass


class ProductoBorrarView(TiendaStaffMixin, BorrarConExplicacionView):
    """`core/borrado.py` mira un solo nivel y prometería un borrado que la
    PROTECT de `ItemPedido→Variante` (dos niveles abajo) hace imposible."""

    model = Producto
    alternativa = "Destildá «Publicado» para sacarlo de la tienda sin perder su historial."

    def get_url_exito(self):
        return reverse("tienda:productos")

    def get_mensaje_exito(self):
        return "Producto eliminado."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if services.tiene_ventas(self.object):
            context["bloqueos"] = "pedidos de alumnos"
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if services.tiene_ventas(self.object):
            messages.error(request, "No se puede eliminar: tiene pedidos de alumnos. " + self.alternativa)
            return redirect(request.path)
        return super().post(request, *args, **kwargs)


class VarianteAjustarView(TiendaStaffMixin, View):
    def post(self, request, pk):
        variante = get_object_or_404(Variante, pk=pk, producto__gimnasio=self.gimnasio)
        form = AjusteStockForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Escribí un número entero, por ejemplo 10 o -1.")
        else:
            try:
                services.ajustar_stock(variante, form.cleaned_data["delta"])
                messages.success(request, f"{variante}: quedan {variante.stock} disponibles.")
            except services.ErrorDeTienda as error:
                messages.error(request, error.mensaje)
        return redirect("tienda:productos")


class FotoProductoView(View):
    """Pública y en `URLS_SIN_BLOQUEO` (sin cookie en una respuesta que se
    cachea un año). La foto de un suplemento no es dato sensible; no se exige
    `publicado` para que el staff vea sus borradores. A diferencia del logo
    (`ArchivoDeGimnasioView`, que aparece en todas las páginas), no se cachea
    el contenido en LocMem: son muchas fotos de hasta 1200 px por proceso, y
    con `immutable` el navegador ya no las vuelve a pedir."""

    def get(self, request, pk):
        producto = get_object_or_404(
            Producto.objects.filter(
                Q(gimnasio__es_demo=True) | Q(gimnasio__tienda_habilitada=True),
                gimnasio__activo=True,
            ),
            pk=pk,
        )
        if not producto.foto:
            raise Http404
        with producto.foto.open("rb") as archivo:
            contenido = archivo.read()
        tipo = mimetypes.guess_type(producto.foto.name)[0] or "image/jpeg"
        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Cache-Control"] = "public, max-age=31536000, immutable"
        return respuesta
```

Orden de bases: `TiendaStaffMixin` (que trae `TenantScopedMixin`) primero, así su `get_queryset` acota al gimnasio (lo ajeno da 404) y su `form_valid` pone `gimnasio` antes de llegar al `form_valid` del mixin de form, que no llama a `super`.

- [ ] **Step 5: URLs** — `tienda/urls.py` (completo para todo el hito; las vistas de las Tareas 5 y 7 se agregan ahí):

```python
from django.urls import path

from tienda import views

app_name = "tienda"

urlpatterns = [
    # Staff
    path("productos/", views.ProductoListView.as_view(), name="productos"),
    path("productos/nuevo/", views.ProductoCreateView.as_view(), name="producto_crear"),
    path("productos/<int:pk>/editar/", views.ProductoUpdateView.as_view(), name="producto_editar"),
    path("productos/<int:pk>/eliminar/", views.ProductoBorrarView.as_view(), name="producto_borrar"),
    path("variantes/<int:pk>/ajustar/", views.VarianteAjustarView.as_view(), name="variante_ajustar"),
    path("categorias/", views.CategoriaListView.as_view(), name="categorias"),
    path("categorias/nueva/", views.CategoriaCreateView.as_view(), name="categoria_crear"),
    path("categorias/<int:pk>/editar/", views.CategoriaUpdateView.as_view(), name="categoria_editar"),
    path("categorias/<int:pk>/eliminar/", views.CategoriaBorrarView.as_view(), name="categoria_borrar"),
    # Foto pública (nombre único: `URLS_SIN_BLOQUEO` compara sin namespace)
    path("productos/<int:pk>/foto/", views.FotoProductoView.as_view(), name="tienda_foto_producto"),
]
```

En `config/urls.py`, después de `path("suscripcion/", …)`: `path("tienda/", include("tienda.urls")),`. En `plataforma/middleware.py`, sumar `"tienda_foto_producto",` a `URLS_SIN_BLOQUEO` con un comentario de una línea: `# foto de producto: respuesta immutable, sin cookie (ver tienda/views.py)`.

- [ ] **Step 6: Templates.** Copiar la estructura de `templates/ejercicios/ejercicio_list.html` / `ejercicio_form.html` y de `templates/ejercicios/categoria_*.html` (buscar los nombres exactos con `ls templates/ejercicios`).
  - `templates/tienda/no_habilitada.html`: extiende `base.html`, `<div class="tarjeta"><h1>Tienda</h1><p>La tienda no está habilitada para tu gimnasio. Es un servicio adicional: escribinos para activarla.</p></div>`.
  - `producto_list.html`: `<div class="acciones-lista">` con «Nuevo producto», «Categorías», «Pedidos» (`tienda:pedidos`, se crea en la Tarea 5: hasta entonces dejar el link comentado con `{# #}` y descomentarlo en la Tarea 5) y «Configuración» (ídem); una `<table class="tabla">` con columnas Producto (foto chica con `<img src="{{ p.foto_url_cacheable }}" alt="" width="48" height="48" loading="lazy">` si tiene), Precio (`$ {{ p.precio|floatformat:"2g" }}` dentro de `<span class="tabular-nums">`), y por cada variante una fila anidada con «disponible / reservado» (`{{ v.stock }}` / `{% with r=reservado|get_item:v.pk %}` — **no hay filtro `get_item`**: armar en la vista una lista `filas = [(producto, [(variante, reservado.get(variante.pk, 0)), ...]), ...]` y pasarla como `filas`, en vez de un filtro nuevo) y el form de ajuste (`<form method="post" action="{% url 'tienda:variante_ajustar' v.pk %}">{% csrf_token %}<input type="number" name="delta" ...><button class="boton-secundario">Ajustar</button></form>`), Publicado (badge), Editar/Eliminar. Junto al disponible de cada variante activa: `{% if v.stock == 0 %}<span class="badge">Sin stock</span>{% elif v.stock <= umbral_pocas %}<span class="badge" data-stock-bajo>Stock bajo</span>{% endif %}`. Título: `<h1>Tienda{% if user.perfil.gimnasio.es_demo %} <span class="badge" data-adicional>Adicional</span>{% endif %}</h1>` (el chip de la demo; su test vive en la Tarea 5, junto al de la barra).
  - `producto_form.html`: `<form method="post" enctype="multipart/form-data" novalidate hx-boost="false">`, `{{ form.as_p }}`, `{{ formset.management_form }}`, `{% if formset.non_form_errors %}<div class="config-error">{{ formset.non_form_errors }}</div>{% endif %}`, y por cada `f in formset`: `{{ f.id }}` + `{% include 'partials/campo_form.html' with campo=f.nombre %}` + (si existe) `{% include 'partials/campo_form.html' with campo=f.stock_inicial %}` + `{% include 'partials/campo_form.html' with campo=f.activa %}` + la casilla DELETE sólo si `not f.instance.pk or not f.instance.items.exists` — **esto es N+1 en el template**: en `get_context_data` calcular `con_ventas = set(ItemPedido.objects.filter(variante__producto=self.object).values_list("variante_id", flat=True))` (una query) y pasarlo; en el template `{% if f.instance.pk not in con_ventas %}{{ f.DELETE }}{% endif %}`. Usar SIEMPRE el partial para los campos del formset: `core.tests.ErroresDeFormularioVisiblesTests` sólo mira `form.<campo>` y NO marcaría un `{{ f.nombre }}` sin su error (lo cubre `test_errores_del_formset_se_ven`).
  - La vista de lista tiene que mostrar el link «Ver en la tienda del alumno» sólo si la configuración está publicada (opcional; si complica, omitir).

Actualizar `ProductoListView.get_context_data` para armar `filas` (comprensión sobre `object_list`, sin queries extra gracias al `prefetch_related`).

- [ ] **Step 7: Correr** `$PY manage.py test tienda core.tests -v 1` → PASS (incluye el barrido de errores de formulario). Después, mutación: en `VarianteForm.save` cambiar `update_fields=["nombre", "activa"]` por un `save()` pelado **y** agregar `"stock"` a `fields` → `test_editar_el_nombre_no_pisa_el_stock_reservado` debe fallar; revertir.

- [ ] **Step 8: Commit**

```bash
npm run build:css
git status --short
git add tienda templates/tienda config/urls.py plataforma/middleware.py static/css/app.css
git commit -m "feat(tienda): productos, variantes, categorías y foto para el staff"
```

---

### Task 5: Staff — pedidos, configuración, tarjeta del panel y link en la barra

**Files:**
- Modify: `tienda/forms.py`, `tienda/views.py`, `tienda/urls.py`, `tienda/tests_vistas_staff.py`, `tenants/views.py` (contexto del panel), `templates/tenants/home.html` (tarjeta), `templates/base.html:247-257` (link)
- Create: `templates/tienda/pedido_list.html`, `templates/tienda/pedido_detalle.html`, `templates/tienda/configuracion_form.html`

**Interfaces:**
- Consumes: `services.marcar_pagado`, `rechazar_comprobante` (vuelve a `PENDIENTE_PAGO`, no cancela), `entregar`, `cancelar_por_staff`, `resolver_cancelacion`, `vencer_pedidos`, `contadores_panel`, `obtener_configuracion`.
- Produces: URLs `tienda:pedidos` (GET `?filtro=revisar|entregar|cancelaciones|todos`), `tienda:pedido_detalle <pk>`, `tienda:pedido_accion <pk>` (POST `accion=` `pagado|rechazar|entregar|cancelar|aceptar_cancelacion|rechazar_cancelacion`, `motivo=`), `tienda:configuracion`. Contexto del panel: `tienda_contadores` (dict o `None`).

- [ ] **Step 1: Tests que fallan** — agregar a `tienda/tests_vistas_staff.py`:

```python
class PedidosStaffTests(_BaseStaff):
    def setUp(self):
        super().setUp()
        self.producto, self.variante = self.crear_producto(stock=5)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 2}, suplantando=False,
        )

    def accion(self, accion, **extra):
        return self.client.post(
            reverse("tienda:pedido_accion", args=[self.pedido.pk]),
            {"accion": accion, **extra}, follow=True,
        )

    def test_listado_filtra_a_revisar(self):
        # Se busca el link al detalle y no «#<pk>»: `base.html` inyecta el
        # color del gimnasio (`#1d6f56`) y «#1» aparece en cualquier página.
        detalle = reverse("tienda:pedido_detalle", args=[self.pedido.pk])
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertNotContains(r, detalle)
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertContains(r, detalle)

    @patch("notificaciones.services.notificar_pedido_al_alumno")
    def test_marcar_pagado_avisa_al_alumno(self, aviso):
        with self.captureOnCommitCallbacks(execute=True):
            self.accion("pagado")
        self.assertEqual(aviso.call_args.args[1], "confirmado")

    def test_marcar_pagado_y_entregar(self):
        self.accion("pagado")
        self.accion("entregar")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.ENTREGADO)

    def test_rechazar_pide_otro_comprobante_y_sigue_reservado(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.accion("rechazar", motivo="No se lee")
        self.assertContains(r, "puede subir otro")
        self.pedido.refresh_from_db()
        self.assertEqual(
            (self.pedido.estado, self.pedido.motivo), (Pedido.Estado.PENDIENTE_PAGO, "No se lee")
        )
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 3)  # 5 - 2: sigue reservado

    def test_cancelar_desde_revision_si_devuelve_el_stock(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        self.accion("cancelar", motivo="No llegó la plata")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.CANCELADO)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 5)

    def test_accion_invalida_es_cartel_no_500(self):
        r = self.accion("entregar")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ya cambió de estado")

    def test_pedido_ajeno_404(self):
        otro_alumno = Alumno.objects.create(gimnasio=self.otro, nombre="B", apellido="P")
        _, var = self.crear_producto(gimnasio=self.otro)
        ajeno = services.crear_pedido(gimnasio=self.otro, alumno=otro_alumno,
                                      carrito={var.pk: 1}, suplantando=False)
        self.assertEqual(self.client.get(reverse("tienda:pedido_detalle", args=[ajeno.pk])).status_code, 404)
        r = self.client.post(reverse("tienda:pedido_accion", args=[ajeno.pk]), {"accion": "pagado"})
        self.assertEqual(r.status_code, 404)

    def test_listado_vence_perezosamente(self):
        from django.utils import timezone
        from datetime import timedelta

        Pedido.objects.filter(pk=self.pedido.pk).update(
            reservado_hasta=timezone.now() - timedelta(hours=1)
        )
        self.client.get(reverse("tienda:pedidos"))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.VENCIDO)


class TiendaApagadaTests(_BaseStaff):
    def test_el_staff_sigue_gestionando_los_pedidos_vivos(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pedido)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=entregar")
        self.assertContains(r, reverse("tienda:pedido_detalle", args=[pedido.pk]))
        self.client.post(reverse("tienda:pedido_accion", args=[pedido.pk]), {"accion": "entregar"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        # ...pero productos y configuración no.
        self.assertContains(self.client.get(reverse("tienda:productos")), "no está habilitada")


class ConfiguracionTests(_BaseStaff):
    def test_guardar(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "publicada": "on", "horas_reserva_transferencia": "48",
            "max_pedidos_activos": "3", "instrucciones_retiro": "Recepción",
        })
        self.assertRedirects(r, reverse("tienda:configuracion"))
        from tienda.models import obtener_configuracion
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.publicada, c.horas_reserva_transferencia), (True, 48))

    def test_plazo_fuera_de_rango_es_error_visible(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "horas_reserva_transferencia": "0", "max_pedidos_activos": "2",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")  # o la clase del partial


class PanelYBarraTests(_BaseStaff):
    def test_tarjeta_del_panel_con_pendientes(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Pedidos de la tienda")
        self.assertContains(r, reverse("tienda:pedidos") + "?filtro=revisar")

    def test_sin_add_on_ni_link_ni_tarjeta(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, reverse("tienda:productos"))
        self.assertNotContains(r, "Pedidos de la tienda")

    def test_con_add_on_link_en_la_barra(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, f'href="{reverse("tienda:productos")}"')

    def test_chip_adicional_solo_en_la_demo(self):
        """La portada dice «todas las funciones incluidas» hasta el Hito 3:
        en la demo, la tienda se marca como adicional."""
        self.assertNotContains(self.client.get(reverse("home")), "data-adicional")
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-adicional")
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.assertContains(self.client.get(reverse("home")), "data-adicional")
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-adicional")
```

Run → FAIL (`NoReverseMatch: tienda:pedidos`).

- [ ] **Step 2: Forms** — agregar a `tienda/forms.py`:

```python
class ConfiguracionTiendaForm(TenantScopedModelForm):
    class Meta:
        model = ConfiguracionTienda
        fields = ["publicada", "horas_reserva_transferencia", "max_pedidos_activos", "instrucciones_retiro"]


class MotivoForm(forms.Form):
    motivo = forms.CharField(max_length=MAX_LARGO_MOTIVO, required=False, label="Motivo")
```

- [ ] **Step 3: Vistas** — agregar a `tienda/views.py` (y los imports `Pedido`, `ConfiguracionTiendaForm`, `MotivoForm`, `obtener_configuracion`, `DetailView`, `transaction.on_commit`):

```python
FILTROS_PEDIDOS = {
    "revisar": Q(estado=Pedido.Estado.EN_REVISION),
    "entregar": Q(estado=Pedido.Estado.PAGADO),
    "cancelaciones": services.Q_CANCELACION_PENDIENTE,
    "todos": Q(),
}


class PedidoListView(TiendaStaffMixin, ListView):
    requiere_add_on = False
    model = Pedido
    template_name = "tienda/pedido_list.html"
    paginate_by = 50

    def get(self, request, *args, **kwargs):
        services.vencer_pedidos(gimnasio=self.gimnasio)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        self.filtro = self.request.GET.get("filtro", "revisar")
        if self.filtro not in FILTROS_PEDIDOS:
            self.filtro = "revisar"
        return (
            super().get_queryset()
            .filter(FILTROS_PEDIDOS[self.filtro])
            .select_related("alumno")
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filtro"] = self.filtro
        context["contadores"] = services.contadores_panel(self.gimnasio)
        return context


class PedidoDetailView(TiendaStaffMixin, DetailView):
    requiere_add_on = False
    model = Pedido
    template_name = "tienda/pedido_detalle.html"

    def get_queryset(self):
        return super().get_queryset().select_related("alumno").prefetch_related("items")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_motivo"] = MotivoForm()
        return context


class PedidoAccionView(TiendaStaffMixin, View):
    requiere_add_on = False

    def post(self, request, pk):
        pedido = get_object_or_404(Pedido.objects.for_gimnasio(self.gimnasio), pk=pk)
        form = MotivoForm(request.POST)
        motivo = form.cleaned_data["motivo"] if form.is_valid() else request.POST.get("motivo", "")
        accion = request.POST.get("accion")
        avisos = {
            "pagado": ("Pago confirmado. Avisamos al alumno que puede retirar.", "confirmado"),
            "rechazar": (
                "Comprobante rechazado: el alumno puede subir otro y el stock sigue reservado.",
                "rechazado",
            ),
            "entregar": ("Pedido entregado.", None),
            "cancelar": ("Pedido cancelado; el stock volvió a la tienda.", "cancelado"),
            "aceptar_cancelacion": ("Cancelación aceptada.", "cancelacion_resuelta"),
            "rechazar_cancelacion": ("Cancelación rechazada.", "cancelacion_resuelta"),
        }
        try:
            if accion == "pagado":
                services.marcar_pagado(pedido)
            elif accion == "rechazar":
                services.rechazar_comprobante(pedido, motivo=motivo)
            elif accion == "entregar":
                services.entregar(pedido)
            elif accion == "cancelar":
                services.cancelar_por_staff(pedido, motivo=motivo)
            elif accion == "aceptar_cancelacion":
                services.resolver_cancelacion(pedido, aceptar=True, motivo=motivo)
            elif accion == "rechazar_cancelacion":
                services.resolver_cancelacion(pedido, aceptar=False, motivo=motivo)
            else:
                raise services.ErrorDeTienda("estado_invalido")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            texto, aviso = avisos[accion]
            messages.success(request, texto)
            if aviso:
                from notificaciones import services as avisos_push

                transaction.on_commit(lambda: avisos_push.notificar_pedido_al_alumno(pedido, aviso))
        return redirect("tienda:pedido_detalle", pk=pedido.pk)


class ConfiguracionView(TiendaStaffMixin, UpdateView):
    form_class = ConfiguracionTiendaForm
    template_name = "tienda/configuracion_form.html"
    success_url = reverse_lazy("tienda:configuracion")

    def get_object(self, queryset=None):
        return obtener_configuracion(self.gimnasio)

    def get_context_data(self, **kwargs):
        from pagos.models import MedioCobro

        context = super().get_context_data(**kwargs)
        context["medios_cobro"] = MedioCobro.objects.for_gimnasio(self.gimnasio).filter(activo=True)
        return context

    def form_valid(self, form):
        messages.success(self.request, "Configuración guardada.")
        return super().form_valid(form)
```

**Importante:** `notificar_pedido_al_alumno` se crea en la Tarea 8. Para que esta tarea quede en verde sola, crearla ya en `notificaciones/services.py` como función mínima y completarla en la Tarea 8:

```python
def notificar_pedido_al_alumno(pedido, aviso) -> None:
    """Completada en la Tarea 8 del plan de la tienda."""
    return None
```

URLs a agregar en `tienda/urls.py`:

```python
    path("pedidos/", views.PedidoListView.as_view(), name="pedidos"),
    path("pedidos/<int:pk>/", views.PedidoDetailView.as_view(), name="pedido_detalle"),
    path("pedidos/<int:pk>/accion/", views.PedidoAccionView.as_view(), name="pedido_accion"),
    path("configuracion/", views.ConfiguracionView.as_view(), name="configuracion"),
```

Y descomentar en `producto_list.html` los links a Pedidos y Configuración.

- [ ] **Step 4: Templates.**
  - `pedido_list.html`: pestañas como links (`?filtro=revisar` «A revisar ({{ contadores.a_revisar }})», `entregar`, `cancelaciones`, `todos`), tabla con `#{{ p.pk }}`, alumno, ítems (`{% for i in p.items.all %}{{ i }}{% endfor %}` — prefetcheado), total `$ {{ p.total|floatformat:"2g" }}`, estado (`{{ p.get_estado_display }}` en `badge`), link al detalle; paginación con links «Anterior / Siguiente» a mano (no existe `partials/paginacion.html`) que **conserven `?filtro=`** (`?filtro={{ filtro }}&page=...`).
  - `pedido_detalle.html`: datos, ítems con subtotal, si hay comprobante: `<a href="{{ object.comprobante.url }}" hx-boost="false" target="_blank" rel="noopener">Ver comprobante</a>` (firmada a propósito: es dato sensible, igual que `pagos`); botones POST según estado: PENDIENTE_PAGO/EN_REVISION → «Marcar pagado»; EN_REVISION → «Rechazar comprobante (pedir otro)» con `{% include 'partials/campo_form.html' with campo=form_motivo.motivo %}` y el texto «El pedido vuelve a esperar el pago con un plazo nuevo y el stock sigue reservado. Para cortar del todo, usá «Cancelar pedido».»; PENDIENTE_PAGO con comprobante (rechazado antes): el link dice «Ver comprobante rechazado» y se muestra «Rechazado: {{ object.motivo }}»; PAGADO → «Entregar»; PENDIENTE_PAGO/EN_REVISION/PAGADO → «Cancelar pedido» con motivo (devuelve el stock); cancelación pedida y no resuelta → «Aceptar cancelación» / «Rechazar cancelación» con motivo y el texto «El alumno pidió la cancelación el …: si aceptás, devolvele la plata por el mismo medio». Cada form con `{% csrf_token %}` e `<input type="hidden" name="accion" value="...">`.
  - `configuracion_form.html`: `{% for campo in form %}{% include 'partials/campo_form.html' with campo=campo %}{% endfor %}` (no `as_p`: el test busca `config-error`, que es lo que pinta el partial), y un bloque «Datos para la transferencia» que lista `medios_cobro` (alias/titular/entidad) con link a la pantalla existente de medios de cobro (buscar su URL con `grep -n "medio" pagos/urls.py`) y el aviso «Si no cargás ningún medio, el alumno no sabe a dónde transferir» cuando la lista está vacía.

- [ ] **Step 5: Panel y barra.** En `tenants/views.py::HomeView`, en la rama de staff, junto a `alumnos_activos_sin_inicio_count`:

```python
            # Tienda (add-on): una sola query agregada. Con el add-on apagado
            # pero pedidos vivos, la tarjeta sigue: el staff tiene que cerrarlos.
            "tienda_contadores": _contadores_tienda(gimnasio),
```

y en el mismo módulo:

```python
def _contadores_tienda(gimnasio):
    from tienda.services import contadores_panel

    contadores = contadores_panel(gimnasio)
    if not gimnasio.puede_usar_tienda and not contadores["vivos"]:
        return None
    if not (contadores["a_revisar"] or contadores["a_entregar"] or contadores["cancelaciones"]):
        return None
    return contadores
```

En `templates/tenants/home.html`, después de la tarjeta «Alumnos sin fecha de inicio»:

```html
  {% if tienda_contadores %}
    <div class="tarjeta aviso-urgente">
      <h2>Pedidos de la tienda</h2>
      <ul>
        {% if tienda_contadores.a_revisar %}<li><a href="{% url 'tienda:pedidos' %}?filtro=revisar">{{ tienda_contadores.a_revisar }} comprobante{{ tienda_contadores.a_revisar|pluralize }} para revisar</a></li>{% endif %}
        {% if tienda_contadores.a_entregar %}<li><a href="{% url 'tienda:pedidos' %}?filtro=entregar">{{ tienda_contadores.a_entregar }} pedido{{ tienda_contadores.a_entregar|pluralize }} para entregar</a></li>{% endif %}
        {% if tienda_contadores.cancelaciones %}<li><a href="{% url 'tienda:pedidos' %}?filtro=cancelaciones">{{ tienda_contadores.cancelaciones }} pedido{{ tienda_contadores.cancelaciones|pluralize }} de cancelación</a></li>{% endif %}
      </ul>
    </div>
  {% endif %}
```

En `templates/base.html`, dentro de `nav-staff`, después de `Novedades`:

```html
      {% if user.perfil.gimnasio.puede_usar_tienda %}<a href="{% url 'tienda:productos' %}">Tienda{% if user.perfil.gimnasio.es_demo %} <span class="badge" data-adicional>Adicional</span>{% endif %}</a>{% endif %}
```

y actualizar el comentario de la línea ~242 («una nav de nueve links») a «una nav de nueve o diez links».

Costo aceptado: `_contadores_tienda` suma UNA query agregada al panel de todo gimnasio (también sin tienda, porque tiene que detectar pedidos vivos con el add-on apagado). No hay test de cantidad absoluta de queries del panel: el de antes/después (`tenants/tests.py:1050`, `test_el_conteo_es_una_query_aunque_crezca_el_padron`) usa un gimnasio sin tienda y compara dos cargas, así que no cambia. No agregar ni ajustar números a ciegas.

- [ ] **Step 6: Correr** `$PY manage.py test tienda tenants core -v 1` → PASS.

- [ ] **Step 7: Commit**

```bash
npm run build:css
git status --short
git add tienda templates notificaciones/services.py tenants/views.py static/css/app.css
git commit -m "feat(tienda): pedidos y configuración para el staff, tarjeta del panel y link en la barra"
```

---

### Task 6: Estadísticas de ventas en el panel y «Vendido este mes»

**Files:**
- Create: `tienda/analitica.py`, `tienda/tests_analitica.py`
- Modify: `tienda/views.py` (`PedidoListView.get_context_data`), `templates/tienda/pedido_list.html`, `tenants/views.py` (`HomeView` + helper `_ventas_tienda`), `templates/tenants/home.html` (tarjeta, `json_script` y gráfico), `tienda/tests_vistas_staff.py`

**Interfaces:**
- Consumes: `Pedido`, `ItemPedido` (Tarea 1); `services.crear_pedido`, `services.marcar_pagado` (Tareas 2-3, sólo en tests); `PedidoListView` (Tarea 5); `tenants/analitica.py::_meses_hacia_atras` y `_etiqueta_mes` (se reusan a propósito, con un comentario, igual que `_validar_imagen` en la Tarea 4).
- Produces:
  - `ESTADOS_VENDIDOS = (PAGADO, ENTREGADO)`.
  - `ventas_por_mes(gimnasio, *, meses=12, hoy=None) -> list[dict]` con `mes: date`, `etiqueta: str`, `total: float`, `pedidos: int` — una fila por mes aunque esté vacío, UNA query.
  - `productos_mas_vendidos(gimnasio, *, dias=90, limite=5, hoy=None) -> list[dict]` con `producto_id`, `nombre`, `unidades: int`, `total: float` — UNA query.
  - `resumen_del_mes(gimnasio, *, hoy=None) -> dict` con `total`, `pedidos` — UNA query.
  - `panel_de_ventas(gimnasio, *, hoy=None) -> dict` con `por_mes`, `este_mes`, `mes_anterior`, `ticket_promedio: float | None`, `top`, `hay_ventas: bool` — DOS queries.
  - Contexto: `vendido_mes` en `PedidoListView`; `ventas_tienda` (dict o `None`) en el panel.

Venta = pedido `PAGADO` o `ENTREGADO`, por el mes de `pagado_en` en hora argentina. `DEVUELTO` no cuenta (esa plata se devolvió, aunque reescriba un mes pasado); `PENDIENTE_PAGO`/`EN_REVISION` son expectativa. El `.order_by()` antes del `values().annotate()` es inofensivo y explícito (desde Django 3.1 el `Meta.ordering` de `Pedido` ya no entra al `GROUP BY` de un agregado); lo que sí importa es agrupar por `TruncMonth("pagado_en")` en la zona activa, y eso lo fijan `test_dos_pedidos_del_mismo_mes_suman_en_una_fila` y `test_pagado_a_las_2230_del_ultimo_dia_cae_en_ese_mes`.

- [ ] **Step 1: Tests que fallan** — `tienda/tests_analitica.py`:

```python
"""Indicadores de ventas de la tienda (molde: los de `tenants/analitica.py`)."""

from datetime import date, datetime
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda import analitica
from tienda.models import ItemPedido, Pedido, Producto, Variante

HOY = date(2026, 10, 31)


def aware(*args):
    return timezone.make_aware(datetime(*args))


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.remera = Producto.objects.create(gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"))
        self.proteina = Producto.objects.create(gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("30000"))
        self.v_remera = Variante.objects.create(producto=self.remera, nombre="M", stock=50)
        self.v_proteina = Variante.objects.create(producto=self.proteina, stock=50)

    def venta(self, pagado_en, *, variante=None, cantidad=1, estado=Pedido.Estado.PAGADO,
              gimnasio=None, alumno=None):
        """Pedido armado a mano (sin `crear_pedido`): acá importa la fecha de
        pago, no el flujo."""
        variante = variante or self.v_remera
        precio = variante.producto.precio
        pedido = Pedido.objects.create(
            gimnasio=gimnasio or self.gimnasio, alumno=alumno or self.alumno, estado=estado,
            total=precio * cantidad, reservado_hasta=pagado_en, pagado_en=pagado_en,
        )
        ItemPedido.objects.create(
            pedido=pedido, variante=variante, nombre=str(variante),
            precio_unitario=precio, cantidad=cantidad,
        )
        return pedido


class VentasPorMesTests(_Base):
    def test_una_fila_por_mes_aunque_este_vacio(self):
        filas = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)
        self.assertEqual(len(filas), 12)
        self.assertEqual((filas[0]["mes"], filas[-1]["mes"]), (date(2025, 11, 1), date(2026, 10, 1)))
        self.assertTrue(all(f["total"] == 0 and f["pedidos"] == 0 for f in filas))

    def test_dos_pedidos_del_mismo_mes_suman_en_una_fila(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina, cantidad=2)
        octubre = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]
        self.assertEqual((octubre["total"], octubre["pedidos"]), (75000.0, 2))

    def test_entregado_cuenta_y_devuelto_vencido_o_pendiente_no(self):
        self.venta(aware(2026, 10, 5, 10, 0), estado=Pedido.Estado.ENTREGADO)
        for estado in (Pedido.Estado.DEVUELTO, Pedido.Estado.VENCIDO, Pedido.Estado.CANCELADO,
                       Pedido.Estado.PENDIENTE_PAGO, Pedido.Estado.EN_REVISION):
            self.venta(aware(2026, 10, 5, 10, 0), estado=estado)
        octubre = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]
        self.assertEqual((octubre["total"], octubre["pedidos"]), (15000.0, 1))

    def test_otro_gimnasio_no_suma(self):
        alumno = Alumno.objects.create(gimnasio=self.otro, nombre="B", apellido="P")
        ajeno = Producto.objects.create(gimnasio=self.otro, nombre="X", precio=Decimal("999"))
        variante = Variante.objects.create(producto=ajeno, stock=5)
        self.venta(aware(2026, 10, 5, 10, 0), variante=variante, gimnasio=self.otro, alumno=alumno)
        self.assertEqual(analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]["pedidos"], 0)
        self.assertEqual(analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY), [])

    def test_pagado_a_las_2230_del_ultimo_dia_cae_en_ese_mes(self):
        """31/10 22:30 en Argentina = 1/11 01:30 UTC. Agrupado en UTC caería
        en noviembre, fuera de la ventana que termina en octubre."""
        self.venta(aware(2026, 10, 31, 22, 30))
        self.assertEqual(analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]["pedidos"], 1)

    def test_el_costo_no_crece_con_la_cantidad_de_pedidos(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 9, 5, 10, 0))
        with CaptureQueriesContext(connection) as pocos:
            analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        for dia in range(1, 21):
            self.venta(aware(2026, 9, dia, 11, 0), variante=self.v_proteina)
            self.venta(aware(2026, 10, dia, 11, 0))
        with CaptureQueriesContext(connection) as muchos:
            panel = analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        self.assertEqual(len(pocos), len(muchos))
        self.assertEqual(len(muchos), 2)
        self.assertEqual(panel["mes_anterior"]["pedidos"], 21)


class ProductosMasVendidosTests(_Base):
    def test_ordena_por_unidades_y_respeta_la_ventana(self):
        self.venta(aware(2026, 10, 5, 10, 0), cantidad=1)
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina, cantidad=3)
        self.venta(aware(2026, 6, 1, 10, 0), cantidad=10)  # fuera de los 90 días
        top = analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY)
        self.assertEqual([(p["nombre"], p["unidades"]) for p in top], [("Proteína", 3), ("Remera", 1)])
        self.assertEqual(top[0]["total"], 90000.0)

    def test_las_variantes_de_un_producto_se_suman(self):
        talle_l = Variante.objects.create(producto=self.remera, nombre="L", stock=5)
        self.venta(aware(2026, 10, 5, 10, 0), cantidad=1)
        self.venta(aware(2026, 10, 6, 10, 0), variante=talle_l, cantidad=2)
        top = analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY)
        self.assertEqual([(p["nombre"], p["unidades"]) for p in top], [("Remera", 3)])


class PanelDeVentasTests(_Base):
    def test_ticket_promedio_y_sin_ventas(self):
        vacio = analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        self.assertFalse(vacio["hay_ventas"])
        self.assertIsNone(vacio["ticket_promedio"])
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina)
        self.assertEqual(analitica.panel_de_ventas(self.gimnasio, hoy=HOY)["ticket_promedio"], 22500.0)

    def test_resumen_del_mes_es_una_query(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        with self.assertNumQueries(1):
            resumen = analitica.resumen_del_mes(self.gimnasio, hoy=HOY)
        self.assertEqual(resumen, {"total": 15000.0, "pedidos": 1})
```

Y en `tienda/tests_vistas_staff.py`:

```python
class VentasEnPantallasTests(_BaseStaff):
    def setUp(self):
        super().setUp()
        _, variante = self.crear_producto(stock=5)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 2}, suplantando=False)
        services.marcar_pagado(pedido)

    def test_vendido_este_mes_en_el_listado_de_pedidos(self):
        r = self.client.get(reverse("tienda:pedidos"))
        self.assertContains(r, "Vendido este mes")
        self.assertContains(r, "30.000")

    def test_tarjeta_de_ventas_en_el_panel(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Ventas de la tienda")
        self.assertContains(r, 'id="ventas-tienda-data"')

    def test_sin_tienda_ni_tarjeta_ni_queries(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        with patch("tienda.analitica.panel_de_ventas", side_effect=AssertionError("no se llama sin tienda")):
            r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Ventas de la tienda")
        self.assertNotContains(r, "ventas-tienda-data")
```

Run: `$PY manage.py test tienda.tests_analitica tienda.tests_vistas_staff.VentasEnPantallasTests -v 2` → ERROR (`ImportError: cannot import name 'analitica'`).

- [ ] **Step 2: Implementación** — `tienda/analitica.py`:

```python
"""Indicadores de ventas de la tienda para el dashboard del staff.

Mismo molde que `tenants/analitica.py` (se reusan sus helpers a propósito):
una fila por mes AUNQUE esté vacío (un gráfico que se saltea meses miente
sobre la tendencia) y UNA query por indicador, nunca un bucle por mes.

Venta = pedido `PAGADO` o `ENTREGADO`, por el mes de `pagado_en` en hora
argentina (`TruncMonth` y `__date` usan la zona activa). `DEVUELTO` no
cuenta: esa plata se devolvió, aunque eso reescriba un mes ya pasado.
"""

from datetime import timedelta

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from tenants.analitica import _etiqueta_mes, _meses_hacia_atras
from tienda.models import ItemPedido, Pedido

ESTADOS_VENDIDOS = (Pedido.Estado.PAGADO, Pedido.Estado.ENTREGADO)


def _como_fecha(valor):
    # `TruncMonth` sobre un DateTimeField devuelve datetime: se normaliza a
    # `date`, como `altas_y_bajas_por_mes`.
    return valor.date() if hasattr(valor, "date") else valor


def ventas_por_mes(gimnasio, *, meses=12, hoy=None):
    hoy = hoy or timezone.localdate()
    ventana = _meses_hacia_atras(hoy, meses)
    filas = {
        _como_fecha(fila["mes_venta"]): fila
        for fila in Pedido.objects.for_gimnasio(gimnasio)
        .filter(estado__in=ESTADOS_VENDIDOS, pagado_en__date__gte=ventana[0])
        # Explícito e inofensivo: Django >= 3.1 ya no agrupa por `Meta.ordering`.
        .order_by()
        .annotate(mes_venta=TruncMonth("pagado_en"))
        .values("mes_venta")
        .annotate(total=Sum("total"), pedidos=Count("pk"))
    }
    return [
        {
            "mes": mes,
            "etiqueta": _etiqueta_mes(mes),
            "total": float(filas.get(mes, {}).get("total") or 0),
            "pedidos": filas.get(mes, {}).get("pedidos", 0),
        }
        for mes in ventana
    ]


def productos_mas_vendidos(gimnasio, *, dias=90, limite=5, hoy=None):
    hoy = hoy or timezone.localdate()
    importe = ExpressionWrapper(
        F("cantidad") * F("precio_unitario"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    filas = (
        ItemPedido.objects.filter(
            pedido__gimnasio=gimnasio,
            pedido__estado__in=ESTADOS_VENDIDOS,
            pedido__pagado_en__date__gt=hoy - timedelta(days=dias),
        )
        .values("variante__producto_id", "variante__producto__nombre")
        .annotate(unidades=Sum("cantidad"), total=Sum(importe))
        .order_by("-unidades", "variante__producto__nombre")[:limite]
    )
    return [
        {
            "producto_id": f["variante__producto_id"],
            "nombre": f["variante__producto__nombre"],
            "unidades": f["unidades"],
            "total": float(f["total"] or 0),
        }
        for f in filas
    ]


def resumen_del_mes(gimnasio, *, hoy=None):
    """«Vendido este mes» del listado de pedidos: UNA query."""
    fila = ventas_por_mes(gimnasio, meses=1, hoy=hoy)[0]
    return {"total": fila["total"], "pedidos": fila["pedidos"]}


def panel_de_ventas(gimnasio, *, hoy=None):
    """La tarjeta del dashboard: DOS queries, sin importar cuántos pedidos haya."""
    por_mes = ventas_por_mes(gimnasio, hoy=hoy)
    este, anterior = por_mes[-1], por_mes[-2]
    return {
        "por_mes": por_mes,
        "este_mes": este,
        "mes_anterior": anterior,
        "ticket_promedio": este["total"] / este["pedidos"] if este["pedidos"] else None,
        "top": productos_mas_vendidos(gimnasio, hoy=hoy),
        "hay_ventas": any(f["pedidos"] for f in por_mes),
    }
```

- [ ] **Step 3: «Vendido este mes».** En `PedidoListView.get_context_data` (Tarea 5) agregar `context["vendido_mes"] = analitica.resumen_del_mes(self.gimnasio)` (import `from tienda import analitica` arriba de `views.py`). En `pedido_list.html`, arriba de las pestañas: `<p class="tabular-nums"><strong>Vendido este mes: $ {{ vendido_mes.total|floatformat:"2g" }}</strong> · {{ vendido_mes.pedidos }} pedido{{ vendido_mes.pedidos|pluralize }}</p>`.

- [ ] **Step 4: Tarjeta del dashboard.** En `tenants/views.py::HomeView`, al lado de `"ingresos_mensuales": analitica.ingresos_por_mes(gimnasio),`:

```python
            # Tienda (add-on): ventas por mes y lo más vendido. Sin tienda,
            # ni una query (lo fija `test_sin_tienda_ni_tarjeta_ni_queries`).
            "ventas_tienda": _ventas_tienda(gimnasio),
```

y en el mismo módulo, al lado de `_contadores_tienda`:

```python
def _ventas_tienda(gimnasio):
    if not gimnasio.puede_usar_tienda:
        return None
    from tienda import analitica as analitica_tienda

    return analitica_tienda.panel_de_ventas(gimnasio)
```

(El `patch("tienda.analitica.panel_de_ventas")` del test funciona porque la función se busca en el módulo en cada llamada: NO importarla con `from tienda.analitica import panel_de_ventas`.)

En `templates/tenants/home.html`, inmediatamente después del `</div>` que cierra la tarjeta «Ingresos cobrados por mes» (antes de la de «Altas y bajas de alumnos»):

```html
  {% if ventas_tienda %}
  <div class="tarjeta" id="ventas-tienda">
    <h2>Ventas de la tienda</h2>
    {% if ventas_tienda.hay_ventas %}
      <p class="tabular-nums">
        <strong>Este mes (en curso): $ {{ ventas_tienda.este_mes.total|floatformat:"0g" }}</strong>
        · {{ ventas_tienda.este_mes.pedidos }} pedido{{ ventas_tienda.este_mes.pedidos|pluralize }}
        · mes anterior: $ {{ ventas_tienda.mes_anterior.total|floatformat:"0g" }}
        {% if ventas_tienda.ticket_promedio %}· ticket promedio: $ {{ ventas_tienda.ticket_promedio|floatformat:"0g" }}{% endif %}
      </p>
      <p class="texto-suave">Pedidos pagados o entregados, por el mes en que se pagaron. Los devueltos no cuentan. El mes actual está en curso: su barra siempre se ve más baja.</p>
      <div class="grafico-contenedor">
        <canvas id="grafico-ventas-tienda"></canvas>
      </div>
      <details class="tabla-detalle">
        <summary>Ver como tabla</summary>
        <div class="tabla-scroll">
          <table class="tabla">
            <thead><tr><th>Mes</th><th>Vendido</th><th>Pedidos</th></tr></thead>
            <tbody>
              {% for fila in ventas_tienda.por_mes %}
                <tr><td>{{ fila.etiqueta }}</td><td class="tabular-nums">$ {{ fila.total|floatformat:"0g" }}</td><td>{{ fila.pedidos }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </details>
      {% if ventas_tienda.top %}
        <h3 class="mt-4 font-semibold">Lo más vendido (últimos 90 días)</h3>
        <ol class="list-decimal pl-5">
          {% for p in ventas_tienda.top %}
            <li>{{ p.nombre }} — {{ p.unidades }} u. · <span class="tabular-nums">$ {{ p.total|floatformat:"0g" }}</span></li>
          {% endfor %}
        </ol>
      {% endif %}
    {% else %}
      <p class="texto-suave">Todavía no hay ventas. Cuando un alumno pague un pedido, lo vas a ver acá.</p>
    {% endif %}
  </div>
  {% endif %}
```

Al lado de `{{ ingresos_mensuales|json_script:"ingresos-data" }}`: `{% if ventas_tienda %}{{ ventas_tienda.por_mes|json_script:"ventas-tienda-data" }}{% endif %}` (el `json_script` serializa `date` sin problema: es `DjangoJSONEncoder`). En el `<script>`, inmediatamente después del bloque de `grafico-ingresos`, el mismo gráfico con sus datos (si no hay tienda, `datosDe` devuelve `[]` y el canvas no existe: no dibuja nada):

```js
      var ventasTiendaData = datosDe("ventas-tienda-data");
      var elVentasTienda = document.getElementById("grafico-ventas-tienda");
      if (elVentasTienda && ventasTiendaData.length) {
        new Chart(elVentasTienda, {
          type: "bar",
          data: {
            labels: ventasTiendaData.map(function (d) { return d.etiqueta; }),
            datasets: [{
              data: ventasTiendaData.map(function (d) { return d.total; }),
              backgroundColor: AZUL,
              borderRadius: 4,
              maxBarThickness: 32,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: {
                beginAtZero: true,
                ticks: {
                  callback: function (v) { return "$ " + v.toLocaleString("es-AR"); },
                },
              },
            },
            maintainAspectRatio: false,
          },
        });
      }
```

- [ ] **Step 5: Correr** `$PY manage.py test tienda tenants core -v 1` → PASS (incluye `test_el_conteo_es_una_query_aunque_crezca_el_padron` del panel, que compara antes/después y no debería cambiar: con tienda, el panel suma dos queries fijas). Después `npm run build:css` y commitear `static/css/app.css` si cambió.

- [ ] **Step 6: Confirmar que los tests muerden.** Una por una, mutar, correr el test (debe FALLAR) y revertir:
  - En `ventas_por_mes`, agrupar por `pagado_en` en vez de `TruncMonth("pagado_en")` (`.values("pagado_en")` y `_como_fecha(fila["pagado_en"])` como clave) → `test_dos_pedidos_del_mismo_mes_suman_en_una_fila` (las claves pasan a ser días y el mes queda en 0).
  - En `ventas_por_mes`, `TruncMonth("pagado_en", tzinfo=datetime.timezone.utc)` (import temporal) → `test_pagado_a_las_2230_del_ultimo_dia_cae_en_ese_mes`.
  - En `_ventas_tienda`, sacar el `if not gimnasio.puede_usar_tienda` → `test_sin_tienda_ni_tarjeta_ni_queries`.

- [ ] **Step 7: Commit**

```bash
npm run build:css
git status --short
git add tienda tenants/views.py templates static/css/app.css
git commit -m "feat(tienda): estadísticas de ventas en el panel y vendido del mes"
```

---

### Task 7: Alumno — catálogo, carrito, checkout, mis pedidos, comprobante, cancelaciones

**Files:**
- Modify: `tienda/forms.py`, `tienda/views.py`, `tienda/urls.py`, `tenants/views.py::_portal_alumno`, `templates/tenants/home.html` (card del alumno)
- Create: `tienda/tests_vistas_alumno.py`, `templates/tienda/catalogo.html`, `templates/tienda/carrito.html`, `templates/tienda/mis_pedidos.html`, `templates/tienda/mi_pedido.html`

**Interfaces:**
- Consumes: servicios de carrito y de pedido.
- Produces: URLs `tienda:catalogo`, `tienda:carrito` (GET; POST `accion=agregar|quitar`, `variante`, `cantidad`), `tienda:checkout` (POST), `tienda:mis_pedidos`, `tienda:mi_pedido <pk>`, `tienda:mi_pedido_comprobante <pk>` (POST multipart), `tienda:mi_pedido_cancelar <pk>` (POST), `tienda:mi_pedido_pedir_cancelacion <pk>` (POST). Contexto del portal: `tienda_visible` (bool).

- [ ] **Step 1: Tests que fallan** — `tienda/tests_vistas_alumno.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import Pedido, Producto, Variante, obtener_configuracion
from tienda.testing import PNG


class _BaseAlumno(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.client.force_login(usuario)
        self.producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"))
        self.variante = Variante.objects.create(producto=self.producto, nombre="M", stock=2)

    def agregar(self, cantidad=1, variante=None):
        return self.client.post(reverse("tienda:carrito"), {
            "accion": "agregar", "variante": (variante or self.variante).pk, "cantidad": cantidad,
        }, follow=True)


class CompraTests(_BaseAlumno):
    def test_compra_de_punta_a_punta(self):
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Remera")
        self.agregar(2)
        self.assertContains(self.client.get(reverse("tienda:carrito")), "30.000")
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        pedido = Pedido.objects.get(alumno=self.alumno)
        self.assertRedirects(r, reverse("tienda:mi_pedido", args=[pedido.pk]))
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 0)
        # El carrito quedó vacío.
        self.assertNotContains(self.client.get(reverse("tienda:carrito")), "30.000")

    def test_sin_stock_es_cartel_y_no_crea_pedido(self):
        self.agregar(2)
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertContains(r, "No queda stock suficiente")
        self.assertFalse(Pedido.objects.exists())

    def test_suplantando_no_compra(self):
        from tenants import suplantacion

        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION] = {"inicio": timezone.now().isoformat()}
        sesion.save()
        self.agregar(1)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertContains(r, "no se puede comprar")
        self.assertFalse(Pedido.objects.exists())

    def test_un_producto_archivado_en_el_carrito_no_traba_la_compra(self):
        otro = Variante.objects.create(producto=self.producto, nombre="L", stock=2)
        self.agregar(1)
        self.agregar(1, variante=otro)
        Variante.objects.filter(pk=otro.pk).update(activa=False)
        self.client.post(reverse("tienda:checkout"))
        pedido = Pedido.objects.get(alumno=self.alumno)
        self.assertEqual(list(pedido.items.values_list("variante_id", flat=True)), [self.variante.pk])

    def test_catalogo_oculta_sin_stock_como_agotado(self):
        Variante.objects.filter(pk=self.variante.pk).update(stock=0)
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Agotado")

    def test_catalogo_avisa_cuando_quedan_pocas(self):
        self.assertContains(self.client.get(reverse("tienda:catalogo")), "Quedan 2")  # stock 2
        Variante.objects.filter(pk=self.variante.pk).update(stock=services.UMBRAL_POCAS_UNIDADES + 1)
        self.assertNotContains(self.client.get(reverse("tienda:catalogo")), "Quedan")


class ComprobanteVistaTests(_BaseAlumno):
    def setUp(self):
        super().setUp()
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 1}, suplantando=False,
        )

    def test_subir_comprobante(self):
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", PNG, content_type="image/png")},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.EN_REVISION)

    def test_un_pdf_o_basura_es_error_visible(self):
        r = self.client.post(
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
            {"comprobante": SimpleUploadedFile("c.png", b"no es imagen", content_type="image/png")},
            follow=True,
        )
        self.assertContains(r, "imagen")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_pedido_de_otro_alumno_404(self):
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        ajeno = services.crear_pedido(gimnasio=self.gimnasio, alumno=otro,
                                      carrito={self.variante.pk: 1}, suplantando=False)
        self.assertEqual(self.client.get(reverse("tienda:mi_pedido", args=[ajeno.pk])).status_code, 404)

    def test_detalle_muestra_datos_de_transferencia(self):
        from pagos.models import MedioCobro

        MedioCobro.objects.create(gimnasio=self.gimnasio, alias="gym.alias.mp", titular="Gym SA")
        self.assertContains(self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk])), "gym.alias.mp")

    def test_cancelar(self):
        self.client.post(reverse("tienda:mi_pedido_cancelar", args=[self.pedido.pk]))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.CANCELADO)

    def test_pedir_cancelacion_de_un_pagado(self):
        services.marcar_pagado(self.pedido)
        r = self.client.post(
            reverse("tienda:mi_pedido_pedir_cancelacion", args=[self.pedido.pk]), follow=True
        )
        self.assertContains(r, f"Solicitud registrada — pedido #{self.pedido.pk}")
        self.pedido.refresh_from_db()
        self.assertIsNotNone(self.pedido.cancelacion_pedida_en)

    def test_comprobante_rechazado_muestra_el_motivo_y_deja_subir_otro(self):
        services.subir_comprobante(
            self.pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"),
        )
        services.rechazar_comprobante(self.pedido, motivo="No se ve el monto")
        r = self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk]))
        self.assertContains(r, "No se ve el monto")
        self.assertContains(r, reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]))

    def test_rechazo_sin_motivo_igual_se_avisa(self):
        services.subir_comprobante(
            self.pedido, alumno=self.alumno,
            archivo=SimpleUploadedFile("c.png", PNG, content_type="image/png"),
        )
        services.rechazar_comprobante(self.pedido, motivo="")
        r = self.client.get(reverse("tienda:mi_pedido", args=[self.pedido.pk]))
        self.assertContains(r, "rechazó tu comprobante")


class BotonDeArrepentimientoTests(_BaseAlumno):
    """Res. 424/2020: el «botón de arrepentimiento» tiene que estar a la
    vista, no escondido dentro de cada pedido."""

    def setUp(self):
        super().setUp()
        self.url = reverse("tienda:mis_pedidos") + "?arrepentimiento=1"

    def test_el_link_esta_en_el_catalogo_y_en_mis_pedidos(self):
        for pantalla in ("tienda:catalogo", "tienda:mis_pedidos"):
            r = self.client.get(reverse(pantalla))
            self.assertContains(r, "Botón de arrepentimiento")
            self.assertContains(r, self.url)

    def test_lista_solo_los_pedidos_cancelables(self):
        pagado = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pagado)
        pendiente = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                          carrito={self.variante.pk: 1}, suplantando=False)
        r = self.client.get(self.url)
        self.assertContains(r, reverse("tienda:mi_pedido_pedir_cancelacion", args=[pagado.pk]))
        self.assertNotContains(r, reverse("tienda:mi_pedido", args=[pendiente.pk]))


class TiendaApagadaAlumnoTests(_BaseAlumno):
    def test_sin_publicar_no_hay_catalogo_pero_si_mis_pedidos(self):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = False
        config.save()
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no está disponible")
        self.assertContains(
            self.client.get(reverse("tienda:mis_pedidos")), reverse("tienda:mi_pedido", args=[pedido.pk])
        )

    def test_card_del_portal(self):
        # `href="..."` exacto: «/tienda/» también está adentro de «/tienda/mis-pedidos/».
        catalogo = f'href="{reverse("tienda:catalogo")}"'
        self.assertContains(self.client.get(reverse("home")), catalogo)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, catalogo)
        self.assertNotContains(r, reverse("tienda:mis_pedidos"))

    def test_con_la_tienda_apagada_queda_el_link_a_mis_pedidos_si_hay_pedidos_vivos(self):
        services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                              carrito={self.variante.pk: 1}, suplantando=False)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, f'href="{reverse("tienda:catalogo")}"')
        self.assertContains(r, reverse("tienda:mis_pedidos"))


class VerbosTests(_BaseAlumno):
    def test_get_al_checkout_y_post_al_catalogo_son_405_no_500(self):
        self.assertEqual(self.client.get(reverse("tienda:checkout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("tienda:catalogo")).status_code, 405)


class VencimientoPerezosoSinPushTests(_BaseAlumno):
    def test_ni_el_catalogo_ni_el_listado_del_staff_mandan_push_ni_tocan_la_red(self):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        Pedido.objects.filter(pk=pedido.pk).update(reservado_hasta=timezone.now() - timedelta(hours=1))
        prohibido = AssertionError("el camino perezoso no puede mandar push")
        with patch("notificaciones.services.notificar_a_usuario", side_effect=prohibido), \
                patch("notificaciones.services.notificar_a_gimnasio", side_effect=prohibido):
            self.client.get(reverse("tienda:catalogo"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.VENCIDO)


class AvisosDeLasVistasTests(_BaseAlumno):
    """En un `TestCase` los `on_commit` no corren solos."""

    @patch("notificaciones.services.notificar_pedido_al_staff")
    def test_el_checkout_avisa_al_staff(self, aviso):
        self.agregar(1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tienda:checkout"))
        self.assertEqual(aviso.call_args.args[1], "nuevo")

    @patch("notificaciones.services.notificar_pedido_al_staff")
    def test_el_comprobante_avisa_al_staff(self, aviso):
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                       carrito={self.variante.pk: 1}, suplantando=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("tienda:mi_pedido_comprobante", args=[pedido.pk]),
                {"comprobante": SimpleUploadedFile("c.png", PNG, content_type="image/png")},
            )
        self.assertEqual(aviso.call_args.args[1], "comprobante")


class PerfilSinAlumnoTests(TestCase):
    def test_cartel_200_no_403(self):
        gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        u = User.objects.create_user("solo", password="c-123456")
        Perfil.objects.create(usuario=u, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(u)
        r = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertEqual(r.status_code, 200)


class PublicoDeLasVistasTests(TestCase):
    #: Las únicas vistas sin mixin de rol, cada una con su motivo.
    EXCEPCIONES = {
        # Pública a propósito: la foto de un producto no es dato sensible y va
        # en `URLS_SIN_BLOQUEO` para responder `immutable` sin cookie.
        "FotoProductoView",
    }

    def test_toda_vista_declara_su_publico(self):
        import inspect

        from django.views import View

        from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
        from tienda import views

        clases = [
            c for _, c in inspect.getmembers(views, inspect.isclass)
            if issubclass(c, View) and c.__module__ == views.__name__
        ]
        self.assertTrue(clases)
        for clase in clases:
            with self.subTest(clase.__name__):
                publicos = [m for m in (StaffRequiredMixin, AlumnoRequiredMixin) if issubclass(clase, m)]
                if clase.__name__ in self.EXCEPCIONES:
                    self.assertEqual(publicos, [])
                else:
                    self.assertEqual(len(publicos), 1)
```

Run → FAIL.

- [ ] **Step 2: Form del comprobante** — agregar a `tienda/forms.py`:

```python
from PIL import Image as _Image

from pagos.models import EXTENSIONES_COMPROBANTE_PERMITIDAS

TAMANIO_MAXIMO_COMPROBANTE = 5 * 1024 * 1024


class ComprobanteForm(forms.Form):
    comprobante = forms.FileField(
        label="Comprobante de la transferencia",
        help_text="Una captura o foto del comprobante (JPG o PNG, hasta 5 MB).",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

    def clean_comprobante(self):
        archivo = self.cleaned_data["comprobante"]
        extension = archivo.name.rsplit(".", 1)[-1].lower() if "." in archivo.name else ""
        if extension not in EXTENSIONES_COMPROBANTE_PERMITIDAS:
            raise forms.ValidationError("Subí una imagen JPG o PNG.")
        if archivo.size > TAMANIO_MAXIMO_COMPROBANTE:
            raise forms.ValidationError("El archivo pesa más de 5 MB.")
        try:
            _Image.open(archivo).verify()
        except Exception:
            raise forms.ValidationError("El archivo no es una imagen válida.")
        archivo.seek(0)
        return archivo
```

- [ ] **Step 3: Vistas del alumno** — agregar a `tienda/views.py` (imports: `AlumnoRequiredMixin`, `TemplateView`, `DetailView`, `suplantacion` de `tenants`, `ComprobanteForm`, `MedioCobro`):

```python
class TiendaAlumnoMixin(AlumnoRequiredMixin):
    """Alumno + tienda visible. `requiere_tienda_visible = False` en «Mis
    pedidos» y su detalle: con la tienda apagada el alumno sigue viendo lo que
    compró. Sin ficha de `Alumno` vinculada, cartel con 200 (bajo boost un 403
    es un click mudo)."""

    requiere_tienda_visible = True

    def get(self, request, *args, **kwargs):
        if not hasattr(super(), "get"):  # vistas sólo-POST (checkout, acciones)
            return self.http_method_not_allowed(request, *args, **kwargs)
        return self._cartel() or super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not hasattr(self, "procesar"):  # vistas sólo-GET
            return self.http_method_not_allowed(request, *args, **kwargs)
        return self._cartel() or self.procesar(request, *args, **kwargs)

    def _cartel(self):
        if self.alumno is None:
            return render(self.request, "tienda/sin_ficha.html", status=200)
        if self.requiere_tienda_visible and not services.tienda_visible_para_alumnos(self.gimnasio):
            return render(self.request, "tienda/cerrada.html", status=200)
        services.vencer_pedidos(gimnasio=self.gimnasio)
        return None
```

`AlumnoRequiredMixin.dispatch` corre primero y setea `self.perfil`; después el `get`/`post` de este mixin pone el cartel o delega. Las vistas de POST implementan **`procesar(self, request, *args, **kwargs)`** (todas con esa firma; el `pk` se lee de `self.kwargs["pk"]`); las de GET heredan de `TemplateView` (que trae `get`). MRO: `TiendaAlumnoMixin` va **primero** en las bases para que su `get` envuelva al de la vista genérica.

```python
class CatalogoView(TiendaAlumnoMixin, TemplateView):
    template_name = "tienda/catalogo.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        productos = (
            Producto.objects.for_gimnasio(self.gimnasio)
            .filter(publicado=True)
            .select_related("categoria")
            .prefetch_related(models.Prefetch("variantes", queryset=Variante.objects.filter(activa=True)))
            .order_by("categoria__orden", "categoria__nombre", "orden", "nombre")
        )
        grupos = {}
        for producto in productos:
            if producto.variantes.all():  # prefetcheado: sin query
                nombre = producto.categoria.nombre if producto.categoria else "Otros"
                grupos.setdefault(nombre, []).append(producto)
        context["grupos"] = list(grupos.items())
        context["unidades_en_carrito"] = sum(services.leer_carrito(self.request.session).values())
        context["umbral_pocas"] = services.UMBRAL_POCAS_UNIDADES
        return context


class CarritoView(TiendaAlumnoMixin, TemplateView):
    template_name = "tienda/carrito.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lineas"], context["total"] = services.lineas_del_carrito(
            self.request.session, gimnasio=self.gimnasio
        )
        context["config"] = obtener_configuracion(self.gimnasio)
        return context

    def procesar(self, request, *args, **kwargs):
        try:
            variante_id = int(request.POST.get("variante", ""))
            cantidad = int(request.POST.get("cantidad", "1"))
        except ValueError:
            messages.error(request, services.MENSAJES["cantidad_invalida"])
            return redirect("tienda:carrito")
        if request.POST.get("accion") == "quitar":
            services.quitar_del_carrito(request.session, variante_id)
            return redirect("tienda:carrito")
        try:
            services.agregar_al_carrito(
                request.session, gimnasio=self.gimnasio, variante_id=variante_id, cantidad=cantidad
            )
            messages.success(request, "Agregado al carrito.")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        destino = "tienda:catalogo" if request.POST.get("volver") == "catalogo" else "tienda:carrito"
        return redirect(destino)


class CheckoutView(TiendaAlumnoMixin, View):
    def procesar(self, request, *args, **kwargs):
        try:
            # Se compra lo que el alumno VE en el carrito (líneas saneadas), no
            # la sesión cruda: un producto archivado después de agregarlo no
            # puede trabar la compra del resto.
            lineas, _ = services.lineas_del_carrito(request.session, gimnasio=self.gimnasio)
            pedido = services.crear_pedido(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                carrito={l.variante.pk: l.cantidad for l in lineas},
                suplantando=suplantacion.esta_activa(request),
            )
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
            return redirect("tienda:carrito")
        services.vaciar_carrito(request.session)
        from notificaciones import services as avisos_push

        transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "nuevo"))
        messages.success(request, "¡Pedido hecho! Transferí y subí el comprobante antes del plazo.")
        return redirect("tienda:mi_pedido", pk=pedido.pk)


class MisPedidosView(TiendaAlumnoMixin, TemplateView):
    requiere_tienda_visible = False
    template_name = "tienda/mis_pedidos.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedidos = Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno)
        # «Botón de arrepentimiento» (Res. 424/2020): la misma pantalla, sólo
        # con lo que todavía se puede cancelar y el botón en cada fila.
        context["arrepentimiento"] = self.request.GET.get("arrepentimiento") == "1"
        if context["arrepentimiento"]:
            # Filtrado en la base y sin cortar: un `[:50]` antes de filtrar
            # podía dejar afuera un pedido cancelable. Son pocos por naturaleza
            # (pagados sin retirar + retirados en los últimos 10 días).
            context["pedidos"] = pedidos.filter(services.q_puede_pedir_cancelacion()).prefetch_related("items")
        else:
            context["pedidos"] = pedidos.prefetch_related("items")[:50]
        return context


class MiPedidoView(TiendaAlumnoMixin, TemplateView):
    requiere_tienda_visible = False
    template_name = "tienda/mi_pedido.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedido = get_object_or_404(
            Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno).prefetch_related("items"),
            pk=self.kwargs["pk"],
        )
        context.update(
            pedido=pedido,
            form=ComprobanteForm(),
            medios_cobro=MedioCobro.objects.for_gimnasio(self.gimnasio).filter(activo=True),
            config=obtener_configuracion(self.gimnasio),
            puede_pedir_cancelacion=services.puede_pedir_cancelacion(pedido),
        )
        return context


class _AccionSobreMiPedido(TiendaAlumnoMixin, View):
    requiere_tienda_visible = False

    def mi_pedido(self):
        return get_object_or_404(
            Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno), pk=self.kwargs["pk"]
        )


class MiPedidoComprobanteView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        pedido = self.mi_pedido()
        form = ComprobanteForm(request.POST, request.FILES)
        if not form.is_valid():
            for error in form.errors.get("comprobante", []):
                messages.error(request, error)
            return redirect("tienda:mi_pedido", pk=pk)
        try:
            services.subir_comprobante(pedido, alumno=self.alumno, archivo=form.cleaned_data["comprobante"])
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            from notificaciones import services as avisos_push

            transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "comprobante"))
            messages.success(request, "Recibimos tu comprobante. Te avisamos cuando el gimnasio lo confirme.")
        return redirect("tienda:mi_pedido", pk=pk)


class MiPedidoCancelarView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        try:
            services.cancelar_por_alumno(self.mi_pedido(), alumno=self.alumno)
            messages.success(request, "Pedido cancelado.")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        return redirect("tienda:mi_pedido", pk=pk)


class MiPedidoPedirCancelacionView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        pedido = self.mi_pedido()
        try:
            services.pedir_cancelacion(pedido, alumno=self.alumno)
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            from notificaciones import services as avisos_push

            transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "cancelacion"))
            # El número de pedido es el código de la solicitud que pide la
            # Res. 424/2020; se muestra en el acto.
            messages.success(
                request,
                f"Solicitud registrada — pedido #{pedido.pk}. Le avisamos al gimnasio: "
                "te van a contactar para devolverte la plata.",
            )
        return redirect("tienda:mi_pedido", pk=pk)
```

Agregar `notificar_pedido_al_staff(pedido, aviso)` como función vacía en `notificaciones/services.py` (se completa en la Tarea 8). Agregar `from django.db import models` arriba de `views.py` para el `Prefetch`.

URLs a agregar:

```python
    # Alumno
    path("", views.CatalogoView.as_view(), name="catalogo"),
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("carrito/confirmar/", views.CheckoutView.as_view(), name="checkout"),
    path("mis-pedidos/", views.MisPedidosView.as_view(), name="mis_pedidos"),
    path("mis-pedidos/<int:pk>/", views.MiPedidoView.as_view(), name="mi_pedido"),
    path("mis-pedidos/<int:pk>/comprobante/", views.MiPedidoComprobanteView.as_view(), name="mi_pedido_comprobante"),
    path("mis-pedidos/<int:pk>/cancelar/", views.MiPedidoCancelarView.as_view(), name="mi_pedido_cancelar"),
    path("mis-pedidos/<int:pk>/pedir-cancelacion/", views.MiPedidoPedirCancelacionView.as_view(), name="mi_pedido_pedir_cancelacion"),
```

- [ ] **Step 4: Templates del alumno** (pensados para celular, una columna; clases existentes `tarjeta`, `boton`, `boton-secundario`, `texto-suave`, `badge`):
  - `cerrada.html` / `sin_ficha.html`: tarjeta con «La tienda no está disponible en este momento.» / «Tu usuario todavía no está vinculado a una ficha de alumno: pedile al gimnasio que lo revise.» y link a `home`.
  - `catalogo.html`: link «Carrito ({{ unidades_en_carrito }})», «Mis pedidos» y, al pie, `<a href="{% url 'tienda:mis_pedidos' %}?arrepentimiento=1">Botón de arrepentimiento</a>` (visible siempre, no dentro de un `<details>`); por grupo `<h2>`; por producto: foto (`foto_url_cacheable`, `loading="lazy"`, `width`/`height` fijos para no saltar el layout), nombre, `$ {{ p.precio|floatformat:"2g" }}`, descripción; form POST a `tienda:carrito` con `accion=agregar`, `volver=catalogo`, `<select name="variante">` con las variantes (`{% if v.stock == 0 %}disabled{% endif %}` y el texto «— Agotado»; `{% elif v.stock <= umbral_pocas %}` el texto «— Quedan {{ v.stock }}»), `cantidad` numérico `min=1 max=20 value=1`; si todas sin stock, «Agotado» y botón deshabilitado.
  - `carrito.html`: líneas con cantidad, subtotal y botón «Quitar» (POST `accion=quitar`); total; «Tenés {{ config.horas_reserva_transferencia }} h para transferir y subir el comprobante; si no, el pedido se cancela solo»; botón POST «Confirmar pedido» a `tienda:checkout`; vacío → «Tu carrito está vacío» + link al catálogo.
  - `mis_pedidos.html`: lista `#{{ p.pk }}`, fecha, total, estado; al pie, el mismo link «Botón de arrepentimiento». Con `arrepentimiento`: título «Botón de arrepentimiento», el texto «Podés arrepentirte de una compra: mientras no la retires, cuando quieras; después de retirarla, hasta 10 días. El gimnasio te devuelve la plata por el mismo medio.», y en cada fila un form POST a `tienda:mi_pedido_pedir_cancelacion` con el botón «Pedir cancelación»; vacío → «No tenés pedidos que se puedan cancelar ahora.» y link a la lista completa.
  - `mi_pedido.html`: estado; si PENDIENTE_PAGO: «Transferí $ {{ pedido.total|floatformat:"2g" }} antes del {{ pedido.reservado_hasta|date:"d/m H:i" }}» + datos de `medios_cobro` (si no hay: «El gimnasio todavía no cargó sus datos de transferencia: consultalo en recepción») + form `<form method="post" enctype="multipart/form-data" hx-boost="false" action="{% url 'tienda:mi_pedido_comprobante' pedido.pk %}">` con `{% include 'partials/campo_form.html' with campo=form.comprobante %}` + botón «Cancelar pedido»; si además `pedido.comprobante` (en PENDIENTE_PAGO sólo hay archivo si el staff lo rechazó; mismo criterio que el detalle del staff, NO `pedido.motivo`, que puede venir vacío): arriba de todo «El gimnasio rechazó tu comprobante{% if pedido.motivo %}: {{ pedido.motivo }}{% endif %}. Subí otro antes del {{ pedido.reservado_hasta|date:"d/m H:i" }}.»; EN_REVISION: «El gimnasio está revisando tu comprobante»; PAGADO: «¡Listo! Retiralo en el gimnasio.» + `config.instrucciones_retiro|linebreaksbr`; VENCIDO/CANCELADO/DEVUELTO con `pedido.motivo` si hay; si `puede_pedir_cancelacion`: botón «Pedir cancelación» con el texto «Podés arrepentirte de la compra: mientras no la retires, cuando quieras; después de retirarla, hasta 10 días.». La cuenta regresiva es texto con la hora límite (no hace falta JS).

- [ ] **Step 5: Card en el portal.** En `tenants/views.py::_portal_alumno(perfil)` (línea ~92; recibe `perfil`, no `gimnasio`), en el dict del alumno con ficha, agregar `"tienda_visible": tienda_visible_para_alumnos(perfil.gimnasio)` (import local de `tienda.services`). La rama `alumno is None` vuelve antes sin la clave y el template la lee como False: está bien. Sumar también `"tienda_pedidos_vivos"`: si `tienda_visible` es False, `Pedido.objects.for_gimnasio(perfil.gimnasio).filter(alumno=alumno, estado__in=ESTADOS_QUE_RETIENEN).exists()`; si es True, no hace falta calcularlo (una query menos). En `home.html`, en la zona del alumno, después de la tarjeta de «Tu cuota» (línea ~959), agregar:

```html
  {% if tienda_visible %}
    <div class="tarjeta">
      <h2>Tienda del gimnasio</h2>
      <p class="texto-suave">Suplementos, ropa y lo que vende tu gimnasio. Pagás por transferencia y retirás acá.</p>
      <a class="boton w-full mt-2" href="{% url 'tienda:catalogo' %}">Ver productos</a>
      <a class="boton-secundario w-full mt-2" href="{% url 'tienda:mis_pedidos' %}">Mis pedidos</a>
    </div>
  {% elif tienda_pedidos_vivos %}
    {% comment %}
      Tienda apagada o sin publicar, pero con pedidos sin cerrar: sin este link
      el alumno no tiene cómo ver lo que pagó y tiene para retirar.
    {% endcomment %}
    <div class="tarjeta">
      <h2>Tus pedidos de la tienda</h2>
      <a class="boton-secundario w-full mt-2" href="{% url 'tienda:mis_pedidos' %}">Mis pedidos</a>
    </div>
  {% endif %}
```

- [ ] **Step 6: Correr** `$PY manage.py test tienda tenants core -v 1` → PASS.

- [ ] **Step 7: Commit**

```bash
npm run build:css
git status --short
git add tienda templates notificaciones/services.py tenants/views.py static/css/app.css
git commit -m "feat(tienda): catálogo, carrito y pedidos del alumno"
```

---

### Task 8: Avisos push, superadmin y cron de vencimiento

**Files:**
- Modify: `notificaciones/models.py` (+ migración), `notificaciones/services.py`, `plataforma/views.py`, `plataforma/urls.py`, `templates/plataforma/gimnasio_detalle.html`, `.github/workflows/enviar-recordatorios.yml`
- Create: `tienda/management/commands/vencer_pedidos.py`, `tienda/tests_comandos.py`

**Interfaces:**
- Consumes: `services.vencer_pedidos`, `services.contadores_panel`.
- Produces: `notificar_pedido_al_staff(pedido, aviso: "nuevo"|"comprobante"|"cancelacion"|"revision_demorada")`, `notificar_pedido_al_alumno(pedido, aviso: "confirmado"|"rechazado"|"cancelado"|"vencido"|"cancelacion_resuelta")`, `notificar_pedido_vencido(pedido) -> bool`, `notificar_revision_demorada(pedido) -> bool`; `RecordatorioEnviado.Tipo.PEDIDO_VENCIDO = "pedido_vencido"`, `PEDIDO_EN_REVISION = "pedido_revision"`; URL `plataforma:tienda_toggle <pk>`; comando `vencer_pedidos`.

Decisión de ejecución (registrar en la spec, sección nueva «Decisiones de ejecución»): el dedup del recordatorio de 24 h y del aviso «vencido» usa `RecordatorioEnviado` (como todos los recordatorios del proyecto) en lugar del campo `Pedido.aviso_revision_en` que decía la spec.

- [ ] **Step 1: Tests que fallan** — `tienda/tests_comandos.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from alumnos.models import Alumno
from notificaciones.models import RecordatorioEnviado
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import Pedido, Producto, Variante


class VencerPedidosComandoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="R", precio=Decimal("1"))
        self.variante = Variante.objects.create(producto=producto, stock=5)

    def pedido(self):
        return services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                     carrito={self.variante.pk: 1}, suplantando=False)

    @patch("notificaciones.services.notificar_a_usuario")
    def test_vence_y_avisa_una_sola_vez_aunque_haya_vencido_perezosamente(self, push):
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(reservado_hasta=timezone.now() - timedelta(hours=1))
        services.vencer_pedidos()  # perezoso: sin push
        push.assert_not_called()
        call_command("vencer_pedidos", stdout=StringIO())
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertEqual(push.call_count, 1)
        self.assertTrue(RecordatorioEnviado.objects.filter(
            tipo=RecordatorioEnviado.Tipo.PEDIDO_VENCIDO, objeto_id=p.pk).exists())

    @patch("notificaciones.services.notificar_a_usuario")
    def test_alumno_sin_acceso_no_queda_marcado_como_avisado(self, push):
        self.alumno.perfil = None
        self.alumno.save()
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(
            estado=Pedido.Estado.VENCIDO, modificado=timezone.now()
        )
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertFalse(RecordatorioEnviado.objects.filter(
            tipo=RecordatorioEnviado.Tipo.PEDIDO_VENCIDO, objeto_id=p.pk).exists())

    @patch("notificaciones.services.notificar_a_gimnasio")
    def test_recordatorio_de_revision_demorada_una_vez(self, push):
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(
            estado=Pedido.Estado.EN_REVISION,
            comprobante_subido_en=timezone.now() - timedelta(hours=25),
        )
        call_command("vencer_pedidos", stdout=StringIO())
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.kwargs["rol"], Perfil.Rol.STAFF)


class AvisosAlStaffTests(TestCase):
    @patch("notificaciones.services.notificar_a_gimnasio")
    def test_siempre_con_rol_staff(self, push):
        from notificaciones.services import notificar_pedido_al_staff

        gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(gimnasio=gimnasio, alumno=alumno, total=1)
        for aviso in ("nuevo", "comprobante", "cancelacion", "revision_demorada"):
            notificar_pedido_al_staff(pedido, aviso)
        self.assertEqual(push.call_count, 4)
        for llamada in push.call_args_list:
            self.assertEqual(llamada.kwargs["rol"], Perfil.Rol.STAFF)


class AvisosAlAlumnoTests(TestCase):
    @patch("notificaciones.services.notificar_a_usuario")
    def test_rechazado_lleva_el_plazo_nuevo_en_hora_argentina(self, push):
        from notificaciones.services import notificar_pedido_al_alumno

        gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO)
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        plazo = timezone.make_aware(datetime(2026, 10, 21, 22, 30))  # 22/10 01:30 UTC
        pedido = Pedido.objects.create(gimnasio=gimnasio, alumno=alumno, total=1, reservado_hasta=plazo)
        notificar_pedido_al_alumno(pedido, "rechazado")
        self.assertIn("21/10 22:30", push.call_args.args[1]["body"])


# Los tests del toggle viven en `plataforma/tests_bloqueo.py`, al lado de
# `ExportacionToggleViewTests` (copiar su forma: invierte la casilla, no toca
# `modificado`, un staff recibe 403). El de abajo es el mínimo; mudarlo allá.
class TiendaTogglePlataformaTests(TestCase):
    def test_toggle_no_toca_modificado_y_muestra_pedidos_vivos(self):
        from django.urls import reverse

        superadmin = User.objects.create_superuser("root", "r@x.com", "c-123456")
        self.client.force_login(superadmin)
        gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        antes = gimnasio.modificado
        self.client.post(reverse("plataforma:tienda_toggle", args=[gimnasio.pk]))
        gimnasio.refresh_from_db()
        self.assertTrue(gimnasio.tienda_habilitada)
        self.assertEqual(gimnasio.modificado, antes)
        r = self.client.get(reverse("plataforma:gimnasio_detalle", args=[gimnasio.pk]))
        self.assertContains(r, "Deshabilitar tienda")
```

(Buscar el nombre real de la URL de la ficha con `grep -n "name=" plataforma/urls.py`; si no es `gimnasio_detalle`, usar el real.)

Run → FAIL.

- [ ] **Step 2: Tipos de recordatorio** — en `notificaciones/models.py::RecordatorioEnviado.Tipo` agregar:

```python
        PEDIDO_VENCIDO = "pedido_vencido", "Pedido de la tienda vencido"
        PEDIDO_EN_REVISION = "pedido_revision", "Comprobante de la tienda sin revisar"
```

(los dos valores entran en `max_length=20`). `$PY manage.py makemigrations notificaciones` → una migración que sólo cambia `choices`.

- [ ] **Step 3: Avisos** — reemplazar las dos funciones vacías en `notificaciones/services.py` por:

```python
_AVISOS_STAFF = {
    "nuevo": ("Nuevo pedido en la tienda", "{alumno} hizo el pedido #{pk} por $ {total}.", "revisar"),
    # `total` llega formateado con `floatformat(total, "2g")` (no «15000.00»).
    "comprobante": ("Comprobante de la tienda", "{alumno} subió el comprobante del pedido #{pk}.", "revisar"),
    "cancelacion": ("Pedido de cancelación", "{alumno} pidió cancelar el pedido #{pk}.", "cancelaciones"),
    "revision_demorada": (
        "Comprobante sin revisar",
        "El comprobante del pedido #{pk} de {alumno} espera hace más de un día.",
        "revisar",
    ),
}

_AVISOS_ALUMNO = {
    "confirmado": ("¡Pedido confirmado!", "Tu pedido #{pk} está pago: retiralo en el gimnasio."),
    "rechazado": (
        "Comprobante rechazado",
        "El gimnasio rechazó el comprobante del pedido #{pk}. Subí otro antes del {plazo}.",
    ),
    "cancelado": ("Pedido cancelado", "El gimnasio canceló tu pedido #{pk}."),
    "vencido": (
        "Pedido vencido",
        "Tu pedido #{pk} venció: terminó el plazo para pagarlo y los productos volvieron a la tienda.",
    ),
    "cancelacion_resuelta": ("Tu pedido de cancelación", "El gimnasio respondió sobre el pedido #{pk}."),
}


# (imports arriba del módulo: `from django.template.defaultfilters import floatformat` y
# `from django.utils.formats import date_format`; `timezone` ya está importado)
def notificar_pedido_al_staff(pedido, aviso) -> None:
    titulo, cuerpo, filtro = _AVISOS_STAFF[aviso]
    payload = {
        "title": titulo,
        "body": cuerpo.format(
            alumno=pedido.alumno, pk=pedido.pk, total=floatformat(pedido.total, "2g")
        ),
        "url": f"{reverse('tienda:pedidos')}?filtro={filtro}",
        "icon": _icono_url(pedido.gimnasio),
    }
    # `rol` explícito SIEMPRE: el default `None` le avisa también a los alumnos.
    notificar_a_gimnasio(pedido.gimnasio, payload, rol=Perfil.Rol.STAFF)


def notificar_pedido_vencido(pedido) -> bool:
    """Lo llama sólo el cron. Perfil ANTES del dedup (mismo criterio que
    `notificar_pago_por_vencer`): si no, un alumno sin acceso queda marcado
    como avisado y no se entera nunca. Devuelve si se mandó."""
    if pedido.alumno.perfil_id is None:
        return False
    if _ya_notificado(pedido.gimnasio, RecordatorioEnviado.Tipo.PEDIDO_VENCIDO, pedido.pk):
        return False
    notificar_pedido_al_alumno(pedido, "vencido")
    return True


def notificar_revision_demorada(pedido) -> bool:
    """Una vez por pedido: el comprobante espera hace más de 24 h."""
    if _ya_notificado(pedido.gimnasio, RecordatorioEnviado.Tipo.PEDIDO_EN_REVISION, pedido.pk):
        return False
    notificar_pedido_al_staff(pedido, "revision_demorada")
    return True


def notificar_pedido_al_alumno(pedido, aviso) -> None:
    if pedido.alumno.perfil_id is None:
        return
    titulo, cuerpo = _AVISOS_ALUMNO[aviso]
    # `plazo` sólo lo usa «rechazado» (el plazo nuevo para subir otro); los
    # demás textos lo ignoran. Hora argentina, no UTC.
    plazo = date_format(timezone.localtime(pedido.reservado_hasta), "d/m H:i") if pedido.reservado_hasta else ""
    payload = {
        "title": titulo,
        "body": cuerpo.format(pk=pedido.pk, plazo=plazo),
        "url": reverse("tienda:mi_pedido", args=[pedido.pk]),
        "icon": _icono_url(pedido.gimnasio),
    }
    notificar_a_usuario(pedido.alumno.perfil.usuario, payload)
```

- [ ] **Step 4: Comando** — `tienda/management/commands/vencer_pedidos.py`

```python
"""Respaldo del vencimiento perezoso de la tienda, más los avisos que el
camino perezoso no manda (corre dentro de GETs y no puede tocar la red).

Lo corre `enviar-recordatorios.yml` cada 15 min. Idempotente: el dedup de los
avisos es `RecordatorioEnviado`, así que un pedido que venció perezosamente
igual recibe su aviso una sola vez."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from notificaciones import services as avisos
from tienda import services
from tienda.models import Pedido

VENTANA_AVISO_VENCIDO = timedelta(days=2)
DEMORA_REVISION = timedelta(hours=24)


class Command(BaseCommand):
    help = "Vence pedidos de la tienda sin comprobante y manda los avisos pendientes."

    def handle(self, *args, **options):
        ahora = timezone.now()
        vencidos = services.vencer_pedidos(ahora=ahora)

        recientes = Pedido.objects.filter(
            estado=Pedido.Estado.VENCIDO, modificado__gte=ahora - VENTANA_AVISO_VENCIDO
        ).select_related("gimnasio", "alumno__perfil__usuario")
        avisados = sum(avisos.notificar_pedido_vencido(p) for p in recientes)

        demorados = Pedido.objects.filter(
            estado=Pedido.Estado.EN_REVISION, comprobante_subido_en__lt=ahora - DEMORA_REVISION
        ).select_related("gimnasio", "alumno")
        recordados = sum(avisos.notificar_revision_demorada(p) for p in demorados)

        self.stdout.write(self.style.SUCCESS(
            f"Pedidos vencidos: {len(vencidos)}. Avisos de vencido: {avisados}. "
            f"Recordatorios de revisión: {recordados}."
        ))
```

El comando no tiene lógica de avisos propia (como `enviar_recordatorios`): itera y llama a `notificar_*`, que hacen perfil → dedup → envío. `_ya_notificado` (`notificaciones/services.py:160`) devuelve True si YA estaba y, si no, crea la fila y devuelve False. Los loops son por pedido a propósito (cada aviso es un push distinto); el universo está acotado por la ventana de 2 días.

- [ ] **Step 5: Workflow** — en `.github/workflows/enviar-recordatorios.yml`, después del paso «Correr enviar_recordatorios» (y antes del ping de Healthchecks, si existe):

```yaml
      - name: Vencer pedidos de la tienda
        # `!cancelled()` (y no `always()`, que corre también tras cancelar o
        # timeout): que una falla de los recordatorios no deje pedidos
        # reteniendo stock. Los avisos de este paso usan las mismas VAPID_*.
        if: ${{ !cancelled() }}
        env:
          DATABASE_URL: ${{ secrets.NEON_DATABASE_URL_POOLED }}
          DJANGO_DEBUG: "False"
          VAPID_PRIVATE_KEY: ${{ secrets.VAPID_PRIVATE_KEY }}
          VAPID_PUBLIC_KEY: ${{ secrets.VAPID_PUBLIC_KEY }}
          VAPID_ADMIN_EMAIL: ${{ secrets.VAPID_ADMIN_EMAIL }}
        run: python manage.py vencer_pedidos
```

**Atención:** los workflows corren contra producción SIN `migrate`. Si este paso llega a `main` antes que el deploy aplique la migración de `tienda`, el paso falla hasta que Render migre (el `buildCommand` corre `migrate`). Es aceptable (el paso está aislado y el de recordatorios no depende de él), pero anotarlo en el mensaje de commit y verificar el primer run después del deploy. **Lo mismo vale para `restaurar-demo.yml` y `sembrar-demo.yml`**, que desde la Tarea 1 tocan tablas de `tienda` vía `vaciar_gimnasio`/`sembrar_demo`: después del deploy, correr `restaurar-demo` a mano y verlo verde.

- [ ] **Step 6: Superadmin** — en `plataforma/views.py`, debajo de `ExportacionToggleView`:

```python
class TiendaToggleView(GimnasioDePlataformaMixin, View):
    """Prende y apaga el add-on de la tienda. POST-only y con `update()`, mismo
    criterio que `ExportacionToggleView`: no toca `modificado`. Apagarlo no
    cancela nada: el staff sigue cerrando los pedidos vivos."""

    def post(self, request, *args, **kwargs):
        gimnasio = self.get_gimnasio()
        habilitada = not gimnasio.tienda_habilitada
        Gimnasio.objects.filter(pk=gimnasio.pk).update(tienda_habilitada=habilitada)
        messages.success(
            request,
            f"Tienda {'habilitada' if habilitada else 'deshabilitada'} para {gimnasio.nombre}.",
        )
        return redirect(self.get_success_url())
```

(Copiar de `ExportacionToggleView` cómo obtiene `get_success_url`.) URL en `plataforma/urls.py`: ese archivo importa las clases una por una (`from plataforma.views import (...)`), así que sumar `TiendaToggleView` a ese import (orden alfabético) y, junto a `exportacion_toggle`, con el mismo formato multilínea:

```python
    path(
        "gimnasios/<int:pk>/tienda/",
        TiendaToggleView.as_view(),
        name="tienda_toggle",
    ),
``` En `GimnasioDetalleView`, agregar al contexto `"tienda_pedidos_vivos": contadores_panel(gimnasio)["vivos"]` con import **local** (`from tienda.services import contadores_panel` dentro del método, como hace `HomeView`). En el `<dl>` de la ficha, al lado de la fila de exportación (`gimnasio_detalle.html:246-249`), sumar `<dt>Tienda habilitada</dt><dd>{{ gimnasio.tienda_habilitada|yesno:"Sí,No" }}</dd>`. En el template, junto al form de exportación:

```html
    <form method="post" action="{% url 'plataforma:tienda_toggle' gimnasio.pk %}">
      {% csrf_token %}
      <button type="submit" class="boton-secundario">
        {% if gimnasio.tienda_habilitada %}Deshabilitar tienda{% else %}Habilitar tienda{% endif %}
      </button>
    </form>
    {% if tienda_pedidos_vivos %}<p class="texto-suave">{{ tienda_pedidos_vivos }} pedido{{ tienda_pedidos_vivos|pluralize }} de la tienda sin cerrar.</p>{% endif %}
```

Si `plataforma` tiene un test de invariante «toda vista es superadmin», `TiendaToggleView` lo cumple por `GimnasioDePlataformaMixin`.

- [ ] **Step 7: Correr** `$PY manage.py test tienda notificaciones plataforma -v 1` → PASS. Mutación: en `notificar_pedido_al_staff`, sacar `rol=Perfil.Rol.STAFF` → `test_siempre_con_rol_staff` falla; revertir.

- [ ] **Step 8: Commit**

```bash
npm run build:css
git status --short
git add tienda notificaciones plataforma templates .github/workflows/enviar-recordatorios.yml static/css/app.css
git commit -m "feat(tienda): avisos push, toggle del superadmin y cron de vencimiento

El paso nuevo del workflow falla hasta que el deploy aplique la migración de
tienda (los workflows no corren migrate): verificar el primer run."
```

---

### Task 9: Siembra demo, documentación y verificación final del hito

**Files:**
- Modify: `tenants/management/commands/sembrar_demo.py` (o `tenants/demo.py`, donde viva la lógica de siembra), `tenants/tests.py`, `templates/tenants/privacidad.html`, `CLAUDE.md`, `ARQUITECTURA.md`, `PRODUCT.md`, `ROADMAP.md`, `ISSUES.md`, `pagos/models.py` (docstring de `MedioCobro`), `docs/superpowers/specs/2026-10-17-tienda-design.md` (sección «Decisiones de ejecución»)

- [ ] **Step 1: Test de la siembra que falla** — en `tenants/tests.py`, dentro de `class SembrarDemoTests` (línea ~3782; su `setUp` crea `Gimnasio(slug="prueba")` **no demo** y `_sembrar(**kwargs)` llama `call_command("sembrar_demo", gimnasio="prueba", ...)`):

```python
    def test_la_tienda_no_se_siembra_en_un_gimnasio_que_no_es_demo(self):
        """`--confirmar` siembra sobre gimnasios con alumnos reales: ahí la
        tienda publicaría productos inventados a la vista de sus alumnos."""
        from tienda.models import Producto

        self._sembrar(alumnos=4, meses=1)

        self.assertFalse(Producto.objects.for_gimnasio(self.gimnasio).exists())

    def test_la_tienda_se_siembra_en_la_demo_sin_duplicar(self):
        from tienda.models import Pedido, Producto, Variante

        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self._sembrar(alumnos=4, meses=1)
        productos = Producto.objects.for_gimnasio(self.gimnasio).count()
        variantes = Variante.objects.filter(producto__gimnasio=self.gimnasio).count()

        self.assertGreater(productos, 0)
        self.assertTrue(Pedido.objects.for_gimnasio(self.gimnasio).exists())
        self.assertFalse(Producto.objects.for_gimnasio(self.gimnasio).exclude(foto="").exists())
        # Lo reservado sale del disponible: el chocolate se siembra con 8 y su
        # pedido en revisión retiene uno.
        from tienda.services import reservado_por_variante

        chocolate = Variante.objects.get(producto__gimnasio=self.gimnasio, nombre="Chocolate")
        self.assertEqual(chocolate.stock + reservado_por_variante(self.gimnasio).get(chocolate.pk, 0), 8)

        self._sembrar(alumnos=4, meses=1, confirmar=True)
        self.assertEqual(Producto.objects.for_gimnasio(self.gimnasio).count(), productos)
        self.assertEqual(Variante.objects.filter(producto__gimnasio=self.gimnasio).count(), variantes)
```

(Si la segunda siembra sobre un gimnasio con alumnos exige otro flag que `confirmar`, mirar `tenants/management/commands/sembrar_demo.py` y usar el real; el test existente `test_con_confirmar_siembra_igual` muestra cómo se pasa.) Run: `$PY manage.py test tenants.tests.SembrarDemoTests -v 2` → FAIL.

- [ ] **Step 2: Siembra** — en la función de siembra (`tenants/demo.py`, la que llama `sembrar_demo`), al final y **sólo si `gimnasio.es_demo`**:

```python
def _sembrar_tienda(gimnasio, alumnos):
    """Productos y pedidos de muestra para que el prospecto vea la tienda con
    vida. Sólo en la demo (en un gimnasio real `--confirmar` publicaría
    productos inventados) y sin fotos (el workflow de restaurar no tiene R2)."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from django.db.models import F

    from tienda.models import (
        ESTADOS_QUE_RETIENEN, CategoriaProducto, ItemPedido, Pedido, Producto, Variante,
        obtener_configuracion,
    )

    config = obtener_configuracion(gimnasio)
    config.publicada = True
    config.instrucciones_retiro = "Retirás en recepción, de lunes a viernes de 8 a 21."
    config.save()
    suplementos, _ = CategoriaProducto.objects.get_or_create(gimnasio=gimnasio, nombre="Suplementos", defaults={"orden": 1})
    ropa, _ = CategoriaProducto.objects.get_or_create(gimnasio=gimnasio, nombre="Ropa", defaults={"orden": 2})
    catalogo = [
        (suplementos, "Proteína whey 1 kg", Decimal("32000"), [("Chocolate", 8), ("Vainilla", 5)]),
        (suplementos, "Creatina 300 g", Decimal("21000"), [("", 12)]),
        (ropa, "Remera dry-fit", Decimal("15000"), [("S", 4), ("M", 6), ("L", 3), ("XL", 0)]),
    ]
    variantes = []
    for categoria, nombre, precio, talles in catalogo:
        producto, _ = Producto.objects.get_or_create(
            gimnasio=gimnasio, nombre=nombre, defaults={"categoria": categoria, "precio": precio}
        )
        for talle, stock in talles:
            variante, _ = Variante.objects.get_or_create(producto=producto, nombre=talle, defaults={"stock": stock})
            variantes.append(variante)
    if Pedido.objects.for_gimnasio(gimnasio).exists() or len(alumnos) < 3:
        return
    ahora = timezone.now()
    # El entregado se pagó hace 35 días: así la tarjeta «Ventas de la tienda»
    # del dashboard de la demo muestra dos meses y no una sola barra.
    hace_un_mes = ahora - timedelta(days=35)
    pagado_en = {
        Pedido.Estado.EN_REVISION: None,
        Pedido.Estado.PAGADO: ahora,
        Pedido.Estado.ENTREGADO: hace_un_mes,
    }
    estados = [Pedido.Estado.EN_REVISION, Pedido.Estado.PAGADO, Pedido.Estado.ENTREGADO]
    for alumno, estado, variante in zip(alumnos[:3], estados, variantes):
        pedido = Pedido.objects.create(
            gimnasio=gimnasio, alumno=alumno, estado=estado, total=variante.producto.precio,
            reservado_hasta=ahora, pagado_en=pagado_en[estado],
            entregado_en=hace_un_mes if estado == Pedido.Estado.ENTREGADO else None,
            comprobante_subido_en=ahora if estado == Pedido.Estado.EN_REVISION else None,
        )
        ItemPedido.objects.create(
            pedido=pedido, variante=variante, nombre=str(variante),
            precio_unitario=variante.producto.precio, cantidad=1,
        )
        if estado in ESTADOS_QUE_RETIENEN:
            # `Variante.stock` es el DISPONIBLE: un pedido que retiene lo
            # descuenta, como `crear_pedido`. Si no, «disponible + reservado»
            # da una unidad de más en la demo.
            Variante.objects.filter(pk=variante.pk).update(stock=F("stock") - 1)
```

Llamarla **dentro** del `with transaction.atomic()` de `tenants/demo.py::sembrar_demo`, al final del bloque (después de las novedades; el `return resumen` de la línea ~505 está FUERA del atomic), con la lista local `alumnos` (línea ~239), y que devuelva los conteos para el resumen (el comando imprime cada clave, `sembrar_demo.py:94`):

```python
        if gimnasio.es_demo:
            productos, pedidos = _sembrar_tienda(gimnasio, alumnos)
            resumen["productos_tienda"] = productos
            resumen["pedidos_tienda"] = pedidos
```

Para eso, `_sembrar_tienda` termina con `return (Producto.objects.for_gimnasio(gimnasio).count(), Pedido.objects.for_gimnasio(gimnasio).count())` (y el `return` temprano de «ya hay pedidos» pasa a ser un salto al mismo `return` final). En `sembrar_demo.py:65`, el texto de ayuda de `--borrar` suma «y sus pedidos de la tienda». Agregar a `RestaurarDemoTests` (molde: `test_borra_lo_que_habia_cargado_el_prospecto`, ~línea 4620) un test que cree un `Producto` «del prospecto» en la demo y verifique que `restaurar_demo` lo saca y vuelve a sembrar el catálogo de muestra. Los pedidos sembrados van a alumnos con `MARCA`, así que `borrar_demo` los limpia (Tarea 1, Step 9). Correr los tests de siembra → PASS.

- [ ] **Step 3: Documentación** (en este orden; texto concreto, sin cifras inventadas — barrer números antes de commitear):
  - `CLAUDE.md`: reescribir el matiz del principio #3 así: «**Matiz desde el 2026-09-29 y el 2026-10-17:** Mercado Pago entró para que el gimnasio le pague la suscripción a la plataforma (`suscripcion/`) y, desde el Hito 2 de la tienda, para que el alumno le pague al gimnasio **sólo en la tienda** (`tienda/`, a la cuenta MP del propio gimnasio). El cobro de cuotas (`pagos`) sigue sin MP: el staff confirma.» Agregar fila a la tabla: `| Tienda (add-on), productos, pedidos, stock | Tienda del gimnasio (`tienda/`) |`. Agregar a Comandos: `python manage.py vencer_pedidos  # vence pedidos de la tienda sin comprobante + avisos`. En «Qué NO construir», al párrafo de Mercado Pago agregarle «…y la tienda (`tienda/`)».
  - `ARQUITECTURA.md`: sección nueva `## Tienda del gimnasio (`tienda/`, 2026-10-17)` con: qué es, los modelos, la máquina de estados, las 14 trampas de la spec (resumidas en una línea cada una con el test que la fija), el camino perezoso vs el cron, por qué `Variante.stock` es disponible, la foto en `URLS_SIN_BLOQUEO`, **el rechazo del comprobante con reintento** (vuelve a `PENDIENTE_PAGO` con plazo nuevo y el stock reservado; el archivo viejo se borra sólo si el nuevo entró; el recordatorio de 24 h en revisión sale una vez por pedido, aceptado), **la regla del arrepentimiento** (pagado sin retirar → siempre; retirado → 10 días desde `entregado_en` en fecha argentina; «Botón de arrepentimiento» visible en catálogo y «Mis pedidos»), **las estadísticas de ventas** (`tienda/analitica.py`: `PAGADO`+`ENTREGADO` por mes de `pagado_en`, `DEVUELTO` reescribe el mes; agrupa por `TruncMonth` en hora argentina; un gimnasio sin tienda no paga ninguna query), el chip «Adicional» de la demo y el umbral de «Quedan N», y en la lista de archivos que van a R2 (§ Deploy, buscar «cuatro campos») sumar `Producto.foto` y `Pedido.comprobante`.
  - Otras secciones de `ARQUITECTURA.md` que la tienda cambia: «Indicadores temporales del panel» (una línea: «Las ventas de la tienda viven en `tienda/analitica.py`, con el mismo molde; sin el add-on no corre ninguna query»), «Panel de plataforma» (`URLS_SIN_BLOQUEO` ya no son sólo nombres de `tenants`: entra `tienda_foto_producto`, comparado por `url_name` sin namespace, de ahí el prefijo), «Datos de demostración» / «Cuenta de demostración compartida» (siembra de la tienda sólo en `es_demo`, orden de `vaciar_gimnasio`/`borrar_demo`), «PWA + Web Push» (tipos `PEDIDO_VENCIDO`/`PEDIDO_EN_REVISION` y el paso `vencer_pedidos` del workflow). Actualizar también el docstring de `URLS_SIN_BLOQUEO` en `plataforma/middleware.py` con lo mismo. Si los componentes nuevos (tarjeta de producto del catálogo) introducen un patrón visual, documentarlo en `DESIGN.md`.
  - `PRODUCT.md`: en los tres lugares que dicen «sin Mercado Pago», aclarar que vale para las cuotas y que la tienda es un add-on con transferencia (y MP desde el Hito 2).
  - `ROADMAP.md`: bloque `> **Actualizado (2026-10-17).** …` en la sección de «No construir todavía» explicando la tienda como add-on.
  - `ISSUES.md`: entrada `[2026-10-17] Tienda: derecho de arrepentimiento` (riesgo aceptado: botón «Pedir cancelación» resuelto por el staff, plazo contado desde el retiro —pagado sin retirar se puede siempre—, link «Botón de arrepentimiento» visible en el catálogo y en «Mis pedidos», y el número de pedido como código de la solicitud; **revisar con un abogado** si alcanza con eso para la Ley 24.240 art. 34 / Res. 424/2020, en particular si el botón tiene que estar también fuera del login) y `[2026-10-17] Principio #3 abierto para la tienda`.
  - `pagos/models.py::MedioCobro` docstring: «Lo muestran también la tienda (`tienda/`) en el detalle del pedido por transferencia».
  - Test de la política (molde: `plataforma/tests_actividad.py:713-720`): `test_avisa_que_se_guardan_los_pedidos_y_comprobantes_de_la_tienda` (GET a `politica_privacidad`, `assertContains` «tienda» y «comprobante»). Escribirlo ANTES de tocar el template.
  - `templates/tenants/privacidad.html`: agregar un párrafo «Compras en la tienda del gimnasio: si tu gimnasio tiene tienda, guardamos tus pedidos (qué compraste, cuándo y cuánto pagaste) y el comprobante de transferencia que subas. Los ve el personal de tu gimnasio y se incluyen en la exportación de datos del gimnasio.» y actualizar la fecha de «Última actualización». (Mercado Pago se agrega en el Hito 2.)
  - Spec: sección final `## Decisiones de ejecución (2026-10-17)` con: (1) dedup de avisos por `RecordatorioEnviado` en lugar de `Pedido.aviso_revision_en`; (2) `Pedido.cancelacion_resuelta_en` agregado para sacar de la cola las cancelaciones rechazadas; (3) el staff puede «Marcar pagado» directo desde `PENDIENTE_PAGO` (pago en mostrador); (4) `ConfiguracionTienda` del Hito 1 no tiene `acepta_transferencia`, `acepta_mercadopago` ni `minutos_reserva_mp` (con un solo medio no hay qué elegir; llegan con el Hito 2), y `Pedido.Medio` tiene sólo `TRANSFERENCIA`; (5) la portada no cambia en el Hito 1 (el add-on se cobra desde el Hito 3; mientras tanto la demo lleva el chip «Adicional»).

- [ ] **Step 4: Verificación completa del hito**

```bash
$PY manage.py makemigrations --check --dry-run
$PY manage.py test --parallel 4 2>&1 | grep -E "^Ran|^OK|FAILED"
git status --short
```
Expected: `No changes detected`; `OK` con 2673 + los nuevos; nada sin agregar.

- [ ] **Step 5: QA en el navegador** (obligatorio, el code-review no lo reemplaza). `runserver` en el worktree contra una COPIA de la base de dev; con la cuenta de prueba (habilitar `tienda_habilitada` desde el panel de plataforma):
  1. Staff: crear categoría, producto con 3 talles y foto; ver la foto en la lista.
  2. Alumno (otra ventana/incógnito): catálogo → carrito → confirmar → ver datos de transferencia → subir comprobante.
  3. Staff: tarjeta del panel → pedido → marcar pagado → entregar.
  4. Pedido con plazo corto (`horas_reserva_transferencia=1` y editar `reservado_hasta` desde `/admin/`) → abrir el catálogo → ver el stock devuelto.
  5. Rechazar un comprobante y, como alumno, ver el motivo y subir otro (el stock queda reservado todo el tiempo); ajustar stock con una reserva viva; intentar borrar un producto vendido; pedir cancelación desde el «Botón de arrepentimiento» y ver «Solicitud registrada — pedido #N».
  5b. Dashboard: tarjeta «Ventas de la tienda» con el gráfico y el top; «Vendido este mes» en pedidos; «Quedan N» en el catálogo y «Stock bajo» en la lista del staff; en la cuenta demo, el chip «Adicional» en la barra y en el título.
  6. Apagar el add-on desde plataforma con pedidos vivos → el staff sigue entregando, el alumno ve «Mis pedidos».
  7. Layout de celular de catálogo/carrito/mi pedido con el iframe de 390 px (ver `facts_verificar_layout_de_celular`).
  8. Un gimnasio SIN tienda: panel, barra, portal del alumno, cuotas y suscripción se ven igual que en `main`.
  Si un `.css`/`.js` no cambia, sospechar del service worker.

- [ ] **Step 6: Commit y review del branch**

```bash
npm run build:css
git add -A tienda tenants templates CLAUDE.md ARQUITECTURA.md PRODUCT.md ROADMAP.md ISSUES.md pagos/models.py docs static/css/app.css
git status --short
git commit -m "docs(tienda): principio #3, arquitectura, privacidad y siembra demo del Hito 1"
```

Después: review final del branch completo (superpowers:requesting-code-review) y recién ahí proponer el merge al usuario. **No mergear ni pushear sin su OK.**
