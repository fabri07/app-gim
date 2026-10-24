# Prueba de Mercado Pago de la tienda y de las cuotas (guía paso a paso)

Para qué sirve: probar con cuentas de prueba (plata de mentira) que el cobro por Mercado Pago anda —las compras de la tienda y el pago de la cuota del alumno—, **antes** de prenderlo para un gimnasio real. Las dos cosas cuelgan de las mismas 4 variables, así que la prueba se hace una sola vez.

Estado de hoy: el código está hecho y **apagado**. Se prende solo cuando las 4 variables de abajo están cargadas en Render. Mientras no estén, la tienda cobra sólo por transferencia y las cuotas se cobran como siempre (el alumno sube el comprobante y el staff confirma).

Cómo funciona, en una frase: cada gimnasio conecta su propia cuenta de Mercado Pago (botón «Conectar Mercado Pago») y la plata va directo a esa cuenta, la de las ventas de la tienda y la de las cuotas.

> **La plata nunca pasa por la cuenta de la plataforma.** La cuenta de Mercado Pago de la plataforma sólo cobra la suscripción de cada gimnasio a la app. Lo que paga un alumno (un producto o su cuota) va siempre a la cuenta del gimnasio que la conectó.

**Ojo:** los nombres de menús de Mercado Pago Developers, los interruptores de OAuth/PKCE/offline_access y los datos de las tarjetas de prueba son los publicados al escribir esta guía y pueden llamarse distinto o haber cambiado. Si no coinciden con lo que ves, guiate por la documentación de MP Developers.

Tené a mano: tu cuenta de Mercado Pago Developers (la de la app que ya cobra las suscripciones), la cuenta de Render, la de GitHub y una terminal en la carpeta del repo.

---

## Parte 1. Crear los usuarios de prueba

Necesitás dos: un **vendedor** (el gimnasio) y un **comprador** (el alumno).

1. Entrá a https://www.mercadopago.com.ar/developers y logueate.
2. Andá a **Tus integraciones** y abrí la aplicación de la plataforma (la misma del cobro de suscripciones).
3. En el menú de la izquierda buscá **Cuentas de prueba** y tocá **Crear cuenta de prueba**.
4. Creá la primera: elegí **Vendedor**, país Argentina, y poné algún monto ficticio. Guardá el usuario y la contraseña que te muestra.
5. Creá la segunda: elegí **Comprador**, país Argentina. Guardá usuario y contraseña. Ponele saldo si te deja elegirlo.
6. Anotá los dos en un lugar seguro. Se loguean en https://www.mercadopago.com.ar con ese usuario y contraseña. Usá una ventana de incógnito para no mezclarlas con tu cuenta real.

## Parte 2. Configurar la aplicación de Mercado Pago de la plataforma

Dentro de la misma aplicación, en **Tus integraciones**:

1. Buscá la sección de **OAuth** (puede llamarse «Autorización» o «Credenciales OAuth»; si no la ves, mirá en **Editar** de la aplicación).
2. Si aparece la opción, activá **OAuth**.
3. Si aparece la opción, activá **PKCE** (el código de seguridad que usa la app al conectar).
4. Si aparece la opción, activá el permiso **offline_access** (hace que la conexión dure meses sin que el dueño del gimnasio tenga que volver a autorizar). Si no aparece esa opción, anotalo en la checklist del final: sin eso, al conectar la app muestra «Mercado Pago no otorgó acceso sin conexión».
5. En **Redirect URI** cargá exactamente esta dirección, sin espacios y con la barra del final:

   `https://www.tugimapp.com/tienda/mercadopago/callback/`

   Tiene que ser HTTPS y estar escrita idéntica a la que cargues después en Render. Una letra distinta y Mercado Pago rechaza la conexión.
6. Guardá los cambios.
7. Anotá el **Client ID** y el **Client Secret** de la aplicación (en **Credenciales de producción**). Los vas a pegar en Render.

## Parte 3. Generar la clave de cifrado

La app cifra los tokens de Mercado Pago de cada gimnasio con una clave propia. Es **distinta** de la de Google Calendar: no reuses esa.

1. En la terminal, dentro de la carpeta del repo, activá el entorno con `source .venv/bin/activate` y pegá este comando:

   ```
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Copiá el texto largo que imprime. Es el valor de `MP_TOKEN_ENCRYPTION_KEY`.
3. Guardalo en tu gestor de contraseñas. **Si lo perdés o lo cambiás, todos los gimnasios conectados pasan a «reconectar»** y tienen que volver a tocar el botón.

## Parte 4. Cargar las 4 variables en Render

1. Entrá a https://dashboard.render.com y abrí el servicio de la app.
2. Andá a **Environment**.
3. Agregá estas cuatro variables. Las cuatro juntas: con una, dos o tres sueltas la app no arranca, a propósito.

| Variable | Valor |
|---|---|
| `MERCADOPAGO_CLIENT_ID` | el Client ID de la Parte 2 |
| `MERCADOPAGO_CLIENT_SECRET` | el Client Secret de la Parte 2 |
| `MERCADOPAGO_OAUTH_REDIRECT_URI` | `https://www.tugimapp.com/tienda/mercadopago/callback/` |
| `MP_TOKEN_ENCRYPTION_KEY` | la clave de la Parte 3 |

4. Guardá. Render redeploya solo. Esperá a que termine.

## Parte 5. Cargar los mismos 4 secrets en GitHub

Los trabajos programados (renovar las conexiones y recuperar pagos que se perdieron) corren en GitHub y necesitan los **mismos valores**.

1. Entrá al repo en GitHub: **Settings**, **Secrets and variables**, **Actions**.
2. Tocá **New repository secret** y cargá estos cuatro, con el mismo nombre y el mismo valor que en Render:
   - `MERCADOPAGO_CLIENT_ID`
   - `MERCADOPAGO_CLIENT_SECRET`
   - `MERCADOPAGO_OAUTH_REDIRECT_URI`
   - `MP_TOKEN_ENCRYPTION_KEY`
3. Revisá que ya existan `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` y `VAPID_ADMIN_EMAIL` (con ellos el workflow manda los avisos al staff). Si faltan, cargalos con los valores de Render.
4. Si cargás las variables en Render y **no** en GitHub, los pasos de Mercado Pago del workflow `reconciliar-cobros.yml` se ponen en rojo en cuanto haya una cuenta conectada. Es a propósito: es el aviso de que se olvidó un secret.
5. Ese workflow tiene ahora un paso más, «Reconciliar cuotas cobradas por Mercado Pago» (recupera los pagos de cuotas cuyo aviso se perdió). Usa los mismos secrets: no hay que cargar nada nuevo.

## Parte 6. Conectar al vendedor de prueba

Usá un gimnasio de **prueba** (por ejemplo `gimnasio-verificacion-r2`), nunca el de un cliente ni la cuenta demo. Para probar la tienda (Partes 7 y 8) tiene que tener la tienda habilitada desde el panel de plataforma; para las cuotas (Parte 9) no hace falta.

Hay dos lugares para conectar, y conectan **la misma cuenta**: la configuración de la tienda (pasos de abajo) o **Mi gimnasio**, tarjeta **Mercado Pago**, botón **Conectar Mercado Pago**. Desde Mi gimnasio se puede conectar aunque el gimnasio no tenga la tienda, y al terminar se vuelve a Mi gimnasio en vez de a la tienda.

1. Entrá a la app como staff de ese gimnasio.
2. Menú **Tienda**, **Configuración**.
3. Ahora aparece la tarjeta **Mercado Pago**. Tocá **Conectar Mercado Pago**.
4. Se abre Mercado Pago. Logueate con el **usuario vendedor de prueba** (Parte 1), no con tu cuenta real.
5. Autorizá a la aplicación.
6. Volvés a la configuración de la tienda con el cartel «Mercado Pago quedó conectado». La tarjeta tiene que decir **Conectada · cuenta de prueba**, con quién la conectó y cuándo.
7. Si aparece un error, anotá el texto exacto en la checklist y mirá el log de Render: ahí queda el motivo, sin datos sensibles.
8. En la misma pantalla, tildá **aceptar Mercado Pago** (podés dejar también la transferencia) y guardá.

## Parte 7. Comprar con el comprador de prueba

1. Abrí una ventana de incógnito y entrá como **alumno** de ese gimnasio (cualquiera de prueba).
2. Menú **Tienda**: agregá al carrito un producto con stock.
3. En el carrito aparecen dos opciones: **Transferencia** y **Mercado Pago**. Elegí **Mercado Pago** y confirmá.
4. La app te manda a Mercado Pago. Logueate con el **usuario comprador de prueba** (Parte 1).
5. Pagá con una **tarjeta de prueba**. Mercado Pago las publica en su documentación (sección «Tarjetas de prueba»); para que el pago salga aprobado, el nombre del titular es `APRO`, según su documentación pública.
6. Al terminar, tocá el botón para volver al sitio. Tendrías que aterrizar en «Mi pedido».

## Parte 8. Qué mirar para saber que anduvo

Del lado del alumno, en «Mi pedido»:
- El estado dice **Pagado, para retirar**. Puede tardar unos segundos; recargá.
- Ya no aparece el botón «Pagar con Mercado Pago».

Del lado del staff:
- En **Tienda**, **Pedidos**, el pedido está en **Pagado, para retirar** y muestra «Pago de Mercado Pago: (número)».
- Llegó un aviso al celular del staff: «Pago con Mercado Pago».
- El stock del producto bajó.
- En la cuenta del **vendedor de prueba** en Mercado Pago, la venta figura como cobrada.
- En **Tienda**, **Pedidos**, la pestaña **Revisar MP** está vacía (si tiene algo, algo salió distinto de lo esperado: anotá qué dice).

Si algo no aparece, esperá unos minutos y recargá. Puede ser uno de los puntos de la checklist de abajo y no un error: anotá qué viste.

Para probar también el camino «no pagué»: creá otro pedido con Mercado Pago y no pagues. Vence solo a los 30 minutos (valor por defecto de «minutos para pagar con Mercado Pago» en la configuración; se puede cambiar entre 20 y 120) y el stock vuelve.

## Parte 9. Pagar una cuota

En el mismo gimnasio de prueba (`gimnasio-verificacion-r2`), con la cuenta ya conectada (Parte 6).

1. Como staff: **Mi gimnasio**, sección **Cobros**. Cargá un **Precio de la cuota** chico (es plata de mentira, pero que se lea fácil). Guardá. Si había cuotas impagas en $0, la app avisa cuántas completó con ese precio.
2. En la misma sección, tildá **Cobrar cuotas con Mercado Pago** y guardá. Si dice «Primero conectá tu cuenta de Mercado Pago», volvé a la Parte 6.
3. Elegí un alumno de prueba que tenga una cuota impaga con monto (en **Pagos**, la cuota tiene que mostrar el monto, no $0). Si está en $0, usá **Corregir monto** en el listado de cuotas.
4. En una ventana de incógnito, entrá como **ese alumno**. En el inicio, en la cuota impaga, aparece el botón **Pagar $ X con Mercado Pago**. Tocalo.
5. La app te manda a Mercado Pago. Logueate con el **usuario comprador de prueba** (Parte 1) y pagá con una tarjeta de prueba con titular `APRO` (igual que en la Parte 7).
6. Al terminar, tocá el botón para volver al sitio. Tendrías que aterrizar en el inicio con «¡Listo, tu cuota quedó paga!» (o «Estamos confirmando tu pago»: recargá en unos segundos).

Qué mirar:
- Del lado del alumno: la cuota ya no aparece como impaga y el botón desapareció. Si el alumno estaba bloqueado por esa cuota, vuelve a ver su rutina.
- Del lado del staff, en **Pagos**: la cuota está **Pagada**, con medio «Mercado Pago» y el distintivo **Mercado Pago** al lado.
- Llegó un aviso al celular del staff: «Cuota pagada con Mercado Pago». Y al del alumno: «Recibimos el pago de tu cuota».
- En **Pagos**, botón **Pagos con Mercado Pago**: el pago figura en **Todos** y la pestaña **Revisar** está en 0 (si tiene algo, anotá qué dice la explicación).
- En la cuenta del **vendedor de prueba** en Mercado Pago, el cobro figura. Anotá qué nombre aparece como descripción del cobro: la app no manda «TUGIMAPP» para las cuotas, así que tendría que salir el de la cuenta del gimnasio.

Para probar el bloqueo: en un gimnasio de prueba con tolerancia de pago cargada, usá un alumno bloqueado por una cuota vencida. El botón sale también en la pantalla «Tu acceso a la rutina está pausado»; después de pagar, el alumno tiene que volver a ver la rutina sin que nadie toque nada.

---

## Checklist de lo que todavía NO está confirmado

Son cosas que dependen de cómo se comporta Mercado Pago de verdad y que el código asume. Anotá al lado de cada una qué pasó. Para ver datos crudos usá el log de Render y el panel de Webhooks de la aplicación en Mercado Pago Developers.

- [ ] **Prefijo y `live_mode` del token de prueba.** Al conectar el vendedor de prueba, ¿la tarjeta dice «cuenta de prueba»? Anotar: ¿el token empieza con `TEST-` o con `APP_USR-`? ¿`live_mode` vino en falso? (La app marca «de prueba» si se cumple cualquiera de las dos.)
- [x] **`init_point` vs `sandbox_init_point`.** Resuelto el 2026-10-24: el vendedor de prueba conectado por OAuth trae token `APP_USR-` y el link de sandbox sólo anda con `TEST-`; el pago del alumno fallaba siempre en la pantalla de MP, sin crearse ni llegar a la app (por eso Sentry no mostraba nada). Ahora el link sale del prefijo del token: con `APP_USR-` se usa el link normal y MP simula el pago entre usuarios de prueba. Si con el cambio desplegado sigue fallando, anotar el texto exacto que muestra MP.
- [ ] **Si llega el `refresh_token`.** Al conectar, ¿conectó o mostró «Mercado Pago no otorgó acceso sin conexión»? Si falla, `offline_access` no quedó activo en la aplicación (Parte 2, paso 4).
- [ ] **Si refrescar invalida el token viejo.** Corré el refresco (workflow `reconciliar-cobros.yml`, paso «Refrescar cuentas de Mercado Pago de la tienda», o `python manage.py refrescar_cuentas_mp`; sólo toca cuentas a menos de 30 días de vencer) y después hacé una compra nueva: ¿la cuenta sigue «Conectada» y el pago se acredita? Anotar si el token viejo siguió andando unos minutos o murió de golpe.
- [ ] **Si la `notification_url` llega firmada.** En el log del webhook de la tienda (`/tienda/v1/webhook/mercadopago/<uuid>/`), ¿la notificación trae el encabezado `x-signature`? Anotar sí o no. Hoy la app no valida firma: la autenticidad sale de leer el pago desde la API de Mercado Pago con el token del gimnasio. Si llega firmada, se agrega la validación obligatoria en un cambio aparte.
- [ ] **Si esos pagos también llegan al webhook de la suscripción.** Mirá el log del webhook del cobro de suscripciones después de la compra de prueba: ¿recibió una notificación de ese pago? Si sí, tiene que haber respondido 200 con un `logger.error`, sin que Mercado Pago reintente.
- [ ] **Pago de OTRA cuenta en el webhook de una tienda: ¿404/403 o 401?** Si diera 401, las notificaciones viejas de una cuenta que se cambió desconectarían la cuenta nueva. Anotar el código que se ve en el log.
- [ ] **`test_token` en el canje.** El canje del código OAuth con el vendedor de prueba, ¿anduvo tal cual está o Mercado Pago pidió un parámetro `test_token`? Anotar el error si lo hubo.
- [ ] **Vuelta del navegador.** Al volver de pagar, ¿se llega a «Mi pedido» con el pedido ya en «Pagado»? Si cae en el login o dice «Estamos confirmando tu pago», anotar si el pedido terminó pagado igual por el aviso de Mercado Pago (tiene que pasar).
- [ ] **Devolución.** Devolvé el pago desde la cuenta del vendedor de prueba: ¿el pedido pasa a **Devuelto**, vuelve el stock y llega el aviso? (La app no devuelve plata sola: la devolución se hace siempre a mano desde Mercado Pago.)
- [ ] **Aviso de un pago de cuota.** Después de la Parte 9, ¿el aviso de Mercado Pago llegó al mismo webhook de la tienda y acreditó la cuota? (En el log de Render, la notificación al webhook de la tienda con respuesta 200.) Si la cuota quedó paga sólo al volver del navegador o recién con la reconciliación, anotalo.
- [ ] **Vuelta de una cuota.** Al volver de pagar la cuota, ¿se llega al inicio con «¡Listo, tu cuota quedó paga!»? Si cae en el login o dice «Estamos confirmando tu pago», anotar si la cuota terminó paga igual.
- [ ] **Devolución de una cuota.** Devolvé desde la cuenta del vendedor de prueba el pago de la cuota de la Parte 9: ¿llega el aviso «Pago de cuota devuelto» y el pago aparece en **Pagos con Mercado Pago**, pestaña **Revisar**? La cuota sigue Pagada a propósito (la app no la vuelve impaga sola): tocá **Volver a impaga** y fijate que la cuota vuelva a pendiente (o vencida) y que la pestaña Revisar quede en 0.

## Cómo se apaga si algo sale mal

1. En Render, **Environment**: borrá las 4 variables (`MERCADOPAGO_CLIENT_ID`, `MERCADOPAGO_CLIENT_SECRET`, `MERCADOPAGO_OAUTH_REDIRECT_URI`, `MP_TOKEN_ENCRYPTION_KEY`). Render redeploya.
2. Resultado: desaparece la tarjeta de Mercado Pago de la configuración de la tienda y de Mi gimnasio, el interruptor «Cobrar cuotas con Mercado Pago» y el botón «Pagar con Mercado Pago» de las cuotas; el carrito ofrece sólo transferencia y el webhook responde 503 (Mercado Pago reintenta un tiempo). Las cuotas se cobran como siempre: el alumno sube el comprobante y el staff confirma. La pantalla «Pagos con Mercado Pago» sigue visible si ya hubo pagos (plata cobrada no se esconde).
3. Los pedidos de Mercado Pago que ya estaban pagados siguen como estaban. Un pedido sin pagar vence solo y el stock vuelve. Las cuotas pagadas por Mercado Pago siguen pagadas; un link de cuota abierto vence solo a las 2 horas.
4. **Apagar de verdad = borrar las 4 variables en Render Y los 4 secrets en GitHub.** Si sólo las borrás en Render, los 4 secrets de GitHub dejan la bandera del workflow PRENDIDA: el cron sigue refrescando tokens y acreditando pagos como si nada. Si sólo borrás los secrets de GitHub y hay una cuenta conectada, los pasos de Mercado Pago del workflow salen en rojo a propósito (tokens que ya no se renuevan). Si no hay cuentas conectadas, salen en verde «apagado». Para evitar el rojo, desconectá la cuenta (desde la configuración de la tienda o desde Mi gimnasio) antes de apagar.
5. Para volver a prender: cargá otra vez las 4 variables en Render y los 4 secrets en GitHub, **con los mismos valores** (misma clave de cifrado; con otra, las cuentas quedan en «reconectar»). Si sólo estuvieron fuera de Render, no se perdió nada: el cron siguió acreditando. Si estuvo todo apagado, no se acreditó ningún pago por Mercado Pago: al prender, la reconciliación recupera los de los últimos 7 días.
