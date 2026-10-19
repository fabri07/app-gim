<!-- Spec aprobada por el usuario el 2026-10-19 (como plan). Los números de línea son de main 00e908b: después del merge de variantes (4656b80) algunos se movieron — buscar por nombre. -->

# Moderación de la tienda (revisión automática + denuncias)

## Contexto

Cualquiera que consiga una cuenta de gimnasio con la tienda habilitada podría vender algo
ilícito (drogas, servicios sexuales, armas, trata). Hoy **no hay ninguna moderación**:
- `Producto` no tiene estado de revisión.
- El superadmin no puede bajar un producto puntual. Sólo puede congelar el gimnasio o apagar la
  tienda.
- `FotoProductoView` (tienda/views.py:436) sirve las fotos sin login, sin mirar `publicado` y con
  `immutable` por un año.

El dueño del producto pidió estar preparado.

**Decisiones del usuario (19-10):**
- **Orden:** seguridad primero. Después vienen las cuotas por Mercado Pago a la cuenta del
  gimnasio: el usuario levantó la regla «cuotas sin MP», pero es un proyecto aparte, con su
  spec, después de la prueba guiada del MP de la tienda. **No va en este plan.**
- **Al detectar algo:** retener y revisar. El producto queda oculto y no se puede comprar hasta
  que el superadmin lo apruebe, y se le avisa por mail.
- **Cobertura:** fotos con contenido sexual, texto del producto (nombre y descripción) y un botón
  «Denunciar» para el alumno.
- **Al bajar un producto:** se cancelan sus pedidos sin pagar. Un pago de MP que llegue tarde va
  a «Revisar» y no revive el pedido.
- **Fuera de alcance:**
  - la pasada sobre los productos que ya están publicados;
  - drogas y armas en fotos (requiere un modelo de visión pago), cubiertas sólo por la denuncia;
  - el logo y el fondo del gimnasio, que se anotan en `ISSUES.md`.

## Costo cero (pedido del usuario, 19-10: «no quiero pagar por el momento»)

Los proveedores de la API de Hugging Face (`hf-inference`) sólo traen unos créditos gratis por
mes. Cuando se agotan responden 402 y hay que pagar. **No se usan.** Los dos modelos corren en un
**Space propio y privado de Hugging Face en hardware gratis** («CPU basic», 2 vCPU y 16 GB) desde
la cuenta `fabri794`:
- Expone un endpoint HTTP chico, `POST /texto` y `POST /foto`.
- La app lo llama con el `HF_TOKEN` gratis del usuario, porque el Space es privado.
- Render free (512 MB) no puede cargar los modelos, así que no corren en la app.

Lo que se resigna por ser gratis:
- **El Space se duerme a las 48 h sin uso** y tarda unos 30-60 s en despertar. Por eso el cron
  de 15 min (ver más abajo) lo llama siempre aunque no haya pendientes, para mantenerlo
  despierto.
- Si igual está dormido o caído, la revisión queda `sin_revisar` y sigue el camino de
  «pendientes» de este plan. Un Space que no responde nunca publica algo sin revisar.
- **La lista de palabras local y la denuncia no dependen del Space.** Con el Space caído, la
  tienda sigue protegida por esas dos.

Crear el Space es publicar en una cuenta de terceros: se hace en la Tarea 0, con el OK del
usuario en ese momento.

## Modelos (los dos de código abierto y gratis, corren dentro del Space)

- **Fotos:** `Falconsai/nsfw_image_detection`, con las etiquetas `normal` y `nsfw`.
- **Texto:** `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, zero-shot multilingüe con etiquetas
  propias. Van cuatro de riesgo («venta de drogas o estupefacientes», «servicios sexuales o
  prostitución», «armas de fuego o municiones», «personas ofrecidas a cambio de dinero») y una
  neutra («artículo de gimnasio, ropa o suplemento») para que los suplementos y pre-entrenos no
  den falsos positivos.
- **Lista de palabras local** (decisión mía), en `tienda/palabras.py`:
  - Jerga rioplatense, comparada por **palabra entera y sin acentos**: «merca» no puede saltar con
    «mercadería» ni con «Mercado Pago».
  - No sale a la red y se puede testear.
  - También se aplica a `Variante.nombre`, `CategoriaProducto.nombre` y
    `ConfiguracionTienda.instrucciones_retiro`, que también le llegan al alumno.

**Tarea 0: compuerta (sin costo).**
1. El usuario genera un `HF_TOKEN` de lectura gratis en su cuenta.
2. Se crea el Space privado en CPU basic con OK explícito: un `app.py` de FastAPI o Gradio con
   `transformers` y los dos modelos, más su `requirements.txt`. El código vive en el repo, en
   `moderacion_space/`, para versionarlo.
3. Se confirma que el hardware dice «CPU basic · Free» y que no hay ningún método de pago
   asociado.
4. Un script descartable corre 10 textos (con suplementos legítimos) y 4 fotos y mide:
   - latencia caliente;
   - latencia en frío;
   - puntajes.

Los umbrales salen de esa medición. Si caliente tarda más de 8 s, se frena y se le pregunta al
usuario.

## Diseño

### Estado de moderación (dato de plataforma sobre `Producto`, no es un modelo tenant-owned nuevo)
- **Campos nuevos en `Producto`:** `moderacion` (`ok` / `retenido` / `bajado`),
  `moderacion_motivo`, `moderacion_desde` y `texto_pendiente`.
- **Campo nuevo en `FotoProducto`:** `revisada`.
- **Todas las columnas con `db_default`.** `migrate` corre en el `buildCommand` mientras la
  versión vieja sigue atendiendo, como pasó con `ConfiguracionTienda`
  (tienda/models.py:56). `FotoProducto.revisada` va con `default=True, db_default=True` para que
  `FotoTests` y `GaleriaDeFotosTests` sigan creando filas sin cambios.
- **Qué es vendible se decide en un solo lugar:** una `Q` compartida `q_vendible(prefijo="")` en
  `tienda/models.py` que exige `publicado` y `moderacion=ok`. Se usa en los **dos** puntos que
  hay: el catálogo (views.py:498) y `_variantes_vendibles` (services.py:153), por donde ya pasan
  carrito, checkout y `crear_pedido`. Para verificarlo, un grep de `publicado=True` en `tienda/`
  tiene que dar sólo `q_vendible`.
- **`FotoProductoView`** devuelve 404 si `moderacion != ok` o si `revisada=False`. No usa
  `q_vendible`, porque a propósito sirve las fotos de los borradores del staff.
- **Transiciones:**
  - Siempre con `filter(pk, moderacion__in=ORIGEN).update(...)`. Retener sólo pasa de `ok`, así
    que nunca baja a un `bajado`.
  - La edición del staff guarda con `save(update_fields=...)` sin los campos de moderación, para
    no pisar un «Bajar» o una tercera denuncia que entren durante los segundos de red. Va un test
    de esa carrera.
- **El staff no puede quitar la moderación:**
  - `ProductoForm` no expone esos campos.
  - Un producto `retenido` o `bajado` muestra el cartel «En revisión por la plataforma» o «Dado de
    baja por la plataforma» junto al badge de `producto_list.html:43`.
  - Las fotos ocultas se reemplazan por un placeholder.
  - `ProductoBorrarView` se niega si `moderacion != ok` o si hay denuncias, para que no se pueda
    borrar la prueba y recrear el producto limpio.
- **Admin de Django:** los campos de moderación van en `readonly_fields`, con `list_filter` por
  `moderacion`. `DenunciaProducto` se registra sólo lectura.

### Revisión al guardar (`_ProductoFormMixin.form_valid`, views.py:170-187)
- **Módulo nuevo `tienda/moderacion.py`:** `revisar_texto()` y `revisar_foto(bytes)` devuelven
  `Veredicto(ok | sospechoso | sin_revisar, motivo)`. El HTTP va con `urllib.request`, como
  `suscripcion/mercadopago.py:158`; no se agrega `requests`.
- **Cuándo se revisa:** sólo lo que cambió. El texto si `nombre` o `descripcion` están en
  `changed_data`, y las fotos si son nuevas. Precio y stock nunca llaman a la red.
- **Tiempo de respuesta:** Render tiene 1 worker sync con timeout de 30 s.
  - Las llamadas van **antes** del `atomic()`, después de `preparar_foto`.
  - Corren **en paralelo** (`ThreadPoolExecutor`) con un **tope total de 8 s**.
  - Lo que no termina en ese tiempo queda `sin_revisar`.
  - Los mails salen por `transaction.on_commit`.
- **Si algo da sospechoso:**
  - El producto y la foto se guardan igual, con el archivo en R2, y el producto queda
    `retenido` dentro del mismo `atomic()`.
  - Se conservan como prueba y para que el superadmin vea qué se subió.
  - El staff ve un `messages.warning` y se manda mail al superadmin.
- **Si la revisión no se pudo hacer (falla o timeout):**
  - Si era una foto, queda `revisada=False`: da 404 y no se publica, así que nunca entra en la
    caché `immutable`. El producto sigue a la venta con sus otras fotos.
  - Si era el texto, queda `texto_pendiente=True` y el producto sigue visible (decisión mía: una
    caída de Hugging Face no traba la tienda de un gimnasio honesto, y la denuncia lo cubre).
  - **En la demo** (`es_demo`) es al revés y se falla cerrado: el producto queda retenido. Las
    credenciales de la demo las tienen los prospectos.
- **Bandera:** `MODERACION_ENABLED = _bandera_todo_o_nada(["MODERACION_URL", "HF_TOKEN"], ...)
  and not TESTING`, donde `MODERACION_URL` es la URL del Space.
  - Ahora son dos variables, así que sí corresponde `_bandera_todo_o_nada`: si se carga una sin
    la otra, falla.
  - Se declaran en `render.yaml` (`sync: false`) y en `.env.example`.
  - Con la bandera apagada, la lista de palabras sigue activa y el Space no se llama.

### Cron `revisar_pendientes`
- Re-revisa las fotos con `revisada=False` y los textos con `texto_pendiente`.
- Lleva un tope de reintentos por corrida, porque si el Space está caído no conviene quedarse
  martillándolo.
- **Llama al Space en cada corrida aunque no haya pendientes**, para mantenerlo despierto.
- Sale con 1 si hay pendientes y la bandera está apagada, como `reconciliar_pedidos`.
- **Va en `enviar-recordatorios.yml`** (cada 15 min), para que el ping alcance a mantener
  despierto el Space.
  - Hay que agregarle los secrets `MODERACION_URL`, `HF_TOKEN`, `R2_*` (sin R2 no puede leer las
    fotos) y `EMAIL_*` (sin ellos el mail cae a la consola).
  - El paso va con `if: ${{ !cancelled() }}` y no arrastra a `enviar_recordatorios` ni a
    `vencer_pedidos`.
  - Los secrets se anotan en `docs/runbook-respaldos.md`.
  - Se documenta que GitHub atrasa el cron de 2 a 4 h.
  - Los minutos de Actions entran en GitHub Pro, que el usuario ya paga, sin costo nuevo.
- Si los pendientes crecen, salen en el contador del panel de plataforma.

### Denunciar (alumno)
- **La vista** usa `TiendaAlumnoMixin` con `procesar` (views.py:464), que trae `_cartel`, el
  control de `alumno is None` y el de tienda visible.
  - Usa un `forms.Form`, no `TenantScopedModelForm`.
  - El producto se busca con
    `get_object_or_404(Producto.objects.for_gimnasio(g).filter(q_vendible()), pk=…)`.
  - Se rechaza si hay suplantación activa, igual que `crear_pedido`.
  - Responde con `messages` y redirect, como el resto del módulo.
- **El formulario:** el motivo se elige de un `select` (drogas, contenido sexual, armas, trata,
  estafa, otro) y hay un detalle opcional.
- **`DenunciaProducto(TenantOwnedModel)`:** `producto`, `alumno`, `motivo`, `detalle`, `estado`
  (abierta/resuelta) y `creado`.
  - `producto` va con `on_delete=PROTECT` (el borrado ya está bloqueado) y `alumno` con
    `on_delete=PROTECT`.
  - `UniqueConstraint(condition=Q(estado="abierta"))` por alumno y producto, validado antes en
    Python y con el `IntegrityError` capturado.
- **Los TRES lugares, en el mismo commit:**
  - `vaciar_gimnasio`: antes de `Pedido` y antes de `alumnos.delete()`, por el PROTECT, en
    tenants/demo.py:770-805.
  - `_ensuciar`.
  - `EXCLUIDOS` de `exportacion.py`, con un motivo neutro porque aparece en el LEEME: «auditoría
    interna de la plataforma», como `RegistroSuplantacion`.
  - Más un test de aislamiento.
- **3 denuncias abiertas de alumnos distintos ponen el producto en `retenido`** (decisión mía).
  Al «Aprobar» se resuelven todas, así que sólo cuentan las nuevas y no se vuelve a retener en
  bucle.
- **Mail al superadmin:** como máximo uno por producto por día. El staff no sabe quién denunció.
- **El catálogo** muestra «Ya lo denunciaste» con un set de pks en **una** query, para respetar la
  prueba de escala de tests_vistas_alumno.py:325.

### Cola del superadmin (`plataforma/`)
- **`plataforma:moderacion`** muestra los productos retenidos, las denuncias abiertas, las fotos
  sin revisar y los textos pendientes. Las fotos se sirven por una vista sólo para superadmin,
  `Cache-Control: private, no-store`.
- **Acciones**, con POST y pantalla de confirmación como `EstadoCuentaView` (views.py:300):
  - **Aprobar:** el producto pasa a `ok`, sus fotos a `revisada=True` y las denuncias se
    resuelven.
  - **Bajar:**
    - el producto pasa a `bajado` y las denuncias se resuelven;
    - las fotos quedan en R2 como prueba, con 404 permanente;
    - **se cancelan los pedidos sin pagar del producto** y se libera el stock, con la misma
      transición y liberación que usa `vencer_pedidos`;
    - un aprobado tardío de MP sobre esos pedidos va a `REVISAR` y no revive el pedido (hay que
      ajustarlo en `acreditar_pedido`);
    - se documenta purgar Cloudflare a mano si la foto se llegó a servir.
  - **Suspender el gimnasio:** es un link a la `EstadoCuentaView` que ya existe.
- **Contador «N para revisar»** en `plataforma:inicio`: una cantidad constante de queries, sin
  importar las filas.
- **Mails:** el helper `avisar_moderacion()` sigue el patrón de `solicitudes/emails.py`
  (`EmailMessage`, la casilla de la cola de settings.py:431).

### Condiciones de uso
No hay términos para el gimnasio, sólo `tenants/privacidad.html` para el alumno. La cláusula
(«Prohibido vender…; la plataforma puede bajar productos, cancelar pedidos y suspender la
cuenta») va en `configuracion_form.html` de la tienda y en la pantalla del superadmin donde se
habilita la tienda. El usuario revisa el texto final.

## Qué NO se puede romper
| Cosa | Cómo se protege |
|---|---|
| Pedidos ya pagados de un producto bajado | «Bajar» sólo cancela los que no tienen pago. Test con un pedido pagado y otro pendiente. |
| Acreditación MP | Sólo cambia el caso «pedido cancelado por moderación», que va a REVISAR. El resto de `tests_mercadopago_acreditar` queda verde sin tocarlo. |
| Camino perezoso, catálogo y foto sin red (`VencimientoPerezosoSinPushTests`) | La red se usa sólo al guardar el staff y en el cron. Test que parchea `urllib.request.urlopen` y falla si el catálogo o la foto lo llaman. |
| La suite no sale a la red | `MODERACION_ENABLED` está apagada en TESTING y los tests mockean el cliente. Se suma Hugging Face a la regla de CLAUDE.md. |
| Timeout de 30 s de gunicorn | Tope total de 8 s, en paralelo, fuera del `atomic()`, mails por `on_commit`. |
| Fotos `immutable` | Una foto sin revisar o de un producto no `ok` da 404 antes de servirse por primera vez. |
| INSERT de la versión vieja durante el deploy | `db_default` en todas las columnas nuevas, con un test de INSERT viejo como el del 18-10. |
| Pruebas de escala (catálogo, plataforma) | «Ya lo denunciaste» en 1 query y contador constante. Las pruebas de escala existentes siguen verdes. |
| `MigracionDeReferenciaExternaTests` (tests_modelos.py:378) | Su `tearDown` migra a la 0005. Se cambia a la última migración de la app, y se corre `manage.py test tienda tenants` junto. |
| Demo y `restaurar_demo` | `sembrar_demo` crea en `ok` sin cambios. `vaciar_gimnasio` borra las denuncias en orden. |
| Stock, variantes, `modificado` de `Gimnasio` | No se tocan, salvo la liberación de stock de los pedidos cancelados. |

## Archivos principales
- **tienda:**
  - `tienda/models.py`, con los campos, `q_vendible` y `DenunciaProducto`, y su migración 0007.
  - Nuevos: `tienda/moderacion.py`, `tienda/palabras.py` y
    `tienda/management/commands/revisar_pendientes.py`.
  - Cambian: `views.py`, `forms.py`, `services.py`, `admin.py` y los templates del catálogo y del
    staff.
- **plataforma:** `plataforma/views.py`, `urls.py` y los templates.
- **Config:** `config/settings.py`, `render.yaml`, `.env.example` y
  `.github/workflows/enviar-recordatorios.yml`.
- **tenants:** `tenants/demo.py`, `tenants/tests.py` y `tenants/exportacion.py`.
- **Docs:**
  - `ARQUITECTURA.md`: subsección «Moderación de la tienda» y la trampa 12, con el criterio nuevo
    de la foto.
  - `CLAUDE.md`: fila en la tabla y Hugging Face en «La suite no sale a la red».
  - `ISSUES.md`: logo y fondo sin moderar.
  - `docs/runbook-respaldos.md`.

## Forma de trabajo
1. Spec en `docs/superpowers/specs/2026-10-19-moderacion-tienda-design.md`, a partir de este
   documento.
2. Tarea 0: crear el Space gratis, con OK del usuario, y medir.
3. Plan con `superpowers:writing-plans`, más dos pasadas adversariales sobre ese plan antes de
   pedir aprobación.
4. Implementar en un worktree con subagents y TDD. Cada test de regresión se verifica fallando
   sin el fix.

## Verificación
- **Suite completa** `python manage.py test -v 2`, con `.env` (por los 5 tests no herméticos),
  más `tienda tenants plataforma` juntos. **Y la suite sobre Postgres 16 en Docker**, como el
  18-10.
- **Tests nuevos clave:**
  - aislamiento de `DenunciaProducto`;
  - `q_vendible` en catálogo y carrito;
  - foto 404 si está sin revisar o si el producto no está `ok`;
  - sin red en catálogo y foto;
  - la carrera de «Bajar» contra la edición del staff;
  - 3 denuncias retienen y «Aprobar» no vuelve a retener;
  - «Bajar» cancela los pendientes y respeta los pagados;
  - el MP tardío va a REVISAR;
  - el staff no puede borrar ni des-retener;
  - la lista de palabras no salta con «mercadería» ni con «Mercado Pago»;
  - tope de 8 s con un cliente mockeado lento;
  - el INSERT viejo con `db_default`.
- **QA en el navegador** (runserver, verificar con el service worker en mente):
  - «vendo merca» queda retenido, con aviso, y no aparece en el catálogo;
  - un suplemento queda en `ok`;
  - denunciar desde un alumno;
  - aprobar y bajar desde `plataforma:moderacion`, con un pedido pendiente que se cancela;
  - con el `HF_TOKEN` real, una foto normal y una foto de prueba.
