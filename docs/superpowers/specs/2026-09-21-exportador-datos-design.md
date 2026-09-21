# Exportador de datos del gimnasio (ZIP de CSV, habilitado por el dueño del producto)

## Context

Cuando un gimnasio quiere dejar de pagar la app o migrar a otro programa, sus
datos (alumnos, rutinas, pagos, etc.) son suyos y hoy no hay forma de
entregárselos sin un `pg_dump` a mano. Se agrega un botón «Exportar mis datos»
en **Mi gimnasio** que descarga un ZIP con un CSV por entidad.

Decisiones tomadas con el usuario:
- **El botón existe siempre, pero solo funciona cuando el dueño del producto lo
  habilita**: casilla en `/admin/`, vigente **hasta que se destilde**.
- **La cuenta demo (`es_demo`) exporta siempre** (es lo que se muestra para vender).
- **Solo CSV en un ZIP**; comprobantes/logo van como nombre de archivo.
- **Dos carpetas en el ZIP**: `para-excel/` (`;` + coma decimal) y
  `para-importar/` (estándar: `,` + punto decimal). No existe un formato que
  sirva a la vez al Excel argentino y a un importador.

Este plan ya pasó una revisión adversarial; los hallazgos están integrados y
marcados con **[R]**.

## Diseño

### 1. Modelo — `tenants/models.py` + migración `tenants/0012`
Junto a `es_demo` (gestión de plataforma; `GimnasioForm.Meta.fields` es lista
explícita, así que el staff no puede tildárselo solo):
- `exportacion_habilitada = BooleanField(default=False)`.
- `exportacion_ultima_descarga = DateTimeField(null=True, editable=False)`.
  Campo y no modelo de auditoría: un `TenantOwnedModel` más obligaría a tocar
  `vaciar_gimnasio` y `_ensuciar` por una sola fecha. Sirve además de freno (§3).
- Property `puede_exportar` → `es_demo or exportacion_habilitada`. **Único lugar
  de la regla.** Al derivar de `es_demo`, `restaurar_demo` no necesita cambios.

`GimnasioAdmin`: `exportacion_habilitada` en `list_display` + `list_filter`;
`exportacion_ultima_descarga` en `readonly_fields`.

### 2. Generación — `tenants/exportacion.py` (nuevo)
Mismo reparto que `rutinas/pdf.py` ↔ `RutinaAsignadaPdfView`.

- `exportar_gimnasio(*, gimnasio, destino)` escribe el ZIP (`ZIP_DEFLATED`) en un
  file-like. Tabla declarativa `HOJAS`: `(nombre, fn_queryset(gimnasio), columnas)`;
  columna = `(encabezado en castellano, lookup de values_list, tipo)`.
- **[R] Paginado por pk, NO `.iterator()`.** Con `disable_server_side_cursors=True`
  (obligatorio por PgBouncer) el driver trae el resultado ENTERO a memoria antes
  de iterar; `RutinaAsignadaItem` crece sin techo y el contenedor tiene 512 MB.
  SQLite local no lo muestra. Se lee `values_list(...)` en tandas
  `pk__gt=último ORDER BY pk LIMIT 5000`. FK por lookup, nunca acceso por fila;
  `choices` traducidos con un dict por columna.
- **[R] Las dos carpetas salen de la MISMA lectura.** `zipfile` admite un solo
  handle de escritura abierto a la vez, así que por hoja: se escribe
  `para-importar/x.csv` directo al ZIP y `para-excel/x.csv` a un
  `SpooledTemporaryFile`, que se vuelca al ZIP al cerrar la hoja. Una sola
  pasada de queries, memoria acotada.
  - `para-importar/`: `,`, punto decimal, `utf-8` sin BOM, fechas ISO.
  - `para-excel/`: `;`, coma decimal, `utf-8-sig`, fechas ISO.
- **[R] Snapshot consistente**: todo dentro de `transaction.atomic()` y, si
  `connection.vendor == "postgresql"`, `SET TRANSACTION ISOLATION LEVEL
  REPEATABLE READ` como primera sentencia. Sin eso el cron o un alumno pueden
  escribir entre dos hojas y dejar ejercicios que referencian una rutina ausente.
  (PgBouncer en modo transacción mantiene la conexión durante la transacción.)
- Aislamiento: `for_gimnasio(gimnasio)` o cadena de FK del padre para los Item.
- **Anti formula-injection [R acotado]**: se prefija `'` si la celda empieza con
  `=`, `@`, tab o CR, o con `+`/`-` seguido de letra o `(`. Así `+54 9 11…`,
  `-5` y «- Tren superior» quedan intactos. Solo en `para-excel/`; el archivo
  para importar va sin tocar (un apóstrofe ahí es corrupción de datos).
- DateTime con `timezone.localtime`.

**Archivos** (en ambas carpetas) + `LEEME.txt` en la raíz (qué carpeta usar, qué
es cada archivo, cómo se relacionan por `id`, fecha, qué no se incluye y por
qué): `gimnasio`, `alumnos` (ficha completa incl. salud + `usuario` de acceso,
**nunca** password), `cuotas` (`comprobante` = nombre de archivo, no URL
firmada), `medios_de_cobro`, `categorias`, `ejercicios`, `plantillas`,
`plantillas_ejercicios`, `rutinas_asignadas`, `rutinas_asignadas_ejercicios`
(incl. RPE), `dias_entrenados`, `novedades`, `novedades_lecturas`,
`turnos_configuracion`, `turnos_horarios`, `turnos_cupos_excepcion`, `reservas`.

**`EXCLUIDOS = {label: motivo}`**: `SuscripcionPush` (credenciales push),
`RecordatorioEnviado` (dedup interno), `RegistroSuplantacion` (auditoría de la
plataforma, con IP), `Importacion` (JSON interno), `GoogleCalendarCredential`
(**tokens**: `EncryptedTextField` descifra transparente, un `values()` ingenuo
los saca en claro), `ReservaCalendarEvent`. Columnas muertas tampoco.

### 3. Vista — `tenants/views.py::ExportarDatosView`, `gimnasio/exportar/` (`tenants:gimnasio_exportar`)
`StaffRequiredMixin, TenantScopedMixin, View`, **solo POST**.
- `if not gimnasio.puede_exportar: raise PermissionDenied` — **la defensa**; el
  botón gris es solo UX.
- **[R] Freno de 60 s por gimnasio**, contra `exportacion_ultima_descarga` en la
  base (no hay `CACHES`: es locmem por proceso). `render.yaml` arranca gunicorn
  sin `-w` → **un solo worker síncrono**: mientras se arma un ZIP nadie más es
  atendido, y las credenciales de la demo circulan. Se estampa la fecha ANTES de
  generar, con un `UPDATE ... WHERE ultima IS NULL OR ultima < hace_60s` y se
  mira `rowcount` (atómico: cubre el doble click). Rechazo → `messages.warning`
  + redirect a Mi gimnasio. `update()` y no `save()`: no debe tocar
  `modificado`, que versiona logo e ícono PWA.
- `SpooledTemporaryFile(max_size=5 MB)` → `seek(0)` → `FileResponse(as_attachment=True,
  filename="datos-<slug>-<localdate>.zip")`.
- **[R] Aviso al dueño del producto** por mail en cada exportación de una cuenta
  NO demo (`mail_admins`-style a `settings.EXPORTACION_AVISO_EMAIL`, env var
  opcional; `fail_silently=True`). Compensa la habilitación sin vencimiento: si
  exporta alguien que no esperabas, te enterás; y sabés cuándo destildar. Más
  `logger.info`. Agregar la variable a `render.yaml` (hay un test que exige que
  todo lo que lee `settings.py` esté declarado ahí).

### 4. Comando — `manage.py exportar_gimnasio --gimnasio <slug> --salida x.zip` [R]
~20 líneas, molde de `crear_gimnasio` (flags largos, `CommandError`). Reusa
`exportar_gimnasio()`. Es la salida si un gimnasio grande roza los 30 s de
gunicorn: se corre en la Shell de Render sin timeout. No chequea
`puede_exportar` (quien tiene Shell ya tiene la base).

### 5. UI — `templates/tenants/gimnasio_form.html`
Tarjeta «Tus datos» debajo de `.config-layout`, arriba del link de privacidad:
- Habilitado: `<form method="post" hx-boost="false">` + csrf + «Exportar mis datos
  (.zip)». **`hx-boost="false"` obligatorio** (htmx se traga la descarga).
- No habilitado: `<button type="button" disabled class="boton-secundario">` +
  «Tus datos son tuyos. Para descargarlos, pedinos que habilitemos la
  exportación». **[R]** El canal sale de `settings.SOPORTE_CONTACTO` (env var
  opcional, vía context processor existente en `tenants/context_processors.py`);
  sin ella el texto no promete un canal. NO usar `gimnasio.contacto` (es el del
  gimnasio).
- `styles/input.css`: `disabled:opacity-60 disabled:cursor-not-allowed` en
  `.boton-secundario`; `npm run build:css` y commitear `static/css/app.css`.
- `templates/tenants/privacidad.html`: párrafo de portabilidad de datos.

### 6. Tests — `tenants/tests_exportacion.py` (nuevo)
- **[R] Fixture propio `_ensuciar_para_exportar`** que envuelve `_ensuciar` (sin
  modificarlo: lo comparten los tests de `vaciar_gimnasio`) y agrega lo que NO
  crea: `NovedadLeida`, `RutinaAsignadaDiaCompletado`, un RPE cargado y una
  `GoogleCalendarCredential` con un `refresh_token` reconocible. Sin esto, «cada
  CSV tiene ≥1 fila» falla en dos hojas y el test de secretos pasa **sin que
  exista ningún token**.
- Permisos: sin casilla → 403 por POST directo; con casilla → 200 + ZIP válido;
  demo sin casilla → 200; alumno → 403; anónimo → login; GET → 405; el campo no
  es editable posteando a `GimnasioForm`.
- Freno: segundo POST dentro de los 60 s → redirect con mensaje, sin ZIP;
  pasados los 60 s (reloj congelado con `patch("django.utils.timezone.now")`) → 200.
- **Aislamiento**: dos gimnasios con `marca` distinta; ningún byte del ZIP de A
  contiene la marca de B (cubre los Item vía FK).
- **Cobertura, el par**: (a) todo `TenantOwnedModel` (`_modelos_tenant_owned`)
  está en `HOJAS` o `EXCLUIDOS`; (b) tras el fixture, cada CSV de cada carpeta
  tiene ≥1 fila de datos.
- **Secretos**: el ZIP no contiene el `refresh_token`, el `endpoint` push ni el
  hash de password del fixture.
- **[R] Escala**: con `TAMANIO_TANDA` parcheado a 2, las queries crecen por
  tanda y no por fila (comparar dos tamaños; nunca `assertNumQueries` fijo).
- Formato: `para-excel/cuotas.csv` trae `;` y `1500,00`; `para-importar/` trae
  `,` y `1500.00`; ambas con la misma cantidad de filas.
- `SimpleTestCase` del saneado: `=cmd`→`'=cmd`; `+54 9 11…`, `-5` y «- Tren
  superior» intactos; `-@x`/`+SUM(` prefijados; `para-importar/` nunca se sanea.
- Mail: sale en cuenta real, no sale en demo (`locmem` ya es automático en tests).
- Comando: genera un ZIP válido; slug inexistente → `CommandError`.
- Cada test de regresión se verifica **fallando sin el fix**.

### 7. Docs
- Spec en `docs/superpowers/specs/2026-09-21-exportador-datos-design.md`, sección
  «Exportador de datos» en `CLAUDE.md`, entrada en `ISSUES.md` con los riesgos
  aceptados (habilitación sin vencimiento por pedido explícito, compensada con el
  mail; comprobantes fuera del ZIP; generación síncrona con un solo worker).
- Nota en `docs/superpowers/specs/2026-08-12-subcuentas-staff-design.md`: cuando
  exista `Perfil.nivel`, exportar es solo del dueño.
- **Un `TenantOwnedModel` nuevo ahora va a TRES lugares**: `vaciar_gimnasio`,
  `_ensuciar`, y `HOJAS`/`EXCLUIDOS`.

## Flujo de trabajo
Rama `feat/exportador-datos` desde `main`, TDD. `git status` al final: no pueden
quedar untracked `exportacion.py`, la migración `0012`, el comando,
`tests_exportacion.py` ni el spec. Sin merge ni push hasta que el usuario lo pida.

## Verificación
1. `python manage.py test tenants`, suite completa, `makemigrations --check`.
2. **[R] Medición a escala antes de dar por bueno**: `sembrar_demo --alumnos 200
   --meses 24` en un gimnasio local, `exportar_gimnasio` con `/usr/bin/time -l`:
   anotar segundos y pico de memoria contra 30 s / 512 MB (SQLite subestima: es
   un piso, no una garantía).
3. `runserver`: sin casilla → botón gris y `curl -X POST` autenticado → 403;
   tildar en `/admin/` → descargar; abrir `para-excel/cuotas.csv` con doble click
   (columnas separadas, acentos, montos numéricos, teléfonos intactos) y revisar
   `para-importar/`; segundo click inmediato → mensaje del freno;
   `exportacion_ultima_descarga` visible en `/admin/`; gimnasio `es_demo` sin
   casilla → descarga.
4. Al verificar UI: desregistrar el service worker tras `build:css` y reiniciar
   `runserver` si el template no cambia.
5. Post-deploy (cuando el usuario lo pida): confirmar en Render cuántos workers
   corren (`WEB_CONCURRENCY`) y cargar `EXPORTACION_AVISO_EMAIL` /
   `SOPORTE_CONTACTO`; probar la descarga en `/g/demo`.

## Desvíos respecto de este diseño, decididos al implementar

- **Techo `MAX_FILAS_WEB = 60_000`** (no estaba): la medición a escala dio
  ~160 µs por fila, así que un gimnasio grande no entra en los 30 s de
  gunicorn. Por encima del techo la vista no genera nada y avisa por mail con
  el comando listo.
- **`SOPORTE_CONTACTO` va por el contexto de `GimnasioUpdateView`**, no por un
  context processor global: solo lo usa esa pantalla.
- **`_formatear` devuelve el par de las dos carpetas en una sola pasada**:
  formatear una vez por carpeta era el 70% del tiempo total.
