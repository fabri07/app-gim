# Tienda: galería de hasta 5 fotos por producto (sin videos)

## Contexto

El Hito 1 de la tienda (mergeado y pusheado el 17-10, `main` `83c5d32`) tiene **una sola foto por
producto** (`Producto.foto`). El usuario pidió **4 o 5 imágenes por producto** y **nada de
videos**. Máximo **5**; una es la **principal** (miniatura del staff y primera del catálogo).

Decisiones del usuario (17-10): **se eligen varias fotos de una vez** (un botón «Agregar fotos» con
selección múltiple; en las ya cargadas, «Principal» y «Borrar»); **borrar una foto NO borra el
archivo de R2** (como logos, comprobantes y la foto actual; mantiene la vuelta atrás).

Pasaron **cuatro revisiones adversariales** (corrección 14 + no-regresión 16 + revisión completa
con simplificación + verificación del diseño nuevo contra Django 5.2.15: 6 hallazgos), verificadas
contra el código; este plan ya las incorpora.

## Qué NO se puede romper

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| `Producto.foto` (columna) | **columna muerta** como `Cuota.mes` (`pagos/models.py:155`): se copia a la galería, **no se vacía**, `editable=False` + «No leer de acá». El rollback de Render no revierte migraciones: el código viejo sigue andando | `makemigrations --check`; test de la migración con el molde `import_module` de `tenants/tests.py:313` / `rutinas/tests.py:3435` |
| `ProductoForm` (`tienda/forms.py:30-40`) | en la **misma** tarea que la migración: fuera `"foto"` de `Meta.fields`, su widget y `clean_foto` (con `editable=False` y `foto` en `fields`, Django tira `FieldError` al importar) | suite completa |
| Templates que usan la foto (`catalogo.html:18`, `producto_list.html:24`: `{% if p.foto %}` + `foto_url_cacheable`) | pasan a `{% with f=p.foto_principal %}{% if f %}…f.url_cacheable` y las vistas suman `prefetch_related("fotos")`, en la **misma** tarea que borra `foto_url_cacheable` (si no, las fotos de productos nuevos nunca se ven y ningún test falla) | test con un producto creado por el form nuevo que muestra su foto en las dos pantallas |
| Tests del form de producto (`datos_producto()`, `test_editar_el_nombre_no_pisa_el_stock_reservado`) | **no cambian**: los campos nuevos van en `ProductoForm`, sin management form | los mismos tests, sin tocar |
| `test_crear_con_variantes_y_foto_reescalada`, 3 `FotoTests` | se reescriben para la galería (misma tarea que la migración) | ellos mismos |
| `tienda_foto_producto` + `URLS_SIN_BLOQUEO` + `EXCEPCIONES={"FotoProductoView"}` | mismo `name` y misma clase; ruta nueva `productos/<pk>/fotos/<foto_pk>/` (la lista compara por nombre: no cambia) | `FotoTests` (son los únicos que la cubren) |
| Formset de variantes | no se toca | sus tests |
| Sin N+1 (catálogo y lista del staff) | `prefetch_related` de fotos en `CatalogoView` y `ProductoListView` | **dos** tests de escala: N vs 2N productos con 2 fotos |
| Exportación (conteos fijos, `test_cada_csv_de_cada_carpeta_tiene_datos`) | hoja `tienda_productos_fotos` (`producto__gimnasio`); fuera `foto_archivo` de `tienda_productos`; textos de hoja y LEEME | foto en `_ensuciar_para_exportar` (`tests_exportacion.py:49`) → conteo 1 + nada del otro gimnasio |
| Demo (`vaciar_gimnasio`, siembra sin fotos) | CASCADE con `Producto`; la siembra sigue sin fotos | `tenants/tests.py:3849` reescrito: no hay `FotoProducto` de la demo (hoy `exclude(foto="")` pasaría sin probar nada) |
| Confirmación de borrado (`core/borrado.py::arrastres_de_borrado`) | `verbose_name="foto"` / `"fotos"` | test «se borran también N fotos» |
| Form `hx-boost="false"` + `enctype` | se conserva | test nuevo que lo fija |

## Diseño

**Modelo** `FotoProducto` en `tienda/models.py` (hijo como `Variante`: no `TenantOwnedModel`,
alcance por FK): `producto` (FK CASCADE, `related_name="fotos"`), `imagen`
(`ImageField(upload_to="tienda/productos/")`), `posicion` (`PositiveSmallIntegerField`), `creado`.
`Meta.ordering = ["posicion", "pk"]`, `verbose_name` «foto»/«fotos». `MAX_FOTOS_POR_PRODUCTO = 5`.
`url_cacheable` → `reverse("tienda:tienda_foto_producto", args=[producto_id, pk])` +
`?v=<nombre del archivo sin carpeta>` (uuid de `preparar_foto`; una foto guardada nunca se
reemplaza, así que la URL `immutable` es inmutable de verdad). `Producto.foto_principal` → primer
elemento de `self.fotos.all()` (sale del prefetch). Se borra `Producto.foto_url_cacheable`.

**Migración** `tienda/0002`: `CreateModel` + `RunPython` que crea `FotoProducto(imagen=p.foto.name,
posicion=0)` por cada `Producto.foto` no vacía (asigna el nombre, no toca R2; reverse no-op) +
`AlterField` de `Producto.foto` a `editable=False`.

**Form del staff** — todo dentro de `ProductoForm` (sin segundo formset):
- `fotos_nuevas`: `MultipleFileField(required=False)` con la receta de Django 5.2: `class
  MultipleFileInput(forms.ClearableFileInput): allow_multiple_selected = True`, instanciado con
  `attrs={"accept": "image/jpeg,image/png"}`; `clean_fotos_nuevas` recorre la lista (filtrando
  `None`) y pasa cada archivo por `preparar_foto`, con el nombre del archivo en el mensaje de error.
  `construct_instance`/`save()` ignoran los campos que no son del modelo.
- `borrar_fotos` (`ModelMultipleChoiceField`, casillas) y `principal` (`ModelChoiceField` opcional,
  `RadioSelect`): declarados con `queryset=FotoProducto.objects.none()`; en `__init__` (después de
  `super()`) se les asigna `FotoProducto.objects.filter(producto=self.instance)` si
  `self.instance.pk` (el related manager de un producto sin pk tira `ValueError`), y
  `principal.initial` = la principal actual. Una foto de otro producto no es opción válida.
- `clean()` con ese mismo queryset: existentes − borradas + nuevas ≤ 5 → `add_error("fotos_nuevas",
  "Máximo 5 fotos por producto: borrá alguna antes de agregar.")`; principal marcada para borrar →
  `add_error("principal", …)`. Nunca un error suelto de `clean()` sin campo.
- `form.guardar_fotos(producto)` llamado en el `atomic()` de `_ProductoFormMixin.form_valid`
  después de `form.save()` (el `save()` de un ModelForm no guarda estos campos): borra las marcadas
  (sólo filas, **no** archivos), crea las nuevas en el orden elegido con posición `max+1` (0 si no
  hay ninguna), y si hay `principal` **renumera todo**: la principal a 0 y el resto 1..n en su orden
  actual (`bulk_update`, ≤ 5 filas). En un producto nuevo la primera foto subida es la principal
  (texto de ayuda lo dice).
- Template `producto_form.html`: `{% for campo in form.campos_generales %}` (método del form que
  excluye los tres campos de fotos) con el partial de siempre, más `{{ form.non_field_errors }}`.
  Bloque «Fotos (hasta 5)» a mano: `{% for c in form.borrar_fotos %}` con la miniatura
  (`c.data.value.instance.url_cacheable`), su casilla «Borrar» y el radio «Principal» (la actual con
  `badge--ok`), y el input «Agregar fotos»; `.errors` de cada uno de los tres campos (test que postea
  6 fotos y ve el error, y otro con la principal marcada para borrar).
- Cartel «Las fotos no se guardaron: volvé a elegirlas» cuando la pantalla se redibuja tras un POST
  y `request.FILES.getlist("fotos_nuevas")` no está vacío — **también** si el form era válido y
  fallaron las variantes (el navegador nunca re-envía un `<input type=file>`). Test de los dos
  casos.
- `tienda/fotos.py::preparar_foto`: `imagen.draft("RGB", (2400, 2400))` antes de `exif_transpose`
  (decodifica el JPEG a escala reducida; menos CPU en el worker de Render).

**Vista de la foto** `FotoProductoView`: `get_object_or_404(FotoProducto, pk=foto_pk,
producto_id=pk, producto__gimnasio__activo=True, <es_demo | tienda_habilitada sobre
producto__gimnasio>)`, mismos headers (`immutable`, sin cookie).

**Catálogo del alumno**: clase propia `.producto-fotos` (no `.semanas-carrusel`, que es sólo de
celular y esconde la barra): fila horizontal con scroll-snap dentro de la tarjeta, cada foto al
~85 % del ancho para que asome la siguiente, barra visible desde `sm:`, «N fotos» si hay más de
una, `tabindex="0" role="region" aria-label="Fotos de {nombre}"`, `loading="lazy"`. Una o ninguna
foto: se ve como hoy. Sin JS. **Lista del staff**: miniatura de la principal. Patrón en
`DESIGN.md`.

**Admin**: sin inline de fotos (se cargan por el form, que reescala y valida).

**Exportación**: hoja `tienda_productos_fotos` (`producto_id`, `posicion`, `archivo`).

## Tareas (worktree nuevo `tienda-galeria`, subagentes, review por tarea; cada una deja la suite verde)

1. **Modelo + migración + form del staff + vista de foto + templates mínimos** (juntos para no
   dejar la suite roja ni fotos desaparecidas): `FotoProducto`, migración (test), `ProductoForm`
   con los tres campos y su bloque en el template, `FotoProductoView` nueva, `catalogo.html` y
   `producto_list.html` con `foto_principal` + `prefetch_related("fotos")`, `FotoTests` y `test_crear_…_foto_reescalada`
   reescritos. Tests: subir 3 de una vez, 6 → error visible, borrar (la fila; el archivo queda),
   cambiar principal, foto de otro producto en `borrar_fotos`/`principal` → inválida, 404 de la
   foto con `foto_pk` de otro producto o gimnasio, cartel de fotos no guardadas, `hx-boost`.
2. **Exportación + demo + borrado + escala**: hoja nueva, `3849` reescrito, frase de borrado,
   tests de escala del catálogo y de la lista del staff.
3. **Catálogo deslizable + docs**: `.producto-fotos`, `npm run build:css`; `ARQUITECTURA.md`
   (sección tienda: modelos, punto 12 de la foto con los nombres de test nuevos, R2
   `FotoProducto.imagen` en `:3627` y `:3765`, demo sin fotos), `DESIGN.md`, `ISSUES.md`
   (columna muerta a borrar en una migración futura y que ese día su test se cae; huérfanos ahora
   hasta 5 por producto; costo de servir hasta 5 fotos por producto si Render corre un solo
   worker), `CLAUDE.md:16` (la tienda ya está mergeada).

Después: review final del branch y **QA en navegador**: elegir 5 fotos de celular de una vez
(medir el tiempo del guardado), intentar una 6ª, cambiar la principal, borrar una, forzar un error
en otro campo con fotos elegidas, deslizar en el catálogo en el iframe de 390 px y en escritorio con
mouse, catálogo con 10 productos. Sin merge ni push sin OK.

## Verificación

- Suite completa con `.env` **sobre el merge**, `makemigrations --check`, `npm run build:css`,
  `git status` sin archivos nuevos olvidados.
- QA de arriba + producto sin fotos y con una sola se ven como hoy.
- Después del deploy: `restaurar-demo` a mano (la demo no tiene fotos, debe quedar igual).
