# Portal del alumno adaptado a la compu (+ menú del alumno)

## Contexto

El usuario vio que el portal del alumno «se ve siempre como un teléfono celular» en Chrome de
escritorio y pidió que **toda la versión del alumno se adapte a la web**. Hoy casi todas sus
pantallas usan `.contenido` (`max-w-md`, 448 px) por una regla explícita de `DESIGN.md:372`, y el
alumno **no tiene menú**: navega por las tarjetas del inicio y por links «Volver al inicio». Ya
son anchas: `turnos/mis_turnos.html`, `rutinas/mi_dia_detalle.html` (tabla en escritorio, carrusel
en celular) y el catálogo de la tienda (`85e5a58`).

**Decisiones del usuario (17-10):**
- **Inicio = panel en 2 columnas** en la compu. Arriba el saludo + «Reservar turno». **Izquierda:
  «Tu código», «Tu rutina», «Tu cuota». Derecha: Tienda/Mis pedidos, «Últimas novedades»** (+ redes
  y privacidad). El celular queda en el mismo orden de hoy.
- **Menú del alumno en compu y celular**; en el celular, **☰ en la barra de arriba** junto a
  «Salir», como el staff.
- **Carrito, detalle del pedido y comprobante de cuota en 2 columnas** en la compu; el comprobante
  **muestra al costado los datos para transferir** (alias, titular, entidad; también en el celular,
  arriba del formulario).
- **«Mis pedidos»: tabla en la compu, en el celular como hoy** (líneas).
- **«Mi código» y los carteles cortos quedan como tarjeta centrada.**
- **Política de privacidad con ancho de lectura cómodo** (~70 caracteres por línea).

Revisiones: completa (13 hallazgos, 2 críticos) + verificación contra el código y htmx (13) + una
revisión nueva con ojos frescos centrada en «¿cumple el pedido?» (9 + preguntas, respondidas) +
verificación de esa versión (11: 3 bloqueantes —márgenes dentro de las grillas, el test de «1 ×
Remera», monto/texto del comprobante— y la forma de no cambiar el celular partiendo el contenido
DENTRO de la tarjeta) + dos pasadas más de verificación (la primera, 2 bloqueantes de espaciado
en el celular y de la partición del carrito; **la última, sin nada bloqueante**, 3 menores ya
incorporados). Todo incorporado.

## Qué NO se puede romper

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| Celular de todas las pantallas del alumno | el contenedor nuevo `.contenido--alumno` es `max-w-md` hasta `lg` (idéntico a `.contenido`); las grillas sólo se activan desde `lg`; el orden del HTML no cambia en ninguna pantalla | QA en iframe de 390 px, 360 px y 800 px |
| Cartel de cuenta sin acceso (`plataforma/cuenta_bloqueada.html` extiende `base.html`) | menú y ☰ **no** se dibujan con `cuenta_suspendida` ni `cuenta_alumnos_bloqueados` (`ESTADOS_SIN_ACCESO_ALUMNO`, `tenants/models.py:518`; `plataforma/context_processors.py`, sin queries, también suplantando) | `plataforma/tests_bloqueo.py:158-164` + tests nuevos de los dos estados, también suplantando |
| Nav y ☰ del staff (`base.html:216-258`), nav del superusuario sin `:class` | no se tocan; el menú del alumno es un `{% if %}` aparte después de la cadena staff/superusuario y antes de `messages`; su ☰ lleva `aria-label="Menú"` (no «Abrir menú») | `tests_bloqueo.py:193,232,233`, `plataforma/tests.py:446` |
| `x-data` del `<body>` (`base.html:85`, hoy sólo staff) | pasa a **staff o alumno** (☰ del topbar y menú comparten `navAbierto`; login y logout son `hx-boost="false"`, suplantar sólo lo hace el staff: el `<body>` de un alumno nunca queda sin `x-data`) | test del `<body>` del alumno |
| `home.html` rama alumno + `_portal_alumno` | mismas condiciones; dos `<div>` columna; cero queries nuevas | tests del portal en `tenants` sin tocar (incl. `tenants/tests.py:1532-1547`) |
| `mi_dia_detalle` (doble forma tabla/carrusel) | sólo recibe el menú y su «Volver a mi rutina» pasa a `home#mi-rutina` | `rutinas/tests.py:1723` |
| `mis_pedidos.html` (rama normal y rama del «Botón de arrepentimiento» con un form por fila) | **un solo markup** `<table>` con CSS que debajo de `lg` muestra cada fila como las líneas de hoy; `_arrepentimiento.html` sigue | `tienda/tests_vistas_alumno.py:230-253,362-373`, `test_el_link_esta_en_el_catalogo_y_en_mis_pedidos` |
| Carrito, `mi_pedido`, comprobante de cuota (forms con `hx-boost="false"` multipart; el de la tienda con `campo_form.html`, el de la cuota con `form.as_p` + `novalidate`; «Botón de arrepentimiento») | misma tarjeta única, contenido partido en dos columnas sólo desde `lg`, sin cambiar el orden; forms intactos; en el celular igual que hoy (salvo los datos de transferencia nuevos del comprobante, pedidos por el usuario) | tests de vistas de tienda (incl. `test_la_cantidad_no_se_duplica_en_el_detalle`) y de `pagos` |
| `partials/cuotas_impagas.html` (datos de transferencia del inicio) | su bloque de medios se extrae a `partials/medios_cobro.html` sin cambiar el HTML resultante | tests del portal con medios cargados |
| Política de privacidad (también la ven anónimos) | sólo el contenedor: ancho de lectura | test existente de privacidad |

## Diseño

**Contenedores y grillas (reusables, en `styles/input.css`, documentados en `DESIGN.md`)**
- `.contenido--alumno { @apply max-w-md mx-auto px-4 py-8 lg:max-w-5xl; }`: tercer contenedor junto
  a `.contenido`/`.contenido--ancho`; se usa como `main_class` en las pantallas del alumno que se
  adaptan. Entre 640 y 1023 px todo queda en 448 px como hoy (los botones `w-full` no se agigantan).
- `.columnas { @apply grid grid-cols-1 gap-6 lg:grid-cols-2 items-start mb-6 last:mb-0; }`
  (dos `<div>` columna; inicio y `mis_turnos`). `.columnas > :only-child { @apply lg:col-span-2; }`.
- `.con-lateral { @apply grid grid-cols-1 items-start lg:grid-cols-[minmax(0,1fr)_20rem]
  lg:gap-x-8; }` — **sin espacio vertical** (en un grid los márgenes no colapsan: con `gap-6` el
  celular sumaría 24 px entre principal y lateral). Molde `.config-layout`
  (`styles/input.css:1009-1011`). **Se usa DENTRO de una sola `.tarjeta`** (carrito, pedido,
  comprobante): en el celular la tarjeta queda exactamente como hoy; desde `lg` su contenido se
  parte en principal + lateral. `.con-lateral > * > :first-child { @apply lg:mt-0; }` alinea arriba
  las dos columnas en la compu (cubre el total del carrito y el aviso del arrepentimiento de
  `mi_pedido`).
- En `.columnas` los hijos son `<div>` columna: la última tarjeta de cada una ya pierde su margen por
  `.tarjeta:last-child { mb-0 }` (`styles/input.css:217`), y el `gap-6` deja los mismos 24 px de hoy.
- `.contenido--lectura { @apply max-w-2xl mx-auto px-4 py-8; }` (privacidad; ~70-75 caracteres por
  línea con el `p-6` de la tarjeta).
- Quedan **cuatro** contenedores: `.contenido`, `.contenido--ancho`, `.contenido--alumno`,
  `.contenido--lectura`.

**1. Menú del alumno** — `templates/partials/nav_alumno.html`, incluido en `base.html` con
`{% if user.perfil.rol == "alumno" and user.perfil.gimnasio and not cuenta_suspendida and not
cuenta_alumnos_bloqueados %}`; el ☰ del topbar lleva la misma condición.
- ☰: `sm:hidden`, `@click="navAbierto = !navAbierto"`, `:aria-expanded="navAbierto"`,
  `aria-controls="nav-alumno"`, `aria-label="Menú"`.
- `<nav id="nav-alumno" class="nav-alumno" aria-label="Secciones" :class="{ 'nav-alumno--abierta':
  navAbierto }" x-init="navAbierto = false">` (el `x-init` lo cierra en cada swap de htmx).
- CSS: estilos compartidos `.nav-staff, .nav-alumno { … }` y `.nav-staff a, .nav-alumno a { … }`;
  **después** de esa regla compartida (si no, su `flex` le gana a `hidden`),
  `.nav-alumno { @apply hidden sm:flex; }` y `.nav-alumno.nav-alumno--abierta { @apply flex; }`
  (selector doble: gana sobre `hidden` sin depender del orden del CSS compilado; sin parpadeo antes
  de Alpine, el repo no usa `x-cloak`).
- Links: **Inicio** (`home`), **Mi rutina** (`home` + `#mi-rutina`: no hay índice de días;
  `scroll-mt-20` en la tarjeta por el topbar sticky; sin ficha o bloqueado cae en el inicio),
  **Turnos** (`turnos:mis_turnos`), **Tienda** (`tienda:catalogo`, sólo con
  `gimnasio.puede_usar_tienda`, propiedad sin query).
- `aria-current="page"`: Inicio con `url_name == "home"`; Mi rutina en `mi_dia_detalle`; Turnos
  namespace `turnos`; Tienda app `tienda`.
- `tienda/cerrada.html` suma un link a «Mis pedidos». Los «Volver al inicio» existentes se dejan.
- Se actualizan el comentario de `base.html` en la nav del superusuario y `ARQUITECTURA.md:2835-2838`
  (el `x-data` ya no es sólo del staff).

**2. Inicio en 2 columnas** — `templates/tenants/home.html`: `main_class` del alumno → `contenido--alumno`. Saludo
arriba (botón «Reservar turno» `w-full lg:w-auto`). Debajo `<div class="columnas">` con dos `<div>`:
- Izquierda: `{% if not alumno %}`sin vínculo`{% else %}`bloqueo, «Tu código», «Tu rutina»
  (`id="mi-rutina"`), «Tu cuota»`{% endif %}`.
- Derecha: `{% if alumno %}{% if tienda_visible %}…{% elif tienda_pedidos_vivos %}…{% endif %}{% endif %}`,
  «Últimas novedades», redes, privacidad.
Orden del HTML idéntico al de hoy; ninguna columna queda vacía.

**3. Tienda del alumno** (en el celular, igual que hoy)
- **Catálogo**: la tarjeta de cabecera queda; adentro, desde `lg`, título a la izquierda y
  «Carrito (n)» / «Mis pedidos» a la derecha (`lg:flex lg:justify-between lg:items-center`; el `<p>`
  de los links con `lg:mb-0`).
- **Carrito** (`contenido--alumno`, `templates/tienda/carrito.html`): hoy el `h1` está antes de
  `{% if lineas %}` (`:6`) y «Seguir viendo» después del `{% endif %}` (`:31`). Queda:
  `{% if lineas %}<div class="con-lateral"><div>h1 + ul de líneas (como hoy)</div><div>total (con
  `lg:mt-0`), aviso del plazo, form «Confirmar pedido», «Seguir viendo»</div></div>{% else %}h1 +
  «vacío» + «Seguir viendo»{% endif %}` (se duplican el `h1` y el link, una vez por rama). El orden
  visual del celular no cambia.
- **Detalle del pedido** (`mi_pedido`, `contenido--alumno`): misma tarjeta, `.con-lateral` adentro.
  Principal = `h1`, línea de estado, avisos de cancelación e ítems (la celda sigue mostrando
  `{{ i }}` = «1 × Remera», que fija `test_la_cantidad_no_se_duplica_en_el_detalle`
  `tests_vistas_alumno.py:210-214`). Lateral = todo desde `{% if pedido.estado == 'pendiente_pago' %}`
  hasta «Mis pedidos» (en revisión/pagado el lateral queda corto: aceptado). Orden del HTML intacto.
- **Mis pedidos** (`contenido--alumno`): una sola `<table class="tabla tabla--pedidos">` envuelta en
  `{% if pedidos %}…{% else %}<p>…</p>{% endif %}` (el `{% empty %}` no puede quedar dentro de la
  tabla). Columnas: número, fecha, total, estado y, sólo en la rama del arrepentimiento, una
  `td.celda-accion` con el botón `boton w-full mt-2 lg:w-auto lg:mt-0`. La rama del arrepentimiento
  conserva el número sin link y el estado como texto (como hoy). **Corte en `lg`** (el contenedor
  mide 448 px hasta `lg`). Reglas, después de las de `.tabla`:
  `@media (width < 64rem) { .tabla--pedidos, .tabla--pedidos tbody, .tabla--pedidos tr { @apply block; }
  .tabla--pedidos { @apply bg-transparent border-0 shadow-none rounded-none overflow-visible text-base; }
  .tabla--pedidos thead { @apply hidden; } .tabla--pedidos tr { @apply my-2; }
  .tabla--pedidos td { @apply inline p-0 border-0; } .tabla--pedidos td + td::before { content: "· "; }
  .tabla--pedidos .celda-accion { @apply block; } .tabla--pedidos .celda-accion::before { content: none; }
  .tabla--pedidos tr:has(.celda-accion) { @apply mt-2 mb-0; } }` (la rama del arrepentimiento hoy
  usa `div.mt-2` sin margen abajo; el número va en `<strong>`, sin link)
  → en el celular se ve como las líneas de hoy (`text-base` porque `.tabla` aplica `text-sm`,
  `input.css:313`; `my-2` reproduce el `mb-2` del `<p>` de hoy; salto de línea entre `</td>` y `<td>`
  porque el « · » depende de ese espacio; el `<th>` de la acción sólo en la rama del
  arrepentimiento). «Ver todos mis pedidos» sigue en la rama del arrepentimiento.

**4. Comprobante de cuota** (`pagos/comprobante_alumno_form.html`, `contenido--alumno`, la tarjeta
única con el `h1` arriba y `.con-lateral` debajo): principal = período, monto **sólo si `object.monto > 0`** (el cron
crea cuotas con `monto=0`, `pagos/models.py:285`; mismo criterio que `partials/cuotas_impagas.html:20`)
y los datos para transferir; lateral (`<div class="mt-4 lg:mt-0">`, para que en el celular el form
no quede pegado a la lista de medios) = el formulario de siempre (`{{ form.as_p }}` con `novalidate`,
`hx-boost="false"` multipart, sin tocar). Los datos para transferir salen del bloque que ya existe en
`partials/cuotas_impagas.html:26-45` (**no** la 46, que es el `{% endif %}` de `{% if cuotas_impagas %}`;
usa sólo `medios_cobro` y `gimnasio.contacto`; «Copiar» anda en cualquier pantalla porque `pwa.js`
se carga en `base.html:82`), extraído a `partials/medios_cobro.html` e incluido en los dos lugares.
`AlumnoComprobanteUpdateView` (`pagos/views.py:170`, hoy sin `get_context_data`) gana un
`get_context_data` con `super()` que suma
`medios_cobro = MedioCobro.objects.for_gimnasio(self.gimnasio).filter(activo=True)` y `gimnasio`
(1 query, aislamiento por `for_gimnasio`, también cuando el form vuelve con error). En el celular:
datos arriba, formulario abajo, dentro de la misma tarjeta (los datos son nuevos: decisión del
usuario).

**5. Otras pantallas**
- `turnos/mis_turnos.html`: desde `lg`, «Google Calendar» y «Mis reservas» lado a lado con
  `.columnas`: `{% if calendar_mostrar or mis_reservas %}<div class="columnas">{% if calendar_mostrar %}<div>…</div>{% endif %}{% if mis_reservas %}<div>…</div>{% endif %}</div>{% endif %}`
  (si sólo está una, ocupa las dos columnas por `:only-child`; si no está ninguna, no queda una
  grilla vacía); no se toca el doble contenedor del template (cambiaría el celular).
- `rutinas/mi_dia_detalle.html`: «Volver a mi rutina» → `home#mi-rutina`.
- `ingreso/mi_codigo.html`, `rutinas/bloqueado.html`, `tienda/{cerrada,sin_ficha,no_habilitada}.html`:
  tarjeta centrada (decisión del usuario); ganan el menú.
- Política de privacidad: `main_class` → `contenido--lectura`.

**6. Docs** — `DESIGN.md:370-374` («Dos anchos de contenedor» → cuatro contenedores + `.columnas`/`.con-lateral`; la regla «el
portal del alumno usa `.contenido`» pasa a «listados, paneles y pantallas con formulario del alumno
usan `.contenido--alumno`; carteles y el QR, tarjeta centrada»), `:386-388` y `:529-534`
(navegación: hay menú del alumno), `:587` («Carrito y pedidos siguen angostos» → ya no), secciones
de `.nav-alumno`, `.tabla--pedidos`, `.contenido--lectura`; `ARQUITECTURA.md` «Portal del alumno y
acceso» (`:1998`, la frase «una sola pantalla mobile-first» ~2027) y `:2835-2838`. `ROADMAP.md:257` no se toca.

## Tareas (worktree nuevo `portal-alumno-web`, subagentes, review por tarea; cada una deja la suite verde y commitea `static/css/app.css`)

1. **Contenedores + menú del alumno**: clases de layout, partial del menú, ☰, `x-data` ampliado,
   link en `cerrada.html`. Tests (con el cliente de tests, para que `resolver_match` exista): alumno
   ve Inicio/Mi rutina/Turnos; Tienda sólo con `puede_usar_tienda`; staff no lo ve y conserva el
   suyo; suplantando se ve; con `SUSPENDIDA` y `ALUMNOS_BLOQUEADOS` no aparecen menú ni ☰ (también
   suplantando); `aria-current` por sección; el menú no suma queries: `r = self.client.get(home)`,
   `req = r.wsgi_request`, y `with self.assertNumQueries(0): render_to_string("partials/nav_alumno.html",
   {"user": req.user, "request": req}, request=req)`, con la tienda prendida y apagada (sin banderas
   sólo para el test); `<body>` con `x-data`.
2. **Inicio en 2 columnas**: tests de `contenido--alumno` y `.columnas`, orden del HTML (código antes
   que rutina, rutina antes que cuota, tienda antes que novedades), condiciones intactas (sin vínculo,
   bloqueado, con y sin tienda).
3. **Tienda**: catálogo (cabecera), carrito, `mi_pedido`, `mis_pedidos` (tabla con líneas en
   celular). Tests: estructura de cada pantalla, forms y arrepentimiento intactos.
4. **Comprobante de cuota + otras pantallas + privacidad + docs**: partial `medios_cobro.html`
   (el inicio queda igual), datos de transferencia en el comprobante (con y sin medios, monto 0 no se
   muestra, el form con error los sigue mostrando, aislamiento: no se ven los del otro gimnasio),
   `mis_turnos` (con las dos tarjetas y con una sola), «Volver a mi rutina», privacidad, docs.

Después: review final del branch y **QA en navegador** a 1440 px, 800 px, 390 px y 360 px:
inicio (con/sin tienda, bloqueado, sin vínculo), ☰ abrir/cerrar y link activo, «Mi rutina» baja a
la tarjeta, mi día, turnos, catálogo, carrito, pedido, mis pedidos (celular = líneas), tienda
cerrada, comprobante de cuota (con y sin medios), mi código, privacidad, suplantando, cuenta con
alumnos bloqueados; topbar del alumno a 360 px (marca, «Activar notificaciones», ☰, «Salir»).
Limpiar el service worker local antes de mirar CSS. Sin merge ni push sin OK.

## Verificación

- Suite completa con `.env` sobre el merge, `makemigrations --check` (sin migraciones),
  `npm run build:css`, `git status` limpio.
- QA de arriba; el staff ve todo igual que hoy.
