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
        # `.order_by()` explícito: el agrupado depende sólo de `mes_venta`.
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
