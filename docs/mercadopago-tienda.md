# Prueba de Mercado Pago de la tienda (guía paso a paso)

Para qué sirve: probar con cuentas de prueba (plata de mentira) que el cobro de la tienda por Mercado Pago anda, **antes** de prenderlo para un gimnasio real.

Estado de hoy: el código está hecho y **apagado**. Se prende solo cuando las 4 variables de abajo están cargadas en Render. Mientras no estén, la tienda cobra sólo por transferencia, igual que siempre.

Cómo funciona, en una frase: cada gimnasio conecta su propia cuenta de Mercado Pago (botón «Conectar Mercado Pago») y la plata va directo a esa cuenta. La plataforma nunca la toca.

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
2. Activá **OAuth**.
3. Activá **PKCE** (el código de seguridad que usa la app al conectar).
4. Activá el permiso **offline_access** (hace que la conexión dure meses sin que el dueño del gimnasio tenga que volver a autorizar). Si no aparece esa opción, anotalo en la checklist del final: sin eso, al conectar la app muestra «Mercado Pago no otorgó acceso sin conexión».
5. En **Redirect URI** cargá exactamente esta dirección, sin espacios y con la barra del final:

   `https://www.tugimapp.com/tienda/mercadopago/callback/`

   Tiene que ser HTTPS y estar escrita idéntica a la que cargues después en Render. Una letra distinta y Mercado Pago rechaza la conexión.
6. Guardá los cambios.
7. Anotá el **Client ID** y el **Client Secret** de la aplicación (en **Credenciales de producción**). Los vas a pegar en Render.

## Parte 3. Generar la clave de cifrado

La app cifra los tokens de Mercado Pago de cada gimnasio con una clave propia. Es **distinta** de la de Google Calendar: no reuses esa.

1. En la terminal, pegá este comando:

   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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

## Parte 6. Conectar al vendedor de prueba

Usá un gimnasio de **prueba** (por ejemplo `gimnasio-verificacion-r2`), nunca el de un cliente ni la cuenta demo. Tiene que tener la tienda habilitada desde el panel de plataforma.

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
5. Pagá con una **tarjeta de prueba**. Mercado Pago las publica en su documentación (sección «Tarjetas de prueba»); para que el pago salga aprobado, el nombre del titular es `APRO`.
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

---

## Checklist de lo que todavía NO está confirmado

Son cosas que dependen de cómo se comporta Mercado Pago de verdad y que el código asume. Anotá al lado de cada una qué pasó. Para ver datos crudos usá el log de Render y el panel de Webhooks de la aplicación en Mercado Pago Developers.

- [ ] **Prefijo y `live_mode` del token de prueba.** Al conectar el vendedor de prueba, ¿la tarjeta dice «cuenta de prueba»? Anotar: ¿el token empieza con `TEST-` o con `APP_USR-`? ¿`live_mode` vino en falso? (La app marca «de prueba» si se cumple cualquiera de las dos.)
- [ ] **`init_point` vs `sandbox_init_point`.** ¿El botón «Pagar con Mercado Pago» abre una pantalla de pago que acepta al comprador de prueba? Anotar si con cuentas de prueba anduvo el link normal o hizo falta el de sandbox, y si dio error.
- [ ] **Si llega el `refresh_token`.** Al conectar, ¿conectó o mostró «Mercado Pago no otorgó acceso sin conexión»? Si falla, `offline_access` no quedó activo en la aplicación (Parte 2, paso 4).
- [ ] **Si refrescar invalida el token viejo.** Corré el refresco (workflow `reconciliar-cobros.yml`, paso «Refrescar cuentas de Mercado Pago de la tienda», o `python manage.py refrescar_cuentas_mp`; sólo toca cuentas a menos de 30 días de vencer) y después hacé una compra nueva: ¿la cuenta sigue «Conectada» y el pago se acredita? Anotar si el token viejo siguió andando unos minutos o murió de golpe.
- [ ] **Si la `notification_url` llega firmada.** En el log del webhook de la tienda (`/tienda/v1/webhook/mercadopago/<uuid>/`), ¿la notificación trae el encabezado `x-signature`? Anotar sí o no. Hoy la app no valida firma: la autenticidad sale de leer el pago desde la API de Mercado Pago con el token del gimnasio. Si llega firmada, se agrega la validación obligatoria en un cambio aparte.
- [ ] **Si esos pagos también llegan al webhook de la suscripción.** Mirá el log del webhook del cobro de suscripciones después de la compra de prueba: ¿recibió una notificación de ese pago? Si sí, tiene que haber respondido 200 con un `logger.error`, sin que Mercado Pago reintente.
- [ ] **Pago de OTRA cuenta en el webhook de una tienda: ¿404/403 o 401?** Si diera 401, las notificaciones viejas de una cuenta que se cambió desconectarían la cuenta nueva. Anotar el código que se ve en el log.
- [ ] **`test_token` en el canje.** El canje del código OAuth con el vendedor de prueba, ¿anduvo tal cual está o Mercado Pago pidió un parámetro `test_token`? Anotar el error si lo hubo.
- [ ] **Vuelta del navegador.** Al volver de pagar, ¿se llega a «Mi pedido» con el pedido ya en «Pagado»? Si cae en el login o dice «Estamos confirmando tu pago», anotar si el pedido terminó pagado igual por el aviso de Mercado Pago (tiene que pasar).
- [ ] **Devolución.** Devolvé el pago desde la cuenta del vendedor de prueba: ¿el pedido pasa a **Devuelto**, vuelve el stock y llega el aviso? (La app no devuelve plata sola: la devolución se hace siempre a mano desde Mercado Pago.)

## Cómo se apaga si algo sale mal

1. En Render, **Environment**: borrá las 4 variables (`MERCADOPAGO_CLIENT_ID`, `MERCADOPAGO_CLIENT_SECRET`, `MERCADOPAGO_OAUTH_REDIRECT_URI`, `MP_TOKEN_ENCRYPTION_KEY`). Render redeploya.
2. Resultado: desaparece la tarjeta de Mercado Pago de la configuración, el carrito ofrece sólo transferencia y el webhook responde 503 (Mercado Pago reintenta un tiempo).
3. Los pedidos de Mercado Pago que ya estaban pagados siguen como estaban. Un pedido sin pagar vence solo y el stock vuelve.
4. En GitHub podés dejar los 4 secrets. Con las variables fuera de Render, los pasos de Mercado Pago del workflow salen en verde «apagado» si no hay cuentas conectadas; si **hay** una cuenta conectada, salen en rojo a propósito, para que nadie olvide que sus tokens dejaron de renovarse. Para silenciarlo, desconectá la cuenta desde la configuración de la tienda antes de apagar.
5. Para volver a prender: cargá otra vez las 4 variables **con los mismos valores** (misma clave de cifrado; con otra, las cuentas quedan en «reconectar»). Mientras estuvo apagado no se acreditó ningún pago por Mercado Pago: al prender, la reconciliación recupera los de los últimos 7 días.
