# Tienda: «orden» opcional, código de barras y variantes por características

## Contexto

El dueño del producto habilitó la tienda en la cuenta de prueba (18-10) y, al cargar productos,
pidió tres cosas:

1. **«Orden» aparece como obligatorio** en categoría y en producto. No lo es: sólo decide la
   posición en el catálogo y en la base ya vale 0 por defecto; es obligatorio porque el ModelForm
   hereda `required` de un `PositiveSmallIntegerField` sin `blank`.
2. **Código de barras**: el que trae el producto o, si no tiene, uno inventado por la app.
   **Decisión del usuario:** se guarda y se busca/escanea en la lista de productos (lector USB =
   teclado + Enter), sin imprimir etiquetas.
3. **Variantes con desplegables** (talle, color, gramos/kilos, sabor) en vez de texto libre.
   **Decisión del usuario:** hasta **2 características por producto**; se tildan los valores y se
   crean todas las combinaciones, cada una con su stock y su código.

Hoy (`tienda/models.py:229-248`) `Variante` tiene `nombre` libre (60, único por producto),
`stock`, `activa`. Todo el resto (catálogo, carrito, `ItemPedido.nombre` vía `str(variante)`,
exportación, demo, admin) consume **`Variante.nombre`**.

**Idea central:** `Variante.nombre` se queda y pasa a ser **calculado** («M · Negro») a partir de
dos valores nuevos; ningún consumidor cambia.

Revisión adversarial: primera ronda (2 pasadas) 3 bloqueantes (contrato del POST del formset
contradictorio, `VarianteForm.save` perdía los campos nuevos, el escaneo en serie podía sumar el
código como stock), 10 medios y 10 menores. Segunda ronda sobre el plan corregido: 2 bloqueantes
(la fila única sin tocar no se guardaba → producto sin variantes; un error en las características
del form producía errores falsos en todas las filas), 5 medios (intercambio de nombres → 500,
migración que vaciaba nombres con significado, regla «exactamente una activa», valores escondidos
que igual se envían, `post` sin especificar) y 9 menores. Tercera ronda: 4 bloqueantes (el
renombre temporal con NUL que Postgres rechaza; el mismo renombre no cubría borrados ni filas
sin cambios —los dos se resuelven **eliminando la constraint de nombre único**, ver §4—;
`Variante.clean()` corría también en el formset del staff con falsos duplicados; filas nuevas no
«vivas» que igual se guardaban), 3 medios y 2 menores. Cuarta ronda: 1 bloqueante (filas nuevas
con errores descartadas en silencio), 3 medios (test de la constraint, duplicados falsos con una
característica inválida, `MultipleObjectsReturned` en la demo) y 5 menores. **Quinta ronda,
pasada completa: nada bloqueante**; 4 medios y 8 menores, incorporados («Ajustes de la 5.ª
ronda»). Todo incorporado abajo.

## Qué NO se puede romper

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| Catálogo y carrito del alumno (`catalogo.html:38-48`, `variante_unica` en `views.py:491-519`) | nada: leen `nombre`; un producto sin características tiene una variante con `nombre=""` (botón directo, como hoy) | `tests_vistas_alumno.py` sin tocar |
| Snapshot del pedido (`services.py:314`, `str(variante)`) | nada | `tests_services.py` |
| `tienda/tests_modelos.py:47-49` (`test_nombre_unico_por_producto`, espera `IntegrityError`) | se reescribe: ahora dos variantes con el mismo nombre se pueden crear en la base (la unicidad es del formset) | ese test reescrito |
| `UniqueConstraint(producto, nombre)` | **se elimina** en 0008 (ver §4); duplicados detectados en el formset sobre **todas** sus filas (activas, inactivas y las con ventas que se conservan) y en el inline del admin → mensaje | tests de duplicados (staff y admin) + rotación de nombres |
| Variantes existentes en producción (Hito 1 vivo) | migración 0008 (ver §5); `nombre` sólo cambia en productos con una sola variante «» o «Único», que pasan a `""`; los demás nombres se conservan como `valor_1` | test de migración (una sola «Único», una sola «Chocolate», varias, mixto, 60 caracteres) |
| Producto con su única variante desactivada (posible hoy) | sigue guardándose (regla «como mucho una activa») | test |
| Edición que intercambia o reusa nombres entre variantes | sin constraint en la base, no hay choque intermedio | test A→B→C y «quitar L + M→L» (también contra Postgres) |
| Variante con ventas (se desactiva, no se borra) | igual; las reglas de valores aplican sólo a las activas | test |
| Stock (`stock_inicial` sólo en el alta; después ajuste ±) | igual | tests de `ajustar_stock` sin tocar |
| `ListadoDeProductosEscalaTests` | la búsqueda filtra el mismo queryset, sin queries por fila | el test + variante con `?q=` |
| Los ~24 usos de `datos_producto`/`datos_edicion` (`tests_vistas_staff.py:40-50,323-336` + overrides en 71-72, 89, 102, 329, 480) | **el contrato cambia** (`nombre` deja de ser campo del form): los helpers se actualizan en un solo lugar (`caracteristica_1` + `variantes-N-valor_1`); ningún test se borra | la suite |
| `test_variante_sin_nombre_no_muestra_dos_puntos_sueltos` (`:807`, regex sobre 900 caracteres de la fila) | el código bajo el nombre no puede dejar `:` sueltos | ese test |
| Sección general de `producto_form` (`ProductoForm.campos_generales`, `forms.py:103`) | las características se excluyen de ahí y se dibujan en «Variantes» | test del HTML |
| Admin `VarianteInline` (`admin.py:14-25`) | `nombre` pasa a `readonly_fields`; se editan valores y código | test del admin |
| Catálogo del alumno | **no** muestra el código | test |
| POST de categoría/producto con `orden` | se acepta; sin `orden` → 0 | tests existentes + nuevos |
| Exportación (`tenants/exportacion.py:313-340`) | columnas nuevas; ningún `TenantOwnedModel` nuevo (no toca los TRES lugares) | `tests_exportacion.py` |
| Demo (`tenants/demo.py:514-593`) | características seteadas explícitamente después del `get_or_create` (un `sembrar_demo` sobre una demo ya migrada no queda en «Opción») | tests de demo |
| hx-boost | `producto_form.html` ya va con `hx-boost="false"`; la búsqueda es GET boosteada (200); el JS se re-ejecuta en cada swap → IIFE con guarda | QA (ida, vuelta, «atrás») |
| Service worker | el `.js` es archivo nuevo | QA en navegador |

## Diseño

### 1. «Orden» opcional (sin migración)
`CategoriaProductoForm` y `ProductoForm`: en `__init__`, `self.fields["orden"].required = False`,
label «Orden en el catálogo», help «Opcional. Los de número más bajo se muestran primero; vacío
es 0, y entre iguales van por orden alfabético.» (molde `ejercicios/models.py:39-41`;
`ordering = ["orden", "nombre"]`); `clean_orden` devuelve `0` si viene vacío.

### 2. Código de barras (`Variante.codigo`)
- `codigo = CharField(max_length=32, blank=True, default="", db_default="")`, normalizado a
  **mayúsculas** y sin espacios en `Variante.save()` (así también desde el admin), no sólo en el
  form.
- `tienda/codigos.py::codigo_interno(pk)` = EAN-13 `"20"` + `pk` a 10 dígitos + verificador. El
  rango GS1 20–29 es «uso interno del comercio»: ningún producto de fábrica lo trae; al salir del
  `pk`, es único sin carreras.
- **Se inventa solo** en `Variante.save()`: después de `super().save()`, si `codigo == ""` →
  `self.codigo = codigo_interno(self.pk)` (en memoria también, así un `save(update_fields=…)`
  posterior no lo pisa con `""`) + `filter(pk=…).update(codigo=…)`. Borrarlo al editar →
  se regenera. Ningún `assertNumQueries` envuelve la creación de variantes (verificado) y no hay
  `bulk_create` de `Variante`.
- **Validación** (formset, una sola query `codigo__in` para todas las filas): letras, dígitos y
  guion, 4–32; único por gimnasio sin mayúsculas (variantes de productos del gimnasio, activas o
  no, **excluidas las del propio formset**, así intercambiar dos códigos no da falso error) y
  único entre filas del formset (no cuentan las filas que de verdad se borran; sí las con ventas
  que se conservan aunque se posteen «Quitar»); un código de 13 dígitos que empieza con `20` sólo vale si es el
  interno de esa misma variante («Los códigos que empiezan con 20 los reserva la app»). El
  gimnasio llega por `form_kwargs` (también en el GET y en el redibujo). **Riesgo aceptado** (va a
  `ISSUES.md` y `ARQUITECTURA.md`): sin constraint en la base (`Variante` no tiene `gimnasio`), dos
  staff guardando a la vez el mismo código manual podrían duplicarlo; la búsqueda muestra todos.
- Migración 0007 llena `codigo_interno(pk)` en las existentes; las que cree la versión vieja
  durante el deploy quedan con `""` (la lista muestra «—») y se completan al guardar.
- Lista de productos: código bajo el nombre de la variante (sin `:` sueltos). Exportación
  `tienda_variantes.codigo`; admin `VarianteInline.codigo`. El catálogo del alumno no lo muestra.
- En el formulario de producto, el lector manda Enter: `static/js/tienda_variantes.js` (nace en
  T1 con sólo esto; T2 le suma el generador) hace `preventDefault()` del Enter en los inputs de
  código y pasa al campo siguiente (si no, el escaneo envía el form a medio cargar).

### 3. Buscar / escanear en la lista de productos
- `producto_list.html`: `<form method="get" role="search">` con `q` («Buscar por nombre o código;
  podés escanear con el lector»), `autocomplete="off"`, `inputmode="search"`.
- `ProductoListView.get_queryset` con `q` (strip, ≤ 60):
  - coincidencia **exacta** (`codigo__iexact`, con `filter`, no `get`) con variantes del
    gimnasio → esos productos y `destacadas` = esos pks: filas resaltadas y **el input de ajuste
    de la primera con `autofocus`; el buscador va sin `autofocus` en esa respuesta** (htmx y el
    navegador enfocan el primer `[autofocus]`);
  - si no, `Q(nombre__icontains=q) | Q(variantes__nombre__icontains=q) |
    Q(variantes__codigo__icontains=q)` + `.distinct()` (ordering de columnas propias: sin
    problema en Postgres).
  - Sin resultados: «No hay productos que coincidan con «q»» + «Ver todos».
- **Escaneo en serie seguro**: el ajuste (`VarianteAjustarView`) redirige a
  `reverse("tienda:productos") + "?" + urlencode({"ajustada": pk})` cuando el POST trae
  `desde_busqueda=1`: la fila ajustada queda resaltada **sin** autofocus y el **buscador vacío con
  `autofocus`**, así el próximo escaneo cae en el buscador y nunca en «Sumar o restar». Sin
  `desde_busqueda`, redirige como hoy (`AjusteStockTests`, `tests_vistas_staff.py:157-172`, no
  cambian). `ajustada` se parsea con `isdigit()`; inválido se ignora (nunca 500). Inputs de ajuste
  con `autocomplete="off"`.
- Sin `q` ni `ajustada`, el buscador lleva `autofocus` (en celular abre el teclado al entrar:
  aceptado, se mira en la QA).

### 4. Variantes por características
**Modelo**:
- `Producto.caracteristica_1`, `caracteristica_2`: `CharField(30, blank, default="",
  db_default="")`, texto libre (nada de `TextChoices` global, lección de `CategoriaEjercicio`).
- `Variante.valor_1`, `valor_2`: `CharField(60, blank, default="", db_default="")`.
- `Variante.save()`: si hay algún valor, `nombre = " · ".join(valores no vacíos)` **antes** de
  `super().save()` (la demo usa `str(variante)` enseguida). Sin valores no toca `nombre` (tests
  que crean con `nombre=`). El form, en cambio, asigna siempre (también `""`).
- `Variante.clean()` (corre en todo `ModelForm`, staff y admin, vía `_post_clean` →
  `full_clean`): **sólo** el largo del nombre calculado ≤ 60, como error de `valor_1` (campo de los
  dos forms; nunca sobre `nombre`, que no es campo → `ValueError`). Sin queries. Así ni el admin da
  `DataError` de Postgres (largos validados en Python, regla de CLAUDE.md).
- Admin: `VarianteInline.form` = form chico cuyo `save()` asigna siempre el nombre calculado
  (como el del staff; si no, vaciar los dos valores dejaría el «M» viejo) y valida el formato del
  código y el prefijo `20` con el mismo validador de `tienda/codigos.py`; `VarianteInline.formset`
  = subclase de `BaseInlineFormSet` que compara los nombres calculados entre sus filas (salteando
  las marcadas `DELETE` y las con errores; el inline muestra todas las variantes del producto).
  **Aceptado**: la unicidad del código por gimnasio no se chequea en el admin (lo usa sólo el
  superadmin).

**Formularios** (`tienda/forms.py`):
- `ProductoForm`: `caracteristica_1/2` en `fields`, excluidas de `campos_generales`, con
  `clean`: la 2 sin la 1 → error en la 2; iguales sin mayúsculas/acentos → error en la 2.
- `VarianteForm`: `fields = ["valor_1", "valor_2", "codigo", "activa"]` (**`nombre` sale del
  form**); recibe `gimnasio`; `save()` asigna `variante.nombre` calculado siempre (también `""`
  sin valores) y, al editar, `update_fields=["nombre", "valor_1", "valor_2", "codigo", "activa"]`;
  `codigo` vacío → lo completa `Variante.save()`.
- `BaseVarianteFormSet`: recibe `caracteristicas=(c1, c2)` y `caracteristicas_validas` (bool);
  `extra=1` fijo (como hoy había 2: una fila vacía siempre disponible, también sin JS; el
  generador **reusa** esa fila vacía antes de clonar), `validate_max=True` («Son más de 50
  variantes»; el tope cuenta también las inactivas con ventas: documentado).
  - **Fila única sin características** (bloqueante de la 2.ª ronda): si no hay `caracteristica_1`
    y la instancia no tiene variantes, la primera fila nueva se construye con
    `empty_permitted=False` y su `has_changed()` devuelve `True`, así una fila sin tocar (stock y
    código vacíos) igual se guarda como la variante `nombre=""`, `stock=0`. Nunca se guarda un
    producto sin variantes.
  - **Valores de una característica vacía se limpian** (`valor_2` si no hay `caracteristica_2`;
    los dos si no hay ninguna) en `VarianteForm.clean()` (ver más abajo), antes de las reglas:
    borrar una característica no deja errores sobre campos que el JS esconde. Los duplicados que
    eso produzca se informan igual.
  - «Vivas» = filas no borradas (las con ventas posteadas «Quitar» cuentan) con pk o con algún
    valor, código o stock, más la fila única del caso anterior.
- **Reglas** (sobre activas vivas): sin `caracteristica_1` → **como mucho una activa** y al menos
  una fila («Sin características el producto tiene una sola variante. Poné una, como Talle, para
  cargar varias»; se puede seguir guardando un producto con su única variante desactivada, como
  hoy); con `caracteristica_1` → cada activa con `valor_1`; con `caracteristica_2` → también
  `valor_2` (error en el campo de la fila: «Completá el Color o desactivá esta fila», que cubre
  agregar una segunda característica a un producto que ya tenía variantes). **Al menos una viva,
  siempre** (con o sin características, como hoy). El largo ≤ 60 vive sólo en `Variante.clean()`.
  Duplicados entre filas **activas efectivas** (`cleaned_data["activa"]` y no «`DELETE` con
  ventas») y contra las inactivas **sin** ventas: «Ya existe «M» desactivada: tildá Activa» si
  la otra está inactiva y el nombre no es vacío; si no, «Hay dos variantes «M»». Dos inactivas
  nunca chocan, ni una activa contra una inactiva **con ventas** (la base ya no tiene la
  constraint, el catálogo sólo muestra activas y los pedidos van por `pk`): así sacar una
  característica a un producto con variantes vendidas nunca deja un callejón sin salida.
  - **Si `caracteristica_1` o `caracteristica_2` tiene error en el form del producto**
    (`caracteristicas_validas=False`: `add_error` la saca de `cleaned_data`): las filas **no**
    limpian valores (bandera por `form_kwargs`), no se marca `fila_unica`, y se saltean las reglas
    de valores **y los duplicados**; sólo corren código y largo. Nada se guarda en ese POST, así que
    no hay riesgo; y nunca aparecen errores falsos en todas las filas.
- **Se elimina la constraint `variante_unica_por_producto`** (3.ª ronda): con nombres calculados,
  el guardado fila por fila de Django (y el borrado dentro del mismo loop) chocaba con ella al
  intercambiar o reusar nombres (A «M»→«L» con B «L»→«XL»; quitar L y renombrar M→L), y el
  renombre temporal necesitaba un NUL que Postgres rechaza. Un nombre repetido no rompe ningún dato
  (stock, pedidos y snapshots van por `pk`): es un problema de carga, y lo frenan el formset (staff)
  y el formset del inline del admin. **Riesgo aceptado** (a `ISSUES.md`): dos staff editando el
  mismo producto a la vez podrían dejar dos «M»; se arregla desde el mismo formulario.
- `VarianteForm.clean()` (no el formset: `_post_clean` ya copió los valores a la instancia
  cuando corre el `clean` del formset) limpia `valor_2`/`valor_1` de una característica vacía; las
  características le llegan a cada fila por `form_kwargs`.
- `VarianteForm.has_changed()` = `super().has_changed() or self.fila_unica or (self.instance.pk
  and hasattr(self, "cleaned_data") and nombre_calculado != self.instance.nombre)` (el `hasattr`
  porque `full_clean` del formset lo llama antes del `clean`): una fila cuyo nombre cambia sin que
  cambie lo posteado (característica borrada, filas viejas) igual se guarda.
- `BaseVarianteFormSet._es_viva(form)`: función **pura** del estado de la fila (`cleaned_data`,
  `fila_unica`, `DELETE`), usada por `_should_delete_form` y por `clean` (Django llama
  `_should_delete_form` en `full_clean`/`is_valid`/`deleted_forms`, antes del `clean` del formset:
  «viva» no puede ser algo que calcule `clean`).
- `BaseVarianteFormSet._should_delete_form`: además de lo de hoy (con ventas → desactivar), una
  fila **nueva, sin errores** y no viva se descarta (`form.instance.pk is None and not form.errors
  and not self._es_viva(form)`): si no, una fila extra con sólo «Activa» destildada, o una
  sobrante con el valor limpiado, se guardaría como `nombre=""`. **Nunca se descarta una fila con
  errores** (bloqueante de la 4.ª ronda: un código «AB» o un stock «-1» en una fila nueva se
  perdía en silencio con «Producto guardado»).
- La fila única **no** dibuja «Quitar», y `_es_viva` la excluye si igual llega con `DELETE`
  (entonces corre «al menos una fila»).
- **Cableado de la fila única**: `BaseVarianteFormSet.get_form_kwargs(index)` agrega
  `fila_unica=True` y `empty_permitted=False` sólo si `index is not None and index ==
  self.initial_form_count() == 0` y no hay `caracteristica_1` (`_construct_form` aplica estos
  kwargs después de los suyos; `empty_form` pasa `None` y queda sin la bandera).
  `VarianteForm.__init__` saca `fila_unica` y `caracteristicas` antes de `super().__init__()`.
- Cada campo nuevo con su línea de error vía `partials/campo_form.html`.
  `test_errores_del_formset_se_ven` (`tienda/tests_vistas_staff.py:97`) se extiende a los campos
  nuevos (el chequeo automático de `core/tests.py:98` sólo ve `campo=form.X`, no las filas); las
  aserciones de «mismo nombre» (`:104`, `:483`) se reescriben al mensaje nuevo.

**Vista** (`_ProductoFormMixin`, `views.py:142-187`): un helper `_formset(data=None, files=None,
form=None)` arma el formset con `form_kwargs={"gimnasio": …}`, `caracteristicas`,
`caracteristicas_validas`, y se usa en `get_context_data` y en `post`. **`post` propio y
explícito** (el mixin va antes que `UpdateView` en el MRO y reemplaza el `post` base):
`self.object = self.get_object()` en la edición y `None` en el alta → `form = self.get_form()` →
`form.instance.gimnasio = …` (hoy en `views.py:173`) → `form.is_valid()` → formset con las
características de `form.cleaned_data` (etiquetas del redibujo: `form["caracteristica_1"].value()`)
→ `formset.is_valid()` → si los dos pasan, `form_valid(self, form)` **con la firma de hoy** (en el
MRO va primero `TenantScopedMixin.form_valid(form)`, `core/mixins.py:44-48`; el formset viaja en
`self._formset`; atomic: producto, fotos, formset, `activa` de las con ventas); si no, redibujo con
los errores **de los dos** en el primer intento (hoy el formset sólo se valida si el form pasó). En
el POST el formset usa `instance=form.instance`. `get_object` ya filtra por gimnasio
(`TenantScopedMixin.get_queryset`).

**Template** (`producto_form.html`, sección «Variantes»):
- «Característica 1» y «Característica 2 (opcional)» con `<datalist>` de las claves de
  `SUGERENCIAS`, con su línea de error.
- Filas: el servidor **siempre** dibuja `valor_1` y `valor_2` con su línea de error (sin JS el
  error nunca queda sobre un campo ausente); el JS sólo los esconde con `hidden` cuando la
  característica está vacía y nunca si el campo tiene error. Etiquetas desde el servidor: nombre de
  la característica (instancia en la edición, datos POST en el redibujo; «Valor 1/2» sólo en un
  alta sin JS); el JS actualiza **sólo el nodo de texto** del `<label>` (no borra el `*` ni el
  `.sr-only`). Más «Código de barras» (help «Vacío: la app inventa uno»), «Stock inicial» (nuevas),
  «Activa», «Quitar» (salvo con ventas).
- **Generador** (`static/js/tienda_variantes.js`): por característica, casillas con las
  sugerencias que correspondan (match sin mayúsculas/acentos; `SUGERENCIAS` vía `json_script`) +
  «Otros valores» separados por coma. «Agregar combinaciones» arma el producto cartesiano,
  saltea las combinaciones ya presentes **activas o inactivas**, reusa la fila extra vacía y
  clona `{{ formset.empty_form }}` para el resto (dibujado con el mismo parcial dentro de un
  `<template>`, reemplazando `__prefix__`) y actualiza `TOTAL_FORMS`; si filas existentes + nuevas
  pasan de 50 → aviso y no agrega nada. Script en IIFE con guarda de presencia del contenedor, sin
  `DOMContentLoaded`, listeners sobre los elementos (molde `plantilla_detail.html:265-270`): htmx
  lo re-ejecuta en cada visita boosteada sin redeclarar nada.
- Comentarios de template de varias líneas con `{% comment %}`; si hay clases nuevas
  (fila destacada, grilla de casillas) en `styles/input.css`, `npm run build:css`.

**Sugerencias** (`tienda/variantes.py`, sólo ayuda, nunca validación): «Talle» XS S M L XL XXL
XXXL; «Talle numérico» 34–46 pares; «Color» Negro Blanco Gris Azul Rojo Verde Rosa Beige;
«Peso» 100 g, 250 g, 500 g, 1 kg, 2 kg, 3 kg, 5 kg; «Sabor» Chocolate Vainilla Frutilla Cookies
Banana Neutro.

**Admin**: `VarianteInline` con `valor_1`, `valor_2`, `codigo`, `nombre` de sólo lectura;
`ProductoAdmin` con las características. **Exportación**: `tienda_productos` +
`caracteristica_1/2`; `tienda_variantes` + `valor_1`, `valor_2`, `codigo`. **Demo**: proteína
«Sabor», remera «Talle», creatina sin características (`nombre=""`), características seteadas
explícitamente después del `get_or_create` y variantes buscadas por `valor_1=…, valor_2=""` (sin
la constraint, buscar sólo por `valor_1` podría dar `MultipleObjectsReturned` si un visitante de la
demo agregó un Color: los productos sobreviven a `sembrar_demo --borrar`, `demo.py:519-521`).

**Catálogo del alumno**: sin cambios (select con «M · Negro»).

**Ajustes de la 5.ª ronda** (sin bloqueantes):
- `con_ventas` (el set de `variante_id` con `ItemPedido`, una sola query que hoy arma
  `get_context_data`) se calcula en `post` y se le pasa al formset; `_should_delete_form` y
  `_es_viva` lo usan en vez de `items.exists()` (nada de N+1; `_es_viva` queda pura).
- `caracteristicas_validas=False` saltea **todas** las reglas del formset salvo formato/unicidad
  del código y largo (incluidas «como mucho una activa» y «al menos una»).
- `VarianteForm.__init__`: si la instancia tiene `valor_1 == ""` y `nombre != ""` (creada por el
  código viejo durante el deploy), `initial["valor_1"] = nombre`, así el staff no la retipea.
- El generador reusa primero las filas existentes con `valor_1` vacío (el «» de un producto al que
  se le agrega Talle) y después la extra; recién ahí clona.
- Demo: `filter(...).first()` + crear si no hay (aceptado en el docstring que una demo tocada por
  un visitante puede quedar con una «M» suelta; lo normal es `restaurar_demo`).
- `?ajustada=`: `int()` dentro de `try/except ValueError` (`"²".isdigit()` es `True`).
- Detalles: `VarianteForm.__init__` saca `gimnasio`, `caracteristicas`, `caracteristicas_validas`
  y `fila_unica` (default `False`); `BaseVarianteFormSet.__init__` saca los suyos; `has_changed`
  lee con `cleaned_data.get`; «Son más de 50 variantes» en `default_error_messages["too_many_forms"]`
  de la subclase; `post` distingue alta/edición por `"pk" in self.kwargs`.
- `ISSUES.md`: el reverse de 0008 (`AddConstraint`) falla si ya hay nombres repetidos (limpiarlos
  antes de un rollback de esquema); con una fila inválida, `validate_max` puede contar también las
  filas quitadas (cosmético).
- Tests: `test_sin_ninguna_variante_es_error_visible` (`tests_vistas_staff.py:70-77`) se reescribe
  en dos: sin características y todo vacío → una variante `nombre=""` desactivada; con «Talle» y
  sin filas → «al menos una variante». Más: sacar Talle con S vendida inactiva + M → guarda (sin
  callejón); `?ajustada=²` → 200; variante vieja con `nombre="S"` y `valor_1=""` → el form
  precarga «S».

### 5. Migraciones (`default` + `db_default`; reverse noop; funciones copiadas dentro)
- **0007** (T1): `codigo` + llenado `codigo_interno(pk)` en lotes con `bulk_update`.
- **0008** (T2): `RemoveConstraint("variante_unica_por_producto")`, características y valores +
  llenado por producto:
  - una sola variante con nombre `""` o «Único»/«Unico» (sin mayúsculas ni acentos; lo sugería el
    texto de ayuda viejo) → `nombre = ""` sin característica (el alumno pasa de un select de una
    opción al botón directo);
  - cualquier otro producto con alguna variante con nombre (también una sola «Chocolate» o
    «1 kg»: el dato no se pierde) → `caracteristica_1 = "Opción"` y `valor_1 = nombre` en cada una
    con nombre;
  - **mixto** («» + «Chocolate»): igual que el anterior; la «» activa queda sin valor y el staff la
    completa o desactiva al editar (mensaje de fila). Caso testeado y documentado.

### 6. Docs
`ARQUITECTURA.md` «Tienda del gimnasio» (nombre calculado, código interno 20…, unicidad en Python,
búsqueda/escaneo en serie, por qué no hay constraint de nombre); `ISSUES.md` (por qué «orden» era
obligatorio; riesgos aceptados: código manual y nombre de variante duplicados por ediciones
simultáneas; una pestaña de producto abierta durante el deploy da error al guardar y se recarga).

## Tareas (subagentes + review por tarea; suite verde y commit en cada una)

T1. **«Orden» opcional + código + búsqueda**: forms; `codigo`, `tienda/codigos.py`,
   `Variante.save()` del código, validación con gimnasio (`form_kwargs` en GET/redibujo/POST),
   `VarianteForm.save` con `codigo` en `update_fields`, migración 0007, lista con código,
   búsqueda, escaneo en serie, exportación, admin, Enter del lector en el form. Tests: categoría y
   producto sin `orden` → 0 y sin `*`; `codigo_interno` contra un EAN-13 conocido; nueva sin código
   → interno; editar y vaciar → regenera; código manual editado se guarda; duplicado (otro
   producto, inactiva, dos filas) → error visible; intercambiar dos códigos → OK; otro gimnasio →
   permitido; `20…` ajeno → error; mayúsculas; búsqueda por nombre/variante/código parcial (en
   minúsculas: SQLite no ignora mayúsculas fuera de ASCII); exacto → filas destacadas, autofocus
   en el ajuste y no en el buscador; ajuste con `desde_busqueda` → `?ajustada=` con el buscador en
   foco y sin autofocus en el ajuste; `?ajustada=abc` → 200; aislamiento; escala con `q`;
   catálogo sin código; código en minúsculas desde el admin se guarda en mayúsculas; el HTML del
   form carga `tienda_variantes.js`; migración.
T2. **Características**: campos + migración 0008, `tienda/variantes.py`, forms, vista con
   validación conjunta, template, JS, admin, exportación, demo, docs. Tests: helpers actualizados
   (los ~24 tests pasan); sin características = una variante, `nombre=""`, catálogo como hoy;
   Talle → «S», «M»; Talle + Color → «M · Negro»; falta valor → error en la fila; 2 sin 1 y 1 = 2 →
   error; segunda característica agregada a un producto con variantes → error guiado; duplicado
   contra inactiva → mensaje «tildá Activa» sin 500; con ventas + «Quitar» posteado a mano no
   produce `IntegrityError`; > 60 y > 50 → errores; con ventas inactiva no exige valores; errores
   del formset se ven junto con los del form en el primer intento; HTML: características fuera de
   la sección general, `valor_1/2` siempre dibujados, `<template>` con `__prefix__`, JSON de
   sugerencias; admin con `nombre` de sólo lectura (inline: duplicado → error del formset;
   `Variante.clean`: > 60 → error en `valor_1`; vaciar los valores recalcula el nombre; código
   «AB» → error); `test_nombre_unico_por_producto` reescrito; migración 0008 (una sola «» / «Único» → `nombre=""`; una sola «Chocolate» →
   «Opción»; varias; mixto; 60 caracteres); exportación; demo re-sembrada con características.
   Además (2.ª ronda): alta sin características con stock y código vacíos → exactamente una
   variante `nombre=""`, `stock=0`; «2 sin 1» con filas que traen `valor_2` → un único error, el
   de la característica; rotación A→B→C de nombres → guarda sin 500; producto sin características
   con su única variante desactivada → se guarda; borrar la característica 2 → los `valor_2` se
   limpian y no hay errores ocultos; `TOTAL_FORMS=50` (el generador reusó la fila extra) →
   guarda, y 51 → «Son más de 50 variantes».
   Además (3.ª ronda): «quitar L + renombrar M→L» guarda; fila extra con sólo «Activa» destildada
   no crea variante; Talle → generar (filas **con stock**) → borrar Talle → error «como mucho una
   activa», sin 500; borrar la característica con filas viejas → los nombres se recalculan
   (`has_changed`); la migración 0008 saca la constraint.
   Además (4.ª ronda): fila extra nueva con sólo `codigo="AB"` (o stock `-1`, o `valor_1` de 61)
   → error visible y ninguna variante creada; «1 = 2» con filas «M · Negro» y «M · Blanco» → un
   único error (en la característica), sin duplicados falsos; la fila única no dibuja «Quitar» y
   con `DELETE` posteado → «al menos una fila»; demo re-sembrada sobre una remera con «M · Negro»
   agregado no da `MultipleObjectsReturned`.

QA en el navegador (local, tienda habilitada): categoría sin orden; producto sin variantes;
remera Talle × Color con el generador (6 combinaciones, stocks, un código propio, el resto
inventados); editar y agregar un talle (sólo las nuevas); agregar una segunda característica a un
producto existente; volver al form con «atrás» y con un link (el generador sigue andando, sin
errores en consola); buscar por nombre y por código pegado + Enter, ajustar, siguiente «escaneo»
cae en el buscador; catálogo del alumno con «M · Negro»; celular 390 px (form y lista).

## Verificación
- Suite completa con `.env` y la de `tienda` + `tenants` contra Postgres en Docker (migraciones,
  `icontains`/`distinct`), `makemigrations --check`, `git status` sin archivos nuevos sueltos
  (el `.js` y las migraciones commiteados).
- Post-deploy: nada a mano; los productos con varias variantes quedan con «Opción» y el dueño la
  renombra («Talle», «Sabor»).
- Sin merge ni push sin OK del usuario.
