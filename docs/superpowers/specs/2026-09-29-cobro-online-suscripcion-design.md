# Cobro online de la suscripción (plataforma → gimnasio) — diseño

Fecha: 2026-09-29. Plan de implementación:
`docs/superpowers/plans/2026-09-29-cobro-online-suscripcion.md`.

Documento de diseño aprobado por el dueño del producto, en su versión revisada
adversarialmente (la tabla del final dice qué cambió en esa revisión). La sección
**«Decisiones de ejecución»** al final manda sobre cualquier párrafo anterior que la
contradiga.

## Contexto

Hoy el dueño del producto le cobra a cada gimnasio **por transferencia y a mano**: recibe la
plata, entra a `/plataforma/gimnasios/<pk>/pagos/nuevo/` y tipea una fila de
`PagoPlataforma`. Con uno o dos clientes funciona; con diez es una tarea recurrente con un
modo de falla caro: **el gimnasio que paga un domingo a la noche sigue bloqueado hasta que
alguien mire el panel.**

Se construye que el gimnasio pague **desde la app, con un click**, y que el pago se acredite
solo. El registro manual **no se elimina**: queda para el que transfiere, paga en efectivo o
arregla algo por fuera.

**No se toca el cobro del gimnasio a sus alumnos** (`pagos/`). Es la fase siguiente y se
decide después, con datos.

**Reversión de una decisión escrita.** `ROADMAP.md` lista «Mercado Pago» en *No construir
todavía* y el principio no negociable #3 dice «Nada de Mercado Pago… en el MVP». Hay que leer
el alcance con cuidado: ese principio está redactado sobre `pagos.Cuota` — *"marca mes como
pagado, guarda comprobante, muestra deudores"* — o sea el cobro **del gimnasio a su alumno**,
que acá no se toca; la app `plataforma` no existía cuando se escribió. Igual la lista genérica
sí lo menciona, así que va un bloque `> **Actualizado.**` con fecha y motivo (**precedente
exacto: el control de ingreso, 2026-09-28**) y **acotado**: Mercado Pago entra para la
suscripción de la plataforma, **no** para las cuotas de los alumnos. Sin esa aclaración, el
próximo lector cree que la puerta quedó abierta para las dos cosas.

## Decisiones tomadas (no re-litigar)

| Decisión | Elegido | Consecuencia |
|---|---|---|
| Modalidad | **Link por ciclo** (Checkout Pro), no débito automático | El monto se calcula fresco cada ciclo → no se desactualiza con la inflación. Un click por ciclo. |
| Planes | Mensual **y anual con descuento** | Supuesto: `DESCUENTO_ANUAL_MESES = 2` (paga 10). **Ver el hallazgo del escalón congelado antes de confirmar el número.** |
| Acreditación en MP | **30 días** (~1,79%+IVA vs ~6,39%) | Configuración de la cuenta, no código. |
| Prueba gratis | **Se mantiene, 30 días** | No hay estado nuevo de «aprobado sin pagar». El botón existe desde el día 1 para quien quiera adelantarse. |
| Descongelar al pagar | **Automático**, con cuatro condiciones | Ver «La regla de descongelamiento». |
| Alcance | **Solo plataforma → gimnasio** | `pagos/` no se toca en ninguna tarea. |

**Hallazgo que puede cambiar el descuento anual, y es decisión tuya:** el plan anual
**congela el escalón de precio 12 meses**. Un gimnasio de 95 alumnos que paga el anual y
llega a 250 pagó todo el año a USD 10 en vez de 15. Con `DESCUENTO_ANUAL_MESES = 2` estás
regalando dos meses **más** el riesgo de escalón. Alternativas: bajar el descuento a 1 mes,
o recalcular el escalón al cumplirse cada ciclo de 30 días dentro del año (más código y más
explicación al cliente). Queda en 2 meses hasta que digas otra cosa.

## Datos externos verificados

- **SDK**: `mercadopago` 3.6.0 en PyPI **arrastra `requests>=2.33`** + `urllib3` + `certifi`,
  y este repo rechazó `requests` explícitamente para `plataforma/cambio.py`. Hacen falta
  **cuatro** cosas: crear preferencia, consultar pago por id, **buscar pago por
  `external_reference`**, y validar HMAC (local) → **stdlib `urllib`, sin dependencia nueva.**
- **Webhook**: `x-signature: ts=<epoch>,v1=<hmac-sha256-hex>`, header `x-request-id`, query
  param `data.id`. Manifest `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`, HMAC-SHA256 con
  el secret del panel, comparado con `hmac.compare_digest`.
- **MP espera 200/201 en 22 s y reintenta cada 15 min indefinidamente.** Ese reintento es la
  cola de trabajo que este proyecto no tiene, y es **mucho mejor que un cron de GitHub**
  (que llega 2-4 h tarde en el plan free).
- Body: `{id, live_mode, type, date_created, user_id, api_version, action, data:{id}}`. **El
  body va SIN firmar**: la firma cubre el `data.id` del header, no el `action` del cuerpo.
- Preferencia: `items[{title, quantity, unit_price, currency_id}]` obligatorios;
  `external_reference`, `back_urls`, `auto_return`, `notification_url`, `expires` +
  `expiration_date_to` (ISO 8601 `YYYY-MM-DDTHH:MM:SSZ`), `binary_mode`,
  `payment_methods.excluded_payment_types`, `metadata`, `statement_descriptor`. Devuelve `id`,
  `init_point`, `sandbox_init_point`.
- **Pruebas sin plata real**: usuarios de prueba (vendedor + comprador), credenciales `TEST-…`,
  tarjetas de prueba donde el **nombre del titular** decide el resultado (`APRO` aprueba,
  `OTHE` rechaza).

## Arquitectura

**App nueva `suscripcion/`**, al final de `INSTALLED_APPS` (importa `plataforma`).

Por qué no dentro de `plataforma/`: la pantalla de pago la usa **el dueño del gimnasio**, y
toda vista de `plataforma/` lleva `SuperadminRequiredMixin` — CLAUDE.md lo declara invariante.
Por qué no en `tenants/`: está al principio del orden de dependencia y tendría que importar
`plataforma.precios`/`facturacion`.

Corolario a no olvidar: el comentario de `INSTALLED_APPS` dice que `plataforma` va *"última de
todas"* porque nadie la lee. Deja de ser cierto.

| Archivo | Qué es |
|---|---|
| `suscripcion/mercadopago.py` | **Django-free**, `urllib` + `hmac`. `crear_preferencia`, `consultar_pago`, `buscar_pagos`, `firma_valida`, `ErrorDeMercadoPago`, `_pedir` (único seam de red). |
| `suscripcion/models.py` | `IntentoDeCobro`. **No es `TenantOwnedModel`** (mismo criterio que `PagoPlataforma`/`solicitudes`) → fuera de `vaciar_gimnasio`, del fixture `_ensuciar` y de `HOJAS`/`EXCLUIDOS`. |
| `suscripcion/services.py` | **Único escritor**: `crear_intento`, `acreditar`, `reconciliar_pendientes`, `vencer_intentos_abiertos`, `integracion_activa`. |
| `suscripcion/emails.py` | Molde de `solicitudes/emails.py`: texto plano, `fail_silently=True`, `EMAIL_REPLY_TO`. |
| `suscripcion/views.py` | `MiSuscripcionView`, `IniciarPagoView`, `RetornoView`, `WebhookView`. |
| `suscripcion/urls.py` | `""`, `pagar/`, `retorno/<uuid:referencia>/`, **`v1/webhook/mercadopago/`**. |

**La versión va en la ruta del webhook** por el mismo argumento que `/ingreso/v1/`: la URL
queda cargada en el panel de MP y cambiarla no es atómico.

### `IntentoDeCobro` — el precio congelado

Campos: `gimnasio` (FK PROTECT), `plan`, `periodo_desde`, `periodo_hasta` (inclusivo),
`monto_usd`, `monto_ars`, `tipo_cambio`, `alumnos_activos`, `referencia_externa` (UUID4
unique), `preference_id`, `init_point`, `expira`, `estado`, `payment_id`, `estado_mp`,
`respuesta_mp` (JSONField recortado), `acreditado_en`, `pago` (OneToOne null PROTECT),
`email_de_quien_pago` (CharField).

Estados: `CREADO`, `PAGADO`, `RECHAZADO`, `VENCIDO`, `CANCELADO`, `DUPLICADO`, `REVISAR`,
`DEVUELTO`.

Se congela todo porque `PagoPlataforma` ya guarda los tres montos *"porque con la inflación
recalcularlos después daría un número que nunca existió"*. El intento extiende ese invariante
al momento del link: **lo que se le cobra es lo que se le mostró.**

**Sin FK a `User`**, y esto no es opcional: `tenants/demo.py::vaciar_gimnasio` borra los
`User` de la demo cada 6 h vía `restaurar-demo.yml`. Con `PROTECT` ese cron se cae con
`ProtectedError` **cada seis horas**; con `CASCADE` se borra un registro de plata. Es la
lección de `ActividadDiaria` (`usuario` CASCADE / `gimnasio` PROTECT). La auditoría va como
`email_de_quien_pago`, un CharField. El FK a `gimnasio` sí va PROTECT: `vaciar_gimnasio`
vacía el gimnasio, no lo borra.

Dos índices únicos parciales, los dos calcados de `ingreso/models.py:228-234`:
- `UniqueConstraint(gimnasio, periodo_desde, condition=Q(estado="creado"))` → **un solo
  intento abierto por período**. Es lo que hace que el segundo click **reuse el link vivo**
  en vez de crear un segundo cobro del mismo mes: el anti-doble-click deja de ser un problema
  de JS y dos pantallas no pueden mostrar dos tipos de cambio distintos del mismo cobro.
- `UniqueConstraint(payment_id, condition=~Q(payment_id=""))`.

## Las diez trampas, resueltas

1. **`cotizacion_dolar()` devuelve `None`.** No hay link. Checkout Pro exige `unit_price` en
   ARS y no hay fallback honesto: `cambio.py` cachea el éxito 24 h, así que un `None`
   significa "no hubo número en 24 horas". La pantalla dice *"Ahora mismo no podemos calcular
   el monto en pesos. Probá en unos minutos"* + `SOPORTE_CONTACTO`, y se loguea un `WARNING`
   con el slug: que un cliente no haya podido pagar es un evento que querés ver. **No** se
   persiste la última cotización buena: en Argentina una cotización vieja siempre subfactura.

2. **El link vence a las 48 h** (`HORAS_VALIDEZ_LINK = 48`), en dos lugares: `expires` +
   `expiration_date_to` en MP (lo hace cumplir del lado de ellos, aunque tu cron no corra
   nunca) y `IntentoDeCobro.expira` para que tus pantallas digan lo mismo. 48 y no 24 porque
   el aviso que llega un viernes a la noche tiene que poder pagarse el domingo; 48 y no una
   semana porque el MEP se mueve y tener dos números del mismo cobro en dos pantallas es el
   problema real, no el margen.
   **Borde que hay que escribir o alguien lo codea mal: `acreditar` acepta intentos en `CREADO`
   Y en `VENCIDO`.** La plata en la cuenta le gana a tu reloj. Un `filter(estado=CREADO)` acá
   se come un pago real.

3. **Vencer es perezoso, no depende del cron.** Con el índice único parcial, un `CREADO`
   eterno **bloquea que el gimnasio genere un link nuevo** — y el cron llega 2-4 h tarde.
   `crear_intento`, en la misma transacción y antes de insertar, marca `VENCIDO` los `CREADO`
   vencidos de ese gimnasio. El cron queda como red para el webhook perdido, que es su único
   trabajo honesto.

4. **Cruzar un escalón entre el link y el pago: se cobra el monto congelado.** El argumento no
   es justicia, es que `monto_ars` es lo que MP ya le debitó a la tarjeta. Y la verificación
   que lo hace seguro: `acreditar` compara `pago["transaction_amount"]` contra
   `intento.monto_ars` (tolerancia de un centavo) y `currency_id == "ARS"`. Si no coincide →
   `REVISAR`, mail al superadmin, **sin** crear `PagoPlataforma`. **Nunca escribir el monto que
   reporta MP: sólo usarlo para validar.** Si no, un checkout manipulado compra un mes por
   ARS 1.

5. **Cambiar de plan no deja dos links vivos**: el índice parcial lo impide (mensual y anual
   comparten `periodo_desde`). Elegir el otro plan es un POST que marca `CANCELADO` y libera
   el índice.

6. **Pago parcial no existe**, y eso es lo que hace que el MVP quepa: `binary_mode: true`
   (sin esto un pago de tarjeta puede quedar `in_process` en revisión manual de MP y el
   gimnasio ve "pagué" mientras tu base dice que no) **más** excluir `ticket` y `atm`
   (`payment_methods.excluded_payment_types`), porque un Rapipago pagado a los 3 días llega
   con el tipo de cambio de hace 3 días. Costo asumido: pierdes al que paga en efectivo — para
   un B2B de USD 10-20 a un dueño que ya usa MP, es barato. Camino de vuelta escrito: sacar
   `binary_mode`, agregar estado `PENDIENTE` y no crear el `PagoPlataforma` hasta `approved`.
   **Y el falso positivo a no comerse**: con cuotas, `total_paid_amount > transaction_amount`
   por el interés del comprador — eso **no** es un pago de más. `net_received_amount` es
   **menor** (la comisión) y usarlo como `monto_ars` haría que el KPI de ingresos baje ~2% en
   silencio contra las filas manuales. Va `transaction_amount`.

7. **El webhook llega ANTES que el navegador: es el caso normal, no el borde.** MP dispara al
   aprobarse; el retorno depende de que el comprador apriete "volver al sitio". `acreditar` es
   el **único** punto de entrada, idempotente por `select_for_update()` + cortocircuito por
   estado (molde literal: `solicitudes/services.py::_decidir`, que existe para esta carrera).
   `RetornoView` no asume que le toca trabajar: llama a `acreditar` y renderiza el estado
   resultante, que puede venir ya `PAGADO`. Y **`acreditar` no recibe `request`** — recibe
   `url_panel_para=None` (callable), como `solicitudes.verificar`: un service que se comporta
   distinto según quién lo llamó es el bug que no vas a poder reproducir.

8. **Dos pagos del mismo período NO se tragan en silencio.** El índice parcial vuelve el caso
   raro, pero sigue siendo posible (el superadmin registró el pago a mano mientras el link
   estaba vivo). Ante `IntegrityError`, el intento va a `DUPLICADO`, se guarda el `payment_id`
   y sale mail al superadmin con gimnasio, monto y período. **Copiar el `except IntegrityError:
   return None` de `solicitudes` acá sería devolver "no pasó nada" habiendo cobrado 10 USD** —
   ahí ese `return None` existe para no filtrar información, que es otro problema. Hay que
   devolver plata, y eso es una conversación.

9. **Devolución y contracargo: la clave de idempotencia es `(payment_id, estado_mp)`, no
   `payment_id`.** MP manda otra notificación de topic `payment` con el **mismo** `payment_id`
   cuando el status pasa a `refunded`/`charged_back`; un cortocircuito por "ya vi este id" la
   descarta y el gimnasio se queda con meses gratis y la cuenta descongelada. Se guarda
   `estado_mp` y un status distinto entra al camino de cambio de estado: intento `DEVUELTO` +
   mail al superadmin (diciendo si esa cuenta se descongeló por ese pago) + visible en el
   panel. **No se borra el `PagoPlataforma` ni se recongela la cuenta sola** — mismo criterio
   que "cobrarle a un gimnasio es una conversación".

10. **Gimnasio exento / demo / inactivo: `crear_intento` es estricto, `acreditar` es
    permisivo.** `crear_intento` se niega leyendo **`Gimnasio.facturacion_aplica`** (único
    lugar donde vive la regla), y se niega con un **200 que explica**, no un 403: bajo el
    `hx-boost` global un 4xx es un click muerto. **`es_demo` es el peligroso** — es la cuenta
    compartida que se le pasa a prospectos; la pantalla en demo muestra precio y plan con el
    **botón deshabilitado** y una nota, que de paso es un buen artefacto de ventas.
    `acreditar` **no** se niega por ninguna de estas: si la plata entró, se acredita y se
    manda mail. Negarse al acreditar es quedarse con plata sin registro.

## El webhook: tres respuestas, no una

Mi borrador decía "200 siempre, o MP reintenta para siempre". Está mal: tira el único
mecanismo de recuperación bueno que hay.

| Caso | Respuesta | Por qué |
|---|---|---|
| Firma inválida | **401**, sin tocar la base | Un reintento no arregla una firma mal firmada. |
| Cuerpo inválido / `data.id` desconocido / intento ya `PAGADO` | **200** | Nada que hacer. Es el cortocircuito anti-martilleo, y no toca ni MP ni una escritura. |
| Fallo transitorio nuestro (MP no contestó, Neon despertando del scale-to-zero) | **500 a propósito** | MP reintenta en 15 min. Eso es infinitamente mejor que el cron, que llega 2-4 h tarde. Con 200 acá, un gimnasio que pagó queda sin acreditar hasta mañana. |

El "reintenta para siempre" no es un riesgo: son 4 requests por hora por pago, y el
cortocircuito por estado los hace baratos.

## La regla de descongelamiento

La invariante de `tenants/models.py` («los dos escalones son MANUALES, nunca un cron») **no se
viola, se extiende**, y hay que escribirlo con estas palabras o el próximo lector la borra por
violación: **todos** los argumentos de esa invariante son sobre el peligro de **cortar** ("un
corte automático el día equivocado le rompe el día de trabajo a alguien que quizás ya
transfirió"). Restaurar tiene el perfil de riesgo inverso. La regla es **«restauración
automática, corte nunca»**.

Se descongela si y sólo si las cuatro:
1. `status == "approved"` verificado contra **`GET /v1/payments/{id}`**, nunca por el body del
   webhook (va sin firmar).
2. `transaction_amount == intento.monto_ars` y `currency_id == "ARS"`.
3. El `PagoPlataforma` se creó en esta transacción.
4. Recalculado el `Max(periodo_hasta)` del gimnasio, **`cubierto_hasta >= hoy`**.

La (4) es la que faltaba: un gimnasio con tres meses de deuda que paga **uno** no se
descongela, y el mail al superadmin **dice cuánto falta**. Es `pagos/views.py::
_avisar_si_sigue_bloqueado` un piso más arriba — el patrón del repo para esta forma exacta de
problema.

Se escribe `Gimnasio.objects.filter(pk=...).update(estado_cuenta=NORMAL,
estado_cuenta_desde=None)`: `update()` por `version_media`, y **los dos campos en el mismo
update** porque una fecha vieja sobre una cuenta normal se lee como que sigue congelada.

**Riesgo aceptado, a `ISSUES.md`:** `estado_cuenta` no tiene `motivo`, así que un gimnasio
suspendido por abuso o por una disputa se descongela con un pago. Hoy es tolerable (un solo
operador, y le llega el mail). El arreglo futuro es un campo `motivo`, no un caso especial
dentro de `acreditar`.

## `mercadopago.py` levanta, al revés de `cambio.py`

`cambio.py` traga `Exception` a propósito porque *el panel no puede caerse por una API de
terceros y no hay plata en juego*. Acá hay plata, así que las reglas son opuestas y hay que
escribirlas como tales en el docstring:

- `firma_valida()`: puro, no levanta, devuelve bool.
- `crear_preferencia` / `consultar_pago` / `buscar_pagos`: **levantan `ErrorDeMercadoPago`**.
  Quien llama decide cómo degradar.
- `_pedir()`: único punto de red (seam de test, igual que `cambio._descargar`), y **levanta
  `RuntimeError` bajo `settings.TESTING`**. No devuelve `None`: un patch olvidado en el camino
  de la plata tiene que romper el test, no degradarlo.
- `TIMEOUT = 5` (MP es más lento que dolarapi), **cero reintentos internos**: el reintento es
  MP a los 15 min o el cron.
- `RetornoView` degrada como el panel: si MP no contesta, **no muestra un error**, muestra
  *"estamos confirmando tu pago, se acredita en un minuto"*. Un lookup fallido no puede
  parecerse a un pago fallido.
- `init_point` vs `sandbox_init_point` se elige por `ACCESS_TOKEN.startswith("TEST-")`, en el
  módulo, en dos líneas. Si no, el riesgo concreto es desplegarle un link de sandbox a un
  cliente real.

**Cuatro detalles del HMAC que, mal, hacen fallar el 100% de las notificaciones:**
los segmentos ausentes se **omiten**, no van vacíos (sin `x-request-id` el manifest es
`id:<id>;ts:<ts>;`); un `data.id` alfanumérico va **en minúsculas**; **el `ts` no se valida
como ventana de frescura** (un replay no puede cobrar dos veces porque la idempotencia es la
defensa real, y un reloj más es un reloj que puede desincronizarse y rechazar un reintento
legítimo de MP a las 3 AM) — se loguea, no se rechaza; y `json.dumps` **levanta `TypeError`
con un `Decimal`**, así que `unit_price` va `float(monto_ars)` (ya viene cuantizado a 0,01).

## Invariantes que no se pueden romper

- **`Gimnasio.modificado` no se toca nunca.** `version_media` deriva de él y versiona logo,
  fondo e ícono PWA (medido: 251 KB por click). Test clon de `test_no_toca_modificado`.
- **`facturacion.periodo_a_cobrar` exige un gimnasio anotado.** Usa
  `getattr(gimnasio, "cubierto_hasta", None)`: con una instancia pelada devuelve `None` **en
  silencio** y cobra siempre el primer período post-prueba. Y la forma natural de escribir la
  vista es `request.user.perfil.gimnasio` — que es lo que hace `GimnasioUpdateView` — o sea
  una instancia pelada. Un comentario no alcanza: van **`facturacion.exigir_anotado(gimnasio)`**
  (levanta `ValueError`) y **`facturacion.gimnasio_anotado(pk)`** como único acceso desde
  `suscripcion`. `periodo_a_cobrar` y `fila_de_gimnasio` no se tocan (su `getattr` está
  documentado para los tests con `Gimnasio` pelado).
- **`periodo_hasta` es INCLUSIVO** en `PagoPlataforma` y en `precios.periodo_siguiente`.
- **`try/except IntegrityError` SIEMPRE dentro de `transaction.atomic()`** — sin el atomic la
  transacción queda marcada para rollback y los `messages` (que viven en la base) revientan
  con `TransactionManagementError`.
- **`gimnasios_anotados()` sigue siendo UNA query.** No agregar "tiene link abierto" al
  monitor: la lista de cobros con problema es una query aparte y barata. Si alguna vez entra,
  entra como `Subquery`.
- **El form que manda a Mercado Pago lleva `hx-boost="false"`.** Causa raíz recurrente #1 del
  proyecto (12 apariciones): el redirect a `mercadopago.com.ar` es cross-origin, htmx lo hace
  por XHR y no puede seguirlo, así que el click queda tragado sin error visible. Es el caso de
  «Conectar Google Calendar». Lo mismo para el botón de `cuenta_bloqueada.html`.
- **Cada vista de `suscripcion` declara su público** con exactamente uno de los dos mixins
  (staff del gimnasio / superadmin), con un test por introspección
  `test_toda_vista_declara_su_publico` — es el sustituto de la invariante "toda vista de
  `plataforma` es superadmin" para una app que tiene dos públicos.
- **Ningún secreto en un log ni en `respuesta_mp`.** Precedente:
  `ReservaCalendarEvent.last_error` ("solo código + mensaje corto: NUNCA tokens").

## El gimnasio suspendido tiene que poder pagar

`ESTADOS_SIN_ACCESO_STAFF = {SUSPENDIDA}`: el middleware le renderiza `cuenta_bloqueada.html`
en toda ruta fuera de la allowlist. Sin arreglar esto, **la feature entera es inalcanzable
justo para el cliente que más la necesita**. Y un link en la nav no salva nada:
`base.html:243` no renderiza `nav-staff` cuando `cuenta_suspendida`.

**`APPS_SIN_BLOQUEO` es el arreglo equivocado** (era mi propuesta, y la descarto): ese
frozenset se evalúa **antes de tocar `request.user`**, orden que existe para rutas cacheables
(`logo_gimnasio` con `immutable`) y rutas que pide la máquina. Usarlo pierde
`registrar_dia_activo`, y CLAUDE.md dice textual que el registro corre antes del bloqueo *a
propósito* porque *"que alguien intente entrar y se encuentre el cartel también es información
— es la señal de que el gimnasio sigue vivo del otro lado de la deuda"*. Perderla en la única
página que ese gimnasio va a abrir es perderla donde más valía.

Va un **tercer nivel**, chequeado *después* de `request.user` y de `registrar_dia_activo` y
antes de `sin_acceso`: `APPS_SIN_BLOQUEO_CON_REGISTRO = frozenset({"suscripcion"})`. Seis
líneas. El gimnasio suspendido paga **y** la visita cuenta como "sigue vivo".

**El webhook no necesita ninguna allowlist**: ignora `request.user`, así que el middleware
corta en anónimo — es el caso de `/ingreso/v1/eventos/`, que CLAUDE.md documenta como "no está
en `APPS_SIN_BLOQUEO` y aun así pasa". Escribir en el docstring qué ruta lo necesita y cuál no,
o el próximo lector cree que el webhook dependía de eso.

**Los dos puntos de entrada que ya existen y cuestan cero queries** (el context processor ya
resolvió el estado): `cuenta_bloqueada.html` rama staff (hoy dice «Escribinos para
reactivarla») y el `banner-atraso` de `base.html:161-168` (hoy dice «Escribinos para
regularizarla»). Ahí van el botón y el link.

## Lo que hay que hacer a mano

1. Cuenta de MP **del dueño del producto**. Esta integración usa **una sola** credencial, la de
   la plataforma: no hay OAuth por tenant ni split. **Escribirlo en el docstring de
   `mercadopago.py`**, porque el día que `pagos` (gimnasio→alumno) tenga MP necesita
   credenciales **por gimnasio** y cifradas con `EncryptedTextField`, y alguien va a querer
   reusar este módulo.
2. En «Tus integraciones», una aplicación con producto **Checkout Pro**. De ahí salen el Access
   Token de producción y el de prueba (`TEST-…`).
3. **Acreditación a 30 días** en «Costos de recibir pagos». Cero código, y antes del primer
   cobro real.
4. **Webhooks**: URL `https://www.tugimapp.com/suscripcion/v1/webhook/mercadopago/` — **con la
   barra final**: `APPEND_SLASH` queda en su default `True` y el redirect de `CommonMiddleware`
   pierde el cuerpo de un POST. Topic: sólo **Pagos**. Al guardar, MP muestra la **clave
   secreta una sola vez**: copiala. Configurar también la URL de prueba.
5. `MERCADOPAGO_ACCESS_TOKEN` y `MERCADOPAGO_WEBHOOK_SECRET` (las dos o ninguna) en: `.env`
   local (las **de prueba**), dashboard de Render (las **de producción**), `render.yaml` como
   `sync: false`, `.env.example`, y secrets de GitHub para el workflow de la Tarea 7.
6. Secret de Healthchecks para el cron nuevo.
7. **Confirmar el descuento anual** a la luz del hallazgo del escalón congelado.
8. Fuera de alcance pero real: **facturación AFIP/ARCA** de lo que cobrás. MP no emite tus
   facturas.

## Fuera de alcance (explícito)

- El cobro del gimnasio a sus alumnos. **Nada de `pagos/` se toca.**
- Débito automático / suscripciones de MP (`preapproval`).
- Recongelar la cuenta sola ante un contracargo.
- Facturación electrónica.
- Otras pasarelas (Payway, MODO).

## Qué cambió en la revisión adversarial

| Antes | Ahora | Por qué |
|---|---|---|
| Webhook: 200 siempre | 401 / 200 / **500** según el caso | El 500 aprovecha el reintento de MP a los 15 min, que es mejor que el cron (2-4 h). |
| Idempotencia por `payment_id` | por **`(payment_id, estado_mp)`** | Un reembolso llega con el mismo id y otro status: el cortocircuito lo tragaba para siempre. |
| `mercadopago.py` como `cambio.py` (nunca levanta) | **levanta**, y `_pedir` revienta bajo `TESTING` | Un patch olvidado en el camino de la plata tiene que romper el test, no degradarlo. |
| 3 endpoints de MP | **4** (falta `payments/search`) | Sin él, webhook perdido + pestaña cerrada = plata sin registro y sin forma de encontrarla. |
| Doble link: avisar y devolver a mano | **índice único parcial que reusa el link vivo** | Elimina la clase de problema en vez de manejarla. |
| `DIAS_VALIDEZ_LINK = 3` | `HORAS_VALIDEZ_LINK = 48`, y `acreditar` acepta `VENCIDO` | La plata en la cuenta le gana a tu reloj. |
| `EventoDeCobro` en el MVP | `estado_mp` + `respuesta_mp` en el intento; la tabla en la Tarea 9 | Un modelo menos que mantener en los tres lugares de la regla tenant-owned. |
| Vencer por cron | **vencimiento perezoso** en `crear_intento` | Con el índice parcial, un `CREADO` eterno bloquea el link nuevo, y el cron llega 2-4 h tarde. |
| `"suscripcion"` en `APPS_SIN_BLOQUEO` | **tercer nivel** `..._CON_REGISTRO` | Esa lista corre antes de `request.user` y perdía la señal de "el gimnasio sigue vivo". |
| Descongelar "si cubre el período" | **cuatro condiciones**, con `cubierto_hasta >= hoy` | Tres meses de deuda pagando uno no se descongela. |
| `MP_*` + public key | `MERCADOPAGO_*`, **sin** public key | Checkout Pro por redirección no la usa. |
| «pago parcial» como caso a modelar | **no existe**: `binary_mode` + excluir `ticket`/`atm` | Es lo que hace que el MVP quepa. |
| FK a `User` en el intento (implícito) | `email_de_quien_pago` CharField | Con PROTECT, `restaurar-demo` se cae cada 6 h; con CASCADE se borra plata. |
| `DIAS_ANIO` sin definir | **360** (12 × 30), no 365 | Con 365 los períodos mensual y anual dejan de encajar en la grilla, para siempre. |

## Decisiones de ejecución (2026-09-29)

Tomadas al convertir el diseño en tareas. Mandan sobre el resto del documento.

1. **Sin `EventoDeCobro`.** La bitácora de eventos de un pago ya existe en el panel de
   Mercado Pago, y el intento guarda `estado_mp`, `respuesta_mp` (la última respuesta,
   recortada) y `acreditado_en`. Un modelo menos. *Costo si está mal:* agregarlo
   después, sin migración de datos.
2. **La devolución y el contracargo se manejan en el MVP, no en una tarea aparte.**
   Dejarlos para después dejaba un agujero conocido en el camino de la plata: un pago
   reembolsado seguiría figurando como cobrado. `acreditar` pasa el intento a `DEVUELTO`
   y le manda mail al superadmin; **no** borra el `PagoPlataforma` ni recongela la
   cuenta.
3. **El tercer nivel de la allowlist (`APPS_SIN_BLOQUEO_CON_REGISTRO`) sólo aplica al
   staff.** Aplicado a cualquiera, el alumno de un gimnasio bloqueado que tipea
   `/suscripcion/` recibiría el 403 de `StaffRequiredMixin` en vez del cartel.
4. **Doble cobro por solapamiento, no sólo por igualdad.** Antes de crear el
   `PagoPlataforma`, `acreditar` busca cualquier pago del gimnasio cuyo rango se
   **solape** con el del intento, no sólo el que arranca el mismo día (lo único que ve
   la `UniqueConstraint`): el superadmin pudo haber cargado a mano un período con otra
   fecha de inicio. Solapa → `DUPLICADO` + mail. La `UniqueConstraint` queda como red
   para la carrera.
5. **`COBROS_AVISO_EMAIL`**, variable suelta y opcional (mismo criterio que
   `SOLICITUDES_AVISO_EMAIL`), para los mails al superadmin; vacía cae a
   `DEFAULT_FROM_EMAIL`.
6. **Los avisos de vencimiento al gimnasio deduplican con un modelo propio,
   `AvisoDeCobro`**, no con `notificaciones.RecordatorioEnviado`: ése es
   `TenantOwnedModel` (dato del gimnasio) y esto es un dato de la plataforma sobre el
   gimnasio — la misma frontera que separa a `PagoPlataforma`.
7. **Ejecución secuencial, no en tandas paralelas.** El proceso usado prohíbe
   implementadores en paralelo sobre el mismo árbol, y abrir un worktree por agente para
   ahorrar minutos en dos tareas chicas agrega merges y copiar el `.env` no versionado
   (sin él la suite no es hermética). *Costo si está mal:* tiempo de reloj, nada de
   corrección.
8. **Las tareas del camino de la plata también se delegan**, al modelo más capaz y con
   la lista de doce puntos de revisión en el brief del revisor. El plan decía que las
   hacía el coordinador; su contexto ya es largo y el proceso exige una revisión
   independiente de cada tarea. *Costo si está mal:* una ronda de corrección.
9. **La «Puerta A» se verifica sobre una copia de la base local y con tests, no sobre una
   copia de producción**, que no es accesible desde acá. La migración es aditiva (un
   campo con default y una columna que pasa a nullable); Render la corre en el
   `buildCommand`.
