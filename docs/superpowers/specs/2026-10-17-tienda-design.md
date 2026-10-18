# Tienda del gimnasio (add-on) — diseño

Fecha: 2026-10-17. Plan de implementación del Hito 1:
`docs/superpowers/plans/2026-10-17-tienda-hito-1.md`. El plan del Hito 2 (Mercado Pago) se
escribe después del spike de sandbox (§ «Lo que hay que hacer a mano»).

Documento de diseño aprobado por el dueño del producto el 2026-10-16, después de **dos
revisiones adversariales** (22 + 25 hallazgos, verificados contra el código). La sección
**«Decisiones de ejecución»** al final, cuando exista, manda sobre cualquier párrafo anterior que
la contradiga.

## Contexto

Muchos gimnasios venden suplementos y ropa deportiva en el mostrador, anotando a mano quién
compró, quién pagó y qué queda. El dueño del producto quiere venderles un **servicio aparte**: el
staff carga sus productos (con variantes y stock) y el alumno los compra desde su portal, con
carrito, pagando por **Mercado Pago a la cuenta del propio gimnasio** o por **transferencia con
comprobante**. Al confirmar el pedido el stock queda **reservado** por un plazo; si vence sin
pago ni comprobante, el stock vuelve a estar disponible. El alumno retira en el gimnasio.

**Cambia un principio no negociable.** `CLAUDE.md` #3 dice que Mercado Pago entró *sólo* para
que el gimnasio le pague a la plataforma, y que eso no abre la puerta al cobro gimnasio→alumno.
Esta feature lo abre **acotado a la tienda**: las cuotas (`pagos`) siguen exactamente igual, sin
MP, con el staff confirmando. Se reescribe el principio en el mismo PR, junto con `PRODUCT.md`,
`ROADMAP.md`, la portada y la política de privacidad, que hoy dicen lo contrario.

## Decisiones tomadas (no re-litigar)

| Tema | Decisión |
|---|---|
| A qué cuenta va la plata de MP | La del **gimnasio**, conectada por **OAuth** («Conectar Mercado Pago»). La plataforma nunca toca el dinero de los alumnos. |
| Variantes | **Sí**, con stock propio (talle, color, sabor). El precio es del producto. |
| Entrega | **Retiro en el gimnasio**. Sin envíos. |
| Plazo de reserva | **Configurable por gimnasio**: default 30 min MP (mínimo 20), 24 h transferencia. |
| Comprobante subido a tiempo | El stock queda reservado **sin límite** hasta que el staff decide; recordatorio a las 24 h (una vez) y tarjeta en el panel. |
| Arrepentimiento (Ley 24.240 art. 34) | Botón **«Pedir cancelación»**: siempre mientras el pedido está pago sin retirar, y hasta 10 días corridos después del retiro (la ley cuenta desde la entrega o el contrato, lo último); link visible «Botón de arrepentimiento» (Res. 424/2020). Lo resuelve el staff. Entrada en `ISSUES.md` para revisarlo con un abogado. |
| Categorías | **Por gimnasio**, sin lista fija (la lección de `CategoriaEjercicio`). |
| Cobro del add-on | **USD 20 por mes, sumados a la suscripción** (definido el 2026-10-17). El plan anual se cobra igual que el tramo: 10 meses (USD 200). La portada deja de decir «todas las funciones incluidas». **Hito 3 HECHO el 2026-10-18** (se cobra desde el próximo pago; sin prueba gratis propia; una línea «+ Tienda online (opcional)» bajo los tramos). |
| Alumno moroso | **Puede comprar**: es venta para el gimnasio. La tienda se bloquea sólo con el gimnasio congelado. |
| Carrito | En la **sesión**, sin modelo y sin reservar stock. |
| Comisión de la plataforma (`marketplace_fee`) | Fuera de v1. |

## Arquitectura

App nueva **`tienda/`**, en `INSTALLED_APPS` después de `pagos` y `suscripcion` (importa
`pagos.MedioCobro` y el cliente `suscripcion/mercadopago.py`), con la forma de sus hermanas:
`services.py` con errores de dominio (`motivo` + `MENSAJES`), `obtener_configuracion()`,
tests separados por capa.

### Modelos

`TenantOwnedModel` (van a los TRES lugares):

- **`ConfiguracionTienda`** — 1 por gimnasio (`UniqueConstraint(gimnasio)`), separada de
  `Gimnasio` para no tocar su `modificado` (versiona logo/fondo/ícono PWA): `publicada`,
  `acepta_transferencia`, `acepta_mercadopago`, `minutos_reserva_mp`, `horas_reserva_transferencia`,
  `max_pedidos_activos` (default 2), `instrucciones_retiro`.
- **`CuentaMercadoPago`** — 1 por gimnasio: `access_token`/`refresh_token` cifrados,
  `mp_user_id`, `token_expira`, `uuid_webhook`, `es_de_prueba`, `estado`
  (conectada/reconectar/desconectada). Va a `EXCLUIDOS` de la exportación.
- **`CategoriaProducto`** — `nombre`, `orden`, único por gimnasio.
- **`Producto`** — `categoria` (`SET_NULL`), `nombre`, `descripcion`, `precio` (`Decimal(10,2)`),
  `foto` (`upload_to="tienda/productos/"`, reescalada a ≤1200 px JPEG), `publicado`, `orden`.
- **`Pedido`** — `alumno` (PROTECT), `medio`, `estado`, `total`, `reservado_hasta`, `comprobante`
  (`upload_to="tienda/comprobantes/"`, las extensiones de `pagos` + ≤5 MB + chequeo Pillow),
  `comprobante_subido_en`, `referencia_externa` (UUID único), `preference_id`/`init_point`
  (`TextField`), `payment_id` (único parcial), `estado_mp`, `respuesta_mp`, `pagado_en`,
  `entregado_en`, `motivo` (largo validado en Python), `cancelacion_pedida_en`,
  `aviso_revision_en`.

**No** `TenantOwnedModel` (cuelgan de un padre que sí lo es, como `RutinaPlantillaItem`):

- **`Variante`** — `producto` (CASCADE), `nombre` (vacío = producto sin variantes), `stock`
  (**disponible**, no físico; `PositiveIntegerField` + `CheckConstraint(stock__gte=0)`), `activa`.
  Todo producto tiene al menos una.
- **`ItemPedido`** — `pedido` (CASCADE), `variante` (PROTECT), snapshot de `nombre` y
  `precio_unitario`, `cantidad` (`MinValueValidator(1)` + `CheckConstraint(cantidad__gte=1)`).

Plataforma: **`Gimnasio.tienda_habilitada`** (la prende sólo el superadmin, por `update()`,
fuera de `GimnasioForm`) y la property **`puede_usar_tienda`** (`es_demo or tienda_habilitada`).

## Estados y stock

```
PENDIENTE_PAGO ─(comprobante a tiempo)→ EN_REVISION ─(staff confirma)→ PAGADO ─→ ENTREGADO
   │ └─(MP aprobado)──────────────────────────────────────────────────→ PAGADO
   ├─(vence)→ VENCIDO          EN_REVISION ─(staff rechaza el comprobante)→ PENDIENTE_PAGO
   │                              (plazo nuevo, motivo visible, el stock SIGUE reservado)
   │                           EN_REVISION ─(staff cancela)→ CANCELADO
   └─(alumno cancela, sólo transferencia)→ CANCELADO
PAGADO ─(staff cancela / MP refunded|charged_back / arrepentimiento)→ DEVUELTO
ENTREGADO ─(MP refunded|charged_back / arrepentimiento)→ DEVUELTO (sin tocar stock)
VENCIDO/CANCELADO ─(MP aprobado tarde)→ re-reserva todo-o-nada → PAGADO; si no → REVISAR
REVISAR ─→ DEVUELTO | ENTREGADO (pide stock con F())
```

**Estados que retienen stock: `PENDIENTE_PAGO`, `EN_REVISION`, `PAGADO`.** `REVISAR` no retiene.

## Las trampas, resueltas

1. **Variante de otro gimnasio en la sesión.** La reserva filtra `producto__gimnasio=gimnasio`, y
   también al agregar al carrito y en la re-reserva tardía.
2. **Cantidad ≤ 0 suma stock** (`stock__gte=-5` siempre se cumple). Se valida `1 ≤ n ≤ tope` en el
   carrito y otra vez en `crear_pedido`; `CheckConstraint` en `ItemPedido`.
3. **Sobreventa.** Nunca leer-y-restar: `Variante.objects.filter(pk=…, producto__gimnasio=…,
   stock__gte=n, activa=True, producto__publicado=True).update(stock=F("stock") - n)`, por ítem
   ordenado por `variante_id`, en `atomic()`; rowcount 0 → error «sin stock de X» y rollback total.
4. **Doble liberación.** Toda transición es `Pedido.objects.filter(pk=…,
   estado__in=ORIGEN).update(...)`, y el stock se libera **sólo si tocó una fila**. Funciona igual
   en SQLite (donde `select_for_update` no hace nada) y en Postgres. Orden global de locks: pedido
   y después variantes por `variante_id`.
5. **El staff pisa reservas al editar el stock.** El form nunca escribe `stock` absoluto: tiene un
   «ajuste ±» que se aplica con `update(stock=F("stock") + delta)` (rechazado si deja menos de
   0), y la pantalla muestra «disponible / reservado».
6. **Los crons de GitHub llegan tarde** (2-4 h en el plan free). Un plazo de 30 min no puede
   depender del cron: `vencer_pedidos` corre **perezosamente** al abrir el catálogo, el carrito, el
   checkout, el detalle, el listado del staff y antes de aceptar un comprobante. El cron es
   respaldo. **El camino perezoso nunca toca la red ni manda push**: con un worker de gunicorn,
   seis llamadas de 5 s son un 502.
7. **`vencer_pedidos` concurrente.** `select_for_update(skip_locked=True)` ordenado por pk,
   transición condicional, después **una** query agregada `Sum(cantidad)` por variante y un
   `update(F()+n)` por variante. El costo no depende de la cantidad de pedidos.
8. **Comprobante subido justo al vencer.** Se guarda el archivo, después la transición condicional
   con 2 min de gracia; si no tocó filas, se borra el archivo y se muestra el cartel de vencido.
   Sólo con `medio == TRANSFERENCIA`. Si el pedido ya tenía un comprobante (rechazado), el
   archivo viejo se borra **sólo si la transición entró**; si no, se borra el nuevo y el viejo
   queda como estaba.
9. **Acaparar stock.** Tope de pedidos activos por alumno (default 2); no se compra durante una
   suplantación.
10. **Borrar un producto vendido da 500.** `core/borrado.py::_relaciones` mira un solo nivel y
    prometería el borrado (Producto→Variante es CASCADE; la PROTECT está en ItemPedido→Variante).
    Con ventas se archiva; `can_delete` del formset y la vista de borrar lo chequean a mano.
11. **`notificar_a_gimnasio` sin `rol` le avisa a todos, alumnos incluidos.** Siempre
    `rol=Perfil.Rol.STAFF`.
12. **La foto del producto** va por una vista propia con URL versionada `?v=` e `immutable`, nunca
    `.url` firmada de R2; su `url_name` único `tienda_foto_producto` va a `URLS_SIN_BLOQUEO` (si no,
    el middleware le cuelga una cookie a una respuesta que se cachea un año). Se sirve si el
    producto existe y el gimnasio tiene tienda, sin exigir `publicado` (el staff ve sus
    borradores) y con el gimnasio activo. A diferencia del logo, no se cachea el contenido en
    LocMem del lado del servidor: son muchas fotos por proceso y con `immutable` el navegador
    ya no las vuelve a pedir.
13. **Apagar el add-on con pedidos vivos.** El alumno pierde catálogo y checkout pero ve sus
    pedidos; el staff sigue gestionando los existentes; la ficha del superadmin muestra «N pedidos
    vivos» junto al toggle.
14. **Demo compartida.** En `es_demo` no se conecta Mercado Pago (las compras de un prospecto le
    caerían a la cuenta de otro). La siembra de la tienda corre sólo con `es_demo`, con
    `get_or_create` y sin fotos.

## Mercado Pago del gimnasio (Hito 2)

**HITO 2 HECHO el 2026-10-18, APAGADO hasta la prueba guiada** (`docs/mercadopago-tienda.md`; se prende con las 4 variables en Render y los mismos 4 secrets en GitHub). Plan: `docs/superpowers/plans/2026-10-18-tienda-cobro-y-mercadopago.md` (Parte B); lo construido está descrito en `ARQUITECTURA.md` «Mercado Pago de la tienda». **Cambios deliberados respecto de lo que dice esta sección:**

- Un segundo pago aprobado sobre un pedido ya pagado **NO lo pasa a `REVISAR`** (perdería el estado y el stock): el pedido queda como está, el pago va a `pagos_adicionales` y aparece en la pestaña «Revisar MP».
- El tópico `mp-connect` (desautorización) queda fuera: un 401 al usar el token cumple la misma función (la cuenta pasa a RECONECTAR).
- El refresco de tokens corre en el workflow de cada 6 h (`reconciliar-cobros.yml`, no un cron diario aparte); sólo toca cuentas con menos de 30 días de vigencia.
- La firma de la `notification_url` sigue siendo pregunta de la prueba: hoy la autenticidad sale de leer el pago de la API con el token del gimnasio.
- La devolución es siempre manual desde la cuenta de Mercado Pago del gimnasio (la automática queda fuera de esta versión).

- Settings (grupo `_bandera_todo_o_nada`, apagado en `TESTING`): `MERCADOPAGO_CLIENT_ID`,
  `MERCADOPAGO_CLIENT_SECRET`, `MERCADOPAGO_OAUTH_REDIRECT_URI`, `MP_TOKEN_ENCRYPTION_KEY` →
  `TIENDA_MP_ENABLED`. Sin ellas, la tienda funciona sólo con transferencia.
- OAuth con `state` firmado (gimnasio + usuario), PKCE y `offline_access`. Refresh con
  `select_for_update` y relectura tras el lock (el refresh token es de un solo uso). Cron diario
  para las cuentas con menos de 30 días.
- Preferencia con `binary_mode` y **`expiration_date_to = reservado_hasta − 10 min`**: la reserva
  local dura más que la preferencia. El alumno no cancela un pedido MP con la preferencia vigente.
- Webhook `tienda/v1/webhook/mercadopago/<uuid_webhook>/`. **La autenticidad sale de la API**:
  estado leído con el token de ESE gimnasio, `external_reference` de un pedido suyo, monto ±0,01,
  `ARS`; un segundo pago aprobado va a `REVISAR`. La firma es obligatoria o no se valida, según el
  spike — nunca «si viene».
- `acreditar_pedido` es **permisivo** (no se niega por `publicada`, `tienda_habilitada` ni cuenta
  congelada); lo llaman webhook, retorno y `reconciliar_pedidos`.
- Desconectar es lógico y se rechaza con pedidos MP vivos.
- Tokens: `include_local_variables=False` en Sentry y el test de frames extendido al token por
  parámetro y al canje OAuth.

## Invariantes que no se pueden romper

Todo cambio sobre código existente es **aditivo, con default = comportamiento actual**, y lo fija
un test de caracterización escrito antes de tocarlo:

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| `suscripcion/mercadopago.py` | parámetros opcionales; `_headers(clave_idempotencia, token=None)`; `es_de_prueba(token=None)` | suite actual de `suscripcion` + test de frames |
| Cobro de la suscripción | add-on sumado **sólo si `tienda_habilitada`** | gimnasio sin tienda → monto, `kpis` y `fila_de_gimnasio` idénticos |
| Webhook de `suscripcion` | si el spike lo pide: 401/403 de `consultar_pago` → 200 | test nuevo |
| `calendario/fields.py` | `clave_setting=` con `deconstruct()` que omite el default | `makemigrations --check` |
| `Gimnasio` | campo nuevo, toggle por `update()`, fuera de `GimnasioForm` | test de que el form no lo expone |
| Middleware de plataforma | sólo un `url_name` más en `URLS_SIN_BLOQUEO` | tests del middleware |
| Demo | orden Pedido → Producto → Categoría → Alumno en `vaciar_gimnasio` y `borrar_demo` | `_ensuciar` + test de siembra sólo en demo |
| Exportación | hojas nuevas, `CuentaMercadoPago` excluida, LEEME | `tests_exportacion.py` con conteos y token reconocible |
| Workflows | **ninguno nuevo**: pasos dentro de `enviar-recordatorios`, `reconciliar-cobros` y `avisar-cobros`, con `if: always()` | correrlos a mano después del deploy |
| `render.yaml`, `.env.example` | variables nuevas | `BlueprintDeclaraLoQueSettingsLeeTests` |

## Pantallas

- **Staff** — link «Tienda» en la barra **sólo si `puede_usar_tienda`** (el resto de los
  gimnasios no ve cambios). Productos (categorías, formset de variantes, ajuste ±,
  disponible/reservado, foto), pedidos (a revisar / a entregar / cancelaciones pedidas / revisar
  MP / todos) con «Vendido este mes: $X · N pedidos», configuración. Tarjeta «Pedidos a revisar
  / a entregar» en el panel, con una query agregada, y tarjeta «Ventas de la tienda» en el
  dashboard (ventas por mes, este mes vs el anterior, ticket promedio, top 5 de productos; sólo
  con `puede_usar_tienda`, sin costo para el resto). Badge «Stock bajo» (≤ 3) en la lista de
  productos. En la demo, el link «Tienda» y el título llevan el chip «Adicional».
- **Alumno** — card «Tienda» en su portal (si `puede_usar_tienda` y `publicada`); catálogo por
  categoría, carrito, checkout, «Mis pedidos», detalle con cuenta regresiva, datos de
  transferencia (`pagos.MedioCobro` activos), comprobante (y, si fue rechazado, el motivo y el
  plazo nuevo para subir otro), «Pagar con Mercado Pago», «Pedir cancelación» y el link «Botón de
  arrepentimiento» en el catálogo y en «Mis pedidos». «Quedan N» en las variantes con 1 a 3
  unidades.
- **Superadmin** — toggle «Tienda» + «N pedidos vivos» en la ficha del gimnasio.
- `hx-boost="false"` en links a MP, subidas y redirects cross-origin; cada campo con su línea de
  error; los carteles responden 200.

## Notificaciones

Staff (`rol=STAFF`): pedido nuevo, comprobante subido, pago MP aprobado, `REVISAR`,
devolución/contracargo, cancelación pedida, `EN_REVISION` de más de 24 h (una vez por pedido,
dedup con `RecordatorioEnviado`: si el comprobante se rechaza y se resube, el segundo período en
revisión no vuelve a recordar — aceptado). Alumno: confirmado/listo para retirar, rechazado (con
el plazo nuevo para subir otro), vencido (sólo desde el cron).

## Lo que hay que hacer a mano

- **Spike en sandbox de MP antes del Hito 2**, con cuentas de prueba del dueño del producto:
  ¿el token OAuth de un vendedor de prueba es `TEST-` o `APP_USR-`?; qué llega a la
  `notification_url` de una preferencia creada con el token del vendedor y con qué firma; si esos
  pagos también le llegan al webhook de `suscripcion`; el tópico `mp-connect`.
- En la app MP de la plataforma: habilitar OAuth, registrar la redirect URI, cargar las cuatro
  variables en Render y los secrets de los workflows.
- ~~Definir `PRECIO_TIENDA_USD`~~ → **USD 20/mes** (2026-10-17).

## Fuera de alcance (explícito)

Envíos, precio por variante, comisión de la plataforma, Mercado Pago para las cuotas, débito
automático, cupones/descuentos, facturación electrónica.

## Hitos

1. **Tienda con transferencia**, mergeable y desplegable sola (sin una sola línea de MP).
2. **Mercado Pago del gimnasio**, después del spike.
3. **Cobro del add-on** (USD 20/mes) en la suscripción, el panel de plataforma y la portada.

## Qué cambió en la revisión del plan del Hito 1 (2026-10-17)

Dos pasadas adversariales sobre el plan detallado (corrección; no-regresión y armonía)
encontraron 13 + 34 puntos, verificados contra el código. Los que cambian el diseño:

- **El UPDATE que reserva stock va sin JOIN.** Con `producto__…` en el filtro, Django arma
  `WHERE id IN (subquery)` y Postgres, después de esperar el lock de fila, no re-evalúa
  `stock >= n`: la compra concurrente de la última unidad terminaba en la CHECK y un 500. La
  pertenencia al gimnasio se valida antes, al armar el diccionario de variantes.
- **El checkout compra lo que el alumno ve** (líneas saneadas del carrito), y leer el carrito
  limpia de la sesión lo que ya no se vende: un producto archivado no traba la compra del resto.
- **Cancelar desde el staff resuelve una cancelación pedida**, y el contador de cancelaciones
  sólo mira pedidos pagados o entregados: si no, la tarjeta del panel no se iba nunca.
- **«Mis pedidos» sigue accesible desde el portal con la tienda apagada** si el alumno tiene
  pedidos sin cerrar.
- Avisos «vencido» y «revisión demorada» como funciones de `notificaciones/services.py` con el
  chequeo de perfil ANTES del dedup; el comando sólo itera.
- El paso del workflow usa `if: ${{ !cancelled() }}`, no `always()`.

## Ajustes del 17-10 (pedidos por el usuario)

Después de aprobar el plan, el usuario pidió (y aprobó) estos cambios:

1. **Arrepentimiento contado desde la entrega.** El art. 34 cuenta los 10 días desde la entrega
   del bien o la celebración del contrato, **lo que ocurra último**. Con retiro en el gimnasio,
   contar desde el pago dejaba sin derecho a quien retira a las dos semanas. Regla:
   `PAGADO` (sin retirar) → siempre se puede pedir; `ENTREGADO` → hasta
   `localdate(entregado_en) + 10`. Link visible «Botón de arrepentimiento» (Res. 424/2020) en el
   catálogo y en «Mis pedidos», que lista los pedidos cancelables; la confirmación muestra
   «Solicitud registrada — pedido #N». Se mantiene la entrada de `ISSUES.md` para el abogado.
2. **Rechazo del comprobante con reintento.** Rechazar ya no cancela: `EN_REVISION →
   PENDIENTE_PAGO` con `reservado_hasta = ahora + horas_reserva_transferencia`, el motivo a la
   vista del alumno y **el stock sigue reservado**. Si no resube, vence como cualquier pendiente.
   Para cortar, el staff usa «Cancelar pedido».
3. **«Vendido este mes»** en la pantalla de pedidos del staff (`PAGADO` + `ENTREGADO` por
   `pagado_en` en el mes de `timezone.localdate()`; `DEVUELTO` no cuenta).
4. **Chip «Adicional» en la demo** (link y título de la tienda). Nació porque la portada decía «todas
   las funciones incluidas»; desde el Hito 3 (18-10) sigue porque la tienda es un adicional que se cobra aparte.
5. **Tarjeta «Ventas de la tienda» en el dashboard** (`tienda/analitica.py`, molde de
   `tenants/analitica.py::ingresos_por_mes`): una fila por mes aunque esté vacío, UNA query por
   indicador, sólo si `puede_usar_tienda` (un gimnasio sin tienda no paga ninguna query).
   Consecuencia asumida: un pedido devuelto después deja de contar en el mes en que se pagó (la
   historia se reescribe; es lo honesto, porque esa plata se devolvió).
6. **«Quedan N»** en el catálogo y badge «Stock bajo» en la lista del staff (umbral fijo 3).
7. La tarea de servicios del plan se parte en dos (reserva/vencimiento y transiciones).

Ajustes de la revisión adversarial de estos cambios:

- Vencer o cancelar (el alumno) un pedido limpia `motivo`: el de un comprobante rechazado ya
  no explica nada. Los textos de «vencido» y `plazo_vencido` son neutros («venció», «se da de
  baja»), porque el pedido pudo tener un comprobante rechazado.
- `subir_comprobante` lee el nombre del archivo anterior de la base, con la fila bloqueada,
  justo antes del `UPDATE` condicional (la instancia del request puede ser vieja).
- El alumno ve el cartel de rechazo si el pedido está esperando el pago **y tiene
  comprobante** (como el staff), aunque el motivo venga vacío.
- El «Botón de arrepentimiento» filtra en la base con `q_puede_pedir_cancelacion` (misma regla
  que `puede_pedir_cancelacion`, fijada por un test que compara las dos), sin cortar antes.
- La siembra de la demo descuenta del disponible lo que retienen sus pedidos.
- La tarjeta de ventas redondea los montos como «Ingresos cobrados por mes» y aclara que el
  mes actual está en curso.

## Decisiones de ejecución

- **Dedup de avisos del cron (Tarea 8):** el recordatorio de 24 h («comprobante sin revisar») y el aviso de «vencido» se deduplican con `RecordatorioEnviado` (`PEDIDO_EN_REVISION`, `PEDIDO_VENCIDO`), como todos los recordatorios del proyecto, y no con el campo `Pedido.aviso_revision_en` que decía la sección anterior. El campo no se crea.
- **«cancelacion_resuelta»:** las vistas mandan la misma clave al aceptar y al rechazar; `notificar_pedido_al_alumno` redacta distinto según `pedido.estado` (DEVUELTO = aceptada).
- **Marcar pagado desde `PENDIENTE_PAGO`:** el staff puede «Marcar pagado» directo, sin comprobante (pago en mostrador).
- **`ConfiguracionTienda` del Hito 1** no tiene `acepta_transferencia`, `acepta_mercadopago` ni `minutos_reserva_mp` (con un solo medio no hay qué elegir; llegan con el Hito 2), y `Pedido.Medio` tiene sólo `TRANSFERENCIA`.
- **La portada no cambió en el Hito 1; en el Hito 3 (18-10)** dice «funciones de gestión» y lista la tienda aparte. Su frase «con cobro por transferencia» **la actualiza el Hito 2** (hecho el 18-10): `precios_tienda_mp` (`settings.TIENDA_MP_ENABLED`) agrega «o Mercado Pago» sólo cuando la función está prendida.
- **`Pedido.cancelacion_resuelta_en`** (ver «cancelacion_resuelta» arriba) saca de la cola las cancelaciones rechazadas.
