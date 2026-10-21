# Moderación de la tienda: cola de aprobación + denuncias

Spec aprobada por el usuario el 2026-10-19. Reemplaza la versión anterior de este mismo archivo
(commit `52a7953`), que usaba modelos de Hugging Face en un Space propio. El usuario la objetó
por desproporcionada: con 6 gimnasios, la revisión humana es más barata y más segura, y no
agrega ni infraestructura, ni costo, ni modos de falla nuevos. Los números de línea son de
`main` `4656b80`; si se movieron, buscar por nombre.

## Por qué

Cualquiera que consiga una cuenta de gimnasio con la tienda habilitada podría vender algo ilícito:
drogas, servicios sexuales, armas, trata. Hoy no hay ninguna moderación:

- `Producto` no tiene estado de revisión.
- El superadmin no puede bajar un producto puntual. Sólo puede congelar el gimnasio o apagarle la
  tienda.
- `FotoProductoView` sirve las fotos sin login, sin mirar `publicado`, con
  `Cache-Control: public, max-age=31536000, immutable`, y está en `URLS_SIN_BLOQUEO`.

## Objetivos

1. Nada nuevo se vende ni se ve sin que lo apruebe el superadmin.
2. El superadmin puede bajar un producto puntual. Al bajarlo, se cancelan sus pedidos sin pagar.
3. El alumno puede denunciar un producto. Varias denuncias lo sacan de la venta sin esperar al
   superadmin.
4. Un gimnasio honesto casi no siente la moderación: un cambio en un producto ya aprobado no le
   frena la venta.

## No-objetivos (decisión del usuario)

- **Ningún modelo de IA, servicio externo ni cron nuevo.** Si el volumen crece, la moderación de
  OpenAI (texto + imagen) o Google Vision SafeSearch podrán marcar prioridad en esta misma cola
  con una llamada HTTP. No se construye ahora. Antes de usarlas, verificar que la gratuidad sea
  real.
- No se revisan de nuevo los productos que ya existen: quedan aprobados. El superadmin puede
  bajarlos desde la cola si los encuentra.
- No se modera el logo ni el fondo del gimnasio. Queda anotado en `ISSUES.md` como riesgo
  aceptado.
- Las cuotas por Mercado Pago son un proyecto aparte y van después.
  *(Actualización 2026-10-20: ese proyecto se construyó aparte, a la misma cuenta MP del
  gimnasio; no cambia nada de esta spec. Ver `ARQUITECTURA.md` § «Cuotas por Mercado Pago».)*

## Anti-objetivos (fallaría aunque «ande»)

- Una foto no aprobada termina en la caché pública `immutable` de Cloudflare o de algún
  navegador.
- El staff puede aprobar lo suyo, borrar la prueba o pisar una baja.
- Un pedido ya pagado se cancela por la moderación.
- La suite sale a la red, o una vista caliente hace una query por fila.

## Diseño

### Estados (dato de plataforma sobre `Producto` y `FotoProducto`, no hay `TenantOwnedModel` nuevo)

- `Producto.moderacion` es un `TextChoices`: `pendiente`, `aprobado` o `bajado`. Lleva
  `default=pendiente` y `db_default=pendiente`.
  - Los INSERT de la versión vieja durante el deploy caen en `pendiente`, que es lo seguro:
    `migrate` corre en el `buildCommand` mientras la versión vieja sigue atendiendo.
  - Una migración de datos pasa todos los productos existentes a `aprobado`.
- `Producto.cambio_pendiente` es un booleano con `db_default=False`. Se prende cuando un producto
  `aprobado` cambia su `nombre` o su `descripcion` (`form.changed_data`). El producto **sigue a la
  venta** con el texto nuevo y entra a la cola como «cambio para revisar» (decisión del usuario).
- `Producto.moderacion_motivo` (texto corto) y `Producto.moderacion_desde`. Los dos con
  `db_default`.
- `FotoProducto.aprobada` es un booleano con `default=False` y `db_default=False`.
  - Una migración de datos pasa las fotos existentes a `True`.
  - Una foto nueva queda oculta hasta que se aprueba, aunque su producto esté aprobado (decisión
    del usuario).
  - Los fixtures de tests que crean fotos a mano para servirlas tienen que pasar `aprobada=True`.
    Pasa con `FotoTests` y `GaleriaDeFotosTests`, y conviene centralizarlo en
    `tienda/testing.py`.
- Las variantes, las categorías y `instrucciones_retiro` no se moderan. Es un riesgo aceptado: lo
  cubre la denuncia y queda anotado.

### Qué se vende y qué se ve

- Hay una sola `Q`, `q_vendible(prefijo="")`, en `tienda/models.py`: `publicado=True` y
  `moderacion=aprobado`. La usan los dos únicos puntos que hoy filtran `publicado`:
  - el catálogo, en `views.py` cerca de la línea 633;
  - `_variantes_vendibles`, en `services.py:153`. Por ahí pasan el carrito, el checkout y
    `crear_pedido`.
- Al terminar, `grep publicado=True tienda/` sólo puede encontrarla dentro de `q_vendible`.
- `FotoProductoView` sirve en dos modos:
  - Si la foto está `aprobada` y el producto no está `bajado`, igual que hoy: público e
    `immutable`.
  - Si no: sólo al staff del mismo gimnasio o al superadmin, con `Cache-Control: private,
    no-store`. A cualquier otro le da 404.
  - La URL `?v=` no cambia al aprobar. Como la versión privada nunca se guardó en una caché
    compartida, no queda ninguna copia vieja.

### Staff

- Al guardar un producto nuevo, aparece `messages.info`: «Lo revisamos y lo publicamos en el día».
  Una foto nueva o un cambio de texto muestran el aviso equivalente.
- En el listado, junto al badge de `producto_list.html`, se ve «Esperando aprobación», «Cambio en
  revisión» o «Dado de baja por la plataforma».
- `ProductoForm` no expone ningún campo de moderación. La edición guarda con
  `save(update_fields=...)` sin esos campos, para no pisar un «Bajar» que entre en paralelo. Va un
  test de esa carrera.
- `ProductoBorrarView` se niega si el producto está `bajado` o tiene denuncias, para que no se
  pueda borrar la prueba y recrearlo limpio.
- Un producto `bajado` no se puede volver a publicar ni a editar desde el form.
- En el admin de Django, los campos de moderación son `readonly_fields`, hay `list_filter` por
  `moderacion` y `DenunciaProducto` se registra en sólo lectura.

### Denunciar (alumno)

- La vista usa `TiendaAlumnoMixin`, con `procesar` y `_cartel`.
  - El formulario es un `forms.Form`.
  - El producto se busca con `get_object_or_404(Producto.objects.for_gimnasio(g).filter(q_vendible()), pk=…)`.
  - Con suplantación activa se rechaza, igual que en `crear_pedido`.
  - Responde con `messages` y un redirect.
- El motivo se elige de un `select` (drogas, contenido sexual, armas, trata, estafa, otro) y hay
  un detalle opcional.
- `DenunciaProducto(TenantOwnedModel)`:
  - Campos: `producto` (PROTECT), `alumno` (PROTECT; **reemplazado por la decisión 10 del plan: SET_NULL**, para no delatar al denunciante en la pantalla de borrar alumno), `motivo`, `detalle`, `estado`
    (abierta/resuelta) y `creado`.
  - `UniqueConstraint(condition=Q(estado="abierta"))` por alumno y producto. Se valida antes en
    Python y se captura el `IntegrityError`.
- Hay que tocar **los tres lugares en el mismo commit**:
  - `vaciar_gimnasio`, antes de `Pedido` y antes de `alumnos.delete()`;
  - el fixture `_ensuciar`;
  - `EXCLUIDOS` de la exportación, con un motivo neutro porque aparece en el LEEME: «auditoría
    interna de la plataforma».
  - Además, un test de aislamiento.
- Con **3 denuncias abiertas de alumnos distintos**, el producto vuelve a `pendiente`:
  `filter(pk, moderacion=aprobado).update(...)`. «Aprobar» resuelve todas las denuncias, así que
  después sólo cuentan las nuevas y no queda en un bucle.
- El catálogo muestra «Ya lo denunciaste» con el set de pks en **una sola** query, por la prueba
  de escala de `tests_vistas_alumno.py`.
- El staff nunca sabe quién lo denunció.

### Cola del superadmin (`plataforma:moderacion`)

- Muestra cuatro listas, cada una con su gimnasio:
  - productos pendientes;
  - productos aprobados con `cambio_pendiente`;
  - fotos no aprobadas;
  - denuncias abiertas.
- **Las fotos se ven en la cola** (pedido del usuario, 19-10).
  - Cada producto muestra **todas sus fotos en miniatura**, con un click para verlas en grande,
    junto al nombre, la descripción, el precio y las variantes. Las fotos ya aprobadas aparecen
    marcadas como tales.
  - Se sirven por una vista exclusiva del superadmin con `Cache-Control: private, no-store`, y
    nunca por la URL firmada de R2.
  - El superadmin aprueba desde ahí mismo.
- **Hay acciones por foto, además de las acciones por producto:**
  - **Aprobar foto**: `aprobada=True`.
  - **Rechazar foto**: borra la fila y deja el archivo en R2. El staff ve «Una foto fue
    rechazada por la plataforma». El producto sigue a la venta sin esa foto.
  - Sirven para el caso «producto bien, una foto mal», sin bajar el producto entero.
- Cada acción va con POST y pantalla de confirmación, como `EstadoCuentaView`. «Aprobar foto»
  puede ir sin confirmación, porque es reversible con «Rechazar». Las acciones sobre productos
  son `filter(pk, moderacion__in=ORIGEN).update(...)`:
  - **Aprobar**: el producto pasa a `aprobado`, sus fotos a `aprobada=True`, `cambio_pendiente`
    vuelve a False y sus denuncias quedan resueltas.
  - **Bajar**:
    - el producto pasa a `bajado`, con el motivo, y las denuncias quedan resueltas;
    - las fotos se quedan en R2 como prueba, con 404 público permanente;
    - **se cancelan los pedidos sin pagar del producto** y se libera el stock con la misma
      transición que usa `vencer_pedidos` (decisión del usuario);
    - si después llega un aprobado de Mercado Pago sobre un pedido cancelado así, va a `REVISAR`
      y no lo revive. Hay que ajustar `acreditar_pedido`;
    - los pedidos pagados no se tocan.
  - **Suspender el gimnasio**: es un link a la `EstadoCuentaView` que ya existe.
- El contador «N para revisar» de `plataforma:inicio` tiene una cantidad constante de queries.
- Mail al superadmin cuando entra algo a la cola:
  - Sale por `transaction.on_commit` y sigue el patrón de `solicitudes/emails.py`.
  - Se manda **a lo sumo uno por gimnasio por hora**, con un campo
    `ConfiguracionTienda.ultimo_aviso_moderacion` (con `db_default` nulo).
  - En la suite usa el backend en memoria.

### Demo

`sembrar_demo` crea los productos y las fotos ya aprobados. Lo que sube un prospecto queda
pendiente, lo que de paso muestra cómo funciona la moderación, y `restaurar_demo` lo limpia
cada 6 h.

### Condiciones de uso

Todavía no hay términos para el gimnasio. La cláusula («Prohibido vender…; la plataforma revisa
cada producto y puede bajarlo, cancelar sus pedidos y suspender la cuenta») va en
`configuracion_form.html` de la tienda y en la pantalla del superadmin que habilita la tienda. El
texto final lo revisa el usuario.

## Qué NO se puede romper

| Cosa | Cómo se protege |
|---|---|
| Productos y fotos que ya están vendiéndose | La migración de datos los deja aprobados. Test de migración. |
| Pedidos pagados de un producto bajado | «Bajar» sólo cancela los que no tienen pago. Test con uno de cada uno. |
| Acreditación de Mercado Pago | Sólo cambia el caso del pedido cancelado por moderación. `tests_mercadopago_acreditar` queda verde sin tocarlo. |
| Caché `immutable` | Las fotos no aprobadas salen `private, no-store`. Test de cabeceras en los dos modos. |
| INSERT viejo durante el deploy | `db_default` en todas las columnas nuevas. Test de INSERT viejo, como el del 18-10, sobre Postgres. |
| Pruebas de escala (catálogo, plataforma) | «Ya lo denunciaste» en 1 query y contador constante. |
| `MigracionDeReferenciaExternaTests` | Su `tearDown` migra a la 0005. Pasarlo a la última migración de la app y correr `manage.py test tienda tenants` juntos. |
| Demo | `sembrar_demo` aprueba y `vaciar_gimnasio` borra las denuncias en orden. |
| La suite no sale a la red | No hay ningún servicio externo nuevo. |

## Lo que queda a criterio del implementador

- El texto exacto de los carteles.
- El diseño visual de la cola, que sigue el estilo del panel de plataforma.
- El nombre de las migraciones.
- Si «aprobar» y «bajar» van en una sola vista con el parámetro de acción o en dos.

## Verificación

- La suite completa con `.env`, más `tienda tenants plataforma` juntos, y la suite sobre Postgres
  16 en Docker.
- Cada test de regresión tiene que fallar sin su fix.
- QA en el navegador:
  - un producto nuevo queda pendiente y no aparece en el catálogo;
  - aprobarlo lo hace aparecer;
  - una foto nueva sobre un producto aprobado queda oculta para el alumno y visible para el
    staff;
  - un cambio de texto entra a la cola;
  - denunciar desde un alumno;
  - con 3 denuncias el producto vuelve a pendiente;
  - bajar un producto con un pedido pendiente cancela ese pedido.
- `curl -I` sobre una foto no aprobada sin sesión da 404, y con sesión da `no-store`.
