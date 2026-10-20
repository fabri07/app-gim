# Moderación de la tienda — plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendado)
> o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan
> casillas (`- [ ]`) para el seguimiento.

**Objetivo:** que nada nuevo se venda ni se vea en la tienda sin que el superadmin lo apruebe, que
el superadmin pueda bajar un producto puntual (cancelando sus pedidos sin pagar) y que el alumno
pueda denunciar.

**Arquitectura:** el estado de moderación es un dato de plataforma sobre `Producto` y
`FotoProducto`, con columnas `db_default` para que el deploy no se caiga. Todo lo que se vende pasa
por una sola `q_vendible()`. Las transiciones viven en un módulo nuevo, `tienda/moderacion.py`, y
son `filter(...).update()` condicionales. La cola es una pantalla nueva del panel de plataforma.
No hay IA, ni servicio externo, ni cron.

**Stack:** Django 5.2, templates + htmx (`hx-boost` global), SQLite en dev y Postgres 16 en
producción.

**Spec:** `docs/superpowers/specs/2026-10-19-moderacion-tienda-design.md` (commit `cc757bc`).
Leerla junto con este plan. Antes de tocar la tienda, leer también `ARQUITECTURA.md` § «Tienda
del gimnasio (`tienda/`)».

**Worktree:** `/Users/fabriziosola/app_gim/.claude/worktrees/moderacion-tienda`, rama
`moderacion-tienda`. Ya tiene `.env`, y sin él hay 5 tests rojos que no son regresión (ver
memoria `facts_suite_app_gim_no_hermetica`).

## Restricciones globales

- Todas las columnas nuevas llevan `db_default`, para que los INSERT de la versión vieja durante
  el `migrate` del `buildCommand` sigan andando. El default de `moderacion` es `pendiente`.
- Los productos y las fotos que ya existen quedan aprobados por migración de datos.
- Ningún servicio externo nuevo: la suite no sale a la red y los mails van al backend locmem.
- Un `TenantOwnedModel` nuevo va a los TRES lugares en el mismo commit: `vaciar_gimnasio`,
  `_ensuciar` y `EXCLUIDOS`.
- Fechas del dominio con `timezone.localdate()`. Los instantes con `timezone.now()`.
- Bajo `hx-boost`, un 4xx es un click mudo: los errores van con `messages` + redirect, y las
  pantallas de cartel responden 200.
- Cada campo de formulario tiene su línea de error, porque `.errorlist` no tiene estilo.
- Nada de N+1: el catálogo y el contador del panel tienen una cantidad de queries constante.
- Textos con voseo y castellano rioplatense.
- La respuesta pública de una foto aprobada no puede llevar `Set-Cookie` ni registrar actividad
  (los dos asserts de `tienda/tests_vistas_staff.py:286-302`). Que lleve `Vary: Cookie` NO es
  evitable ni es un defecto: `tenants/middleware.py:40` lee la sesión en cada request desde
  siempre.
- **No se toca `vencer_pedidos`** (cron + listado). La cancelación por baja es una función nueva.
- El estado de la base manda sobre la instancia en memoria: toda transición de moderación es un
  `filter(...).update()` condicional, nunca un `if self.object.moderacion == ...` antes de
  escribir.
- Si se toca `styles/input.css`: `npm run build:css` antes de commitear.

## Decisiones del plan que la spec dejaba abiertas (para que el usuario las revise)

1. **Pago tardío de Mercado Pago.** «Bajar» cancela con `cancelado_por_staff=True`, y
   `pagos_mp._aplicar` ya manda a `REVISAR` un aprobado que llega sobre un pedido así
   (`tienda/pagos_mp.py:379`). Pero `_re_reservar` (`tienda/pagos_mp.py:259`) revive a PAGADO un
   pedido VENCIDO o cancelado por el alumno sin mirar la moderación: ahí se agrega que, si algún
   ítem no cumple `q_vendible`, el pedido va a REVISAR (la misma rama que «sin stock»). Tarea 6.
2. **«Pedidos sin pagar» son sólo los `PENDIENTE_PAGO`**, como en `vencer_pedidos`. Un pedido
   `EN_REVISION` (el alumno ya subió el comprobante, así que la plata probablemente se movió) no
   se toca: lo resuelve el staff, como cualquier pedido con plata.
3. **El superadmin aprueba sólo lo que vio.** La confirmación de «Aprobar» manda las fotos y las
   denuncias que mostró, más el `modificado` del producto. Una foto que entró después sigue
   pendiente. Si el texto cambió mientras la pantalla estaba abierta, aprobar no hace nada y avisa
   «El producto cambió mientras lo mirabas: revisalo de nuevo».
4. **La cola se agrupa por producto, en dos secciones:** «Productos nuevos para aprobar» y «A la
   venta con algo para revisar». Cada tarjeta lleva marcas: texto cambiado, N fotos nuevas, N
   denuncias. El contador del inicio cuenta productos y sale en UNA query.
5. **El aviso al staff de una foto rechazada** se guarda en `Producto.moderacion_motivo` y se
   muestra debajo del badge en el listado. Se limpia cuando se aprueba el producto.
6. **«Rechazar foto» también vale para una foto ya aprobada.** Las copias que ya bajaron los
   navegadores no se pueden borrar: es un riesgo aceptado y se anota en `ISSUES.md`.
7. **`MigracionDeReferenciaExternaTests` ya migra a `leaf_nodes()`** en su `tearDown`
   (`tienda/tests_modelos.py:407-413`), así que esa fila de la tabla de la spec no requiere
   cambios.
8. **Con 3 denuncias el producto sale de la venta, pero sus pedidos sin pagar siguen vivos**
   (decisión del usuario, 19-10). Se cancelan sólo si el superadmin lo baja.
9. **La cuenta demo queda fuera de la cola** (decisión del usuario, 19-10). No manda mail, no suma
   al contador y se ve en una sección aparte y colapsada de la cola. El visitante igual ve
   «Esperando aprobación».
10. **`DenunciaProducto.alumno` es `SET_NULL`, no PROTECT.** Con PROTECT o CASCADE, la pantalla
    de borrar alumno (`core/borrado.py:23-37`, `alumnos/views.py:449`) le mostraría al staff «tiene
    1 denuncia» (delata al denunciante) y además PROTECT impediría borrarlo y rompería
    `borrar_demo` (`tenants/demo.py:648`).
11. **Una foto es pública sólo si está aprobada Y su producto está APROBADO.** Un producto que
    vuelve a pendiente por denuncias deja de servir sus fotos en público.

## Foco de revisión

1. **Foto aprobada vista con una sesión iniciada.** Sale con `Cache-Control: public,
   max-age=31536000, immutable`, sin `Set-Cookie` y sin registrar actividad. Test en la Tarea 4.
2. **Foto no aprobada vista por un anónimo.** El 404 lleva `Cache-Control: no-store`, para que
   ninguna caché guarde el «no existe» y la foto quede oculta después de aprobarla. Test en la
   Tarea 4.
3. **El staff sube una foto o edita el texto con la cola abierta.** Aprobar no aprueba lo que el
   superadmin no vio. Tests en la Tarea 6.
4. **Un pedido pendiente con dos productos, uno de los cuales se baja.** Se cancela el pedido
   entero y vuelve el stock de TODOS sus ítems. Los pedidos `EN_REVISION`, `PAGADO` y
   `ENTREGADO` del mismo producto no cambian. Test en la Tarea 6.
5. **Un producto bajado o pendiente que ya estaba en el carrito.** El carrito lo saca y el
   checkout de lo que queda funciona, sin ningún 500. Test en la Tarea 3.
6. **Checkout y «Bajar» al mismo tiempo.** No puede quedar un pedido vivo de un producto bajado.
   Test en la Tarea 6.
7. **El superadmin aprueba mientras el staff edita el texto.** El cambio no queda a la venta sin
   revisar. Test en la Tarea 5.

## Qué NO se puede romper (revisado contra el código el 19-10, a pedido del usuario)

| Lo que hoy funciona | Qué le hace el plan | Cómo queda protegido |
|---|---|---|
| Productos y fotos que hoy están a la venta | La migración de datos los deja aprobados. Mantienen las mismas URL y la caché `immutable` | Test de migración (0008 → 0009) |
| Deploy: la versión vieja atiende mientras corre `migrate` | Todas las columnas nuevas llevan `db_default`. Un INSERT viejo cae en «pendiente» (lo seguro) | Test de INSERT crudo sobre Postgres |
| Carritos abiertos de los alumnos | Sus productos ya están aprobados: no cambia nada | `q_vendible` + test del carrito con un producto bajado |
| Checkout y stock | Sólo suma una revalidación dentro del mismo `atomic`. Si algo no es vendible, se deshace todo | Suite de `tests_services` y de compra sin tocar |
| `vencer_pedidos` (cron + listado) | **No se toca.** La cancelación por baja es una función nueva que copia el patrón; el refactor `_pasar_en_lote` queda **fuera del plan** | `tests_services` y `tests_comandos` sin cambios |
| Mercado Pago de la tienda (apagado) | Sólo cambia el caso nuevo «producto ya no vendible». Todo lo demás sigue igual | `tests_mercadopago_*` verdes sin modificarlos |
| Fotos públicas y su caché | La aprobada sale igual que hoy, sin `Set-Cookie` ni actividad registrada. El service worker no cachea `/tienda/` (`static/js/sw.js:42`) | Los tests existentes de `tests_vistas_staff.py:286-302` + los nuevos |
| Borrar un alumno | Con `SET_NULL` la denuncia no aparece en la pantalla ni lo bloquea | Test nuevo |
| Borrar un producto sin denuncias ni baja | Sigue igual | Los tests existentes de borrado |
| Exportación de datos | Columnas iguales. El LEEME suma una línea con un motivo nuevo | Test de cobertura de `HOJAS`/`EXCLUIDOS` |
| Demo y `restaurar_demo` cada 6 h | Siembra aprobado, borra las denuncias en orden y no manda mails | Tests de la demo + `--borrar` con una denuncia |
| Panel de plataforma (inicio) | Suma 1 query constante | Test de queries |
| Bloqueos (gimnasio suspendido, alumno sin pagar) | Las fotos siguen en `URLS_SIN_BLOQUEO`. El modo privado niega el acceso a un staff sin acceso | Test nuevo |
| Suite actual | Los fixtures pasan a usar los helpers aprobados. Ningún test cambia lo que verifica | Línea de base de la Tarea 0 = mismo total de tests + los nuevos, todos verdes |

**El único cambio de comportamiento a propósito para los gimnasios reales:** un producto nuevo, una
foto nueva o un cambio de texto en uno aprobado esperan la aprobación del superadmin. Lo ya
publicado no cambia.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `tienda/models.py` | Campos de moderación, `q_vendible`, `DenunciaProducto` |
| `tienda/migrations/0009_moderacion.py`, `0010_denunciaproducto.py` | Columnas + migración de datos (lo existente queda aprobado); el modelo de denuncias |
| `tienda/moderacion.py` (nuevo) | Transiciones (aprobar, bajar, fotos, denuncia), cola, contador y aviso por mail |
| `tienda/services.py` | `q_vendible` en `_variantes_vendibles`; `cancelar_pedidos_de_producto` |
| `tienda/views.py` | Catálogo, `FotoProductoView` con dos modos, form de staff, borrar, `DenunciarView` |
| `tienda/forms.py` | `DenunciaForm` |
| `tienda/admin.py` | Campos de moderación de sólo lectura, filtro y `DenunciaProducto` de sólo lectura |
| `tienda/testing.py` | `crear_producto()` y `crear_foto()` aprobados para los tests |
| `templates/tienda/denunciar.html`, `templates/tienda/email/aviso_moderacion.txt` (nuevos) | Pantalla de denuncia y mail |
| `templates/tienda/catalogo.html`, `producto_list.html`, `producto_form.html`, `configuracion_form.html` | Denunciar, badges y cláusula |
| `plataforma/views_moderacion.py` (nuevo), `plataforma/urls.py`, `plataforma/views.py` (`InicioView`) | Cola, foto del superadmin, acciones y contador |
| `templates/plataforma/moderacion.html`, `moderacion_confirmar.html` (nuevos), `inicio.html`, `gimnasio_detalle.html` | Pantallas |
| `tenants/demo.py`, `tenants/tests.py` (`_ensuciar`), `tenants/exportacion.py` | Los tres lugares + demo aprobada |
| `tienda/tests_moderacion.py`, `plataforma/tests_moderacion.py` (nuevos) | Tests nuevos |
| `ARQUITECTURA.md`, `CLAUDE.md`, `ISSUES.md` | Documentación |

---

### Tarea 0: línea de base

- [ ] **Paso 1:** en el worktree, `git status -sb` limpio sobre `cc757bc`.
- [ ] **Paso 2:** `.venv/bin/python manage.py test 2>&1 | tail -5`. Anotar la cantidad de tests y
  que salen todos OK: es la línea de base contra la que se compara cada tarea. Si el worktree no
  tiene `.venv`, usar el de `/Users/fabriziosola/app_gim/.venv`.

---

### Tarea 1: estados de moderación, migración y helpers de test

**Archivos:**
- Modificar: `tienda/models.py` (`ConfiguracionTienda`, `Producto`, `FotoProducto`)
- Crear: `tienda/migrations/0009_moderacion.py`
- Modificar: `tienda/testing.py`
- Test: `tienda/tests_moderacion.py` (nuevo)

**Interfaces:**
- Produce:
  - `Producto.Moderacion` (TextChoices) con `PENDIENTE = "pendiente", "Esperando aprobación"`,
    `APROBADO = "aprobado", "Aprobado"` y `BAJADO = "bajado", "Dado de baja por la plataforma"`.
  - `Producto.moderacion`: `CharField(max_length=10, choices, default=PENDIENTE,
    db_default=PENDIENTE, editable=False)`.
  - `Producto.cambio_pendiente`: `BooleanField(default=False, db_default=False, editable=False)`.
  - `Producto.moderacion_motivo`: `CharField(max_length=300, blank=True, default="",
    db_default="", editable=False)`.
  - `Producto.moderacion_desde`: `DateTimeField(null=True, blank=True, editable=False)`.
  - `FotoProducto.aprobada`: `BooleanField(default=False, db_default=False, editable=False)`.
  - `ConfiguracionTienda.ultimo_aviso_moderacion`: `DateTimeField(null=True, blank=True,
    editable=False)`.
  - `q_vendible(prefijo: str = "") -> Q`, definida después de `Producto`. Devuelve
    `Q(**{f"{prefijo}publicado": True, f"{prefijo}moderacion": Producto.Moderacion.APROBADO})`.
  - `tienda.testing.crear_producto(**campos) -> Producto`: `Producto.objects.create`, con
    `moderacion=APROBADO` salvo que se pase otro valor.
  - `tienda.testing.crear_foto(**campos) -> FotoProducto`: igual, con `aprobada=True`.

`editable=False` deja los campos fuera de `ProductoForm` sin tocar `Meta.fields`, y fuera del form
del admin. La Tarea 5 los muestra como `readonly_fields`.

- [ ] **Paso 1: tests que fallan**, en `tienda/tests_moderacion.py`:
  - `EstadosTests.test_un_producto_nuevo_nace_pendiente`: `Producto.objects.create(...)` da
    `moderacion == "pendiente"` y `cambio_pendiente is False`.
  - `EstadosTests.test_una_foto_nueva_nace_sin_aprobar`.
  - `EstadosTests.test_q_vendible_exige_publicado_y_aprobado`: con cuatro productos (publicado ×
    aprobado), `Producto.objects.filter(q_vendible())` trae sólo el publicado y aprobado. Y
    `Variante.objects.filter(q_vendible("producto__"))` trae sólo su variante.
  - `VentanaDelDeployModeracionTests.test_un_insert_viejo_cae_en_pendiente`: un INSERT crudo a
    `tienda_producto` sin las columnas nuevas, y otro a `tienda_fotoproducto`, siguiendo el molde
    de `tienda/tests_modelos.py:321`. Al releer: `moderacion == "pendiente"`, `cambio_pendiente
    False`, `moderacion_motivo == ""` y `aprobada False`.
  - `MigracionDeModeracionTests(TransactionTestCase)`, con el molde de
    `MigracionDeCaracteristicasTests` (`tienda/tests_variantes.py:502`), desde
    `0008_variantes_por_caracteristicas` hasta `0009_moderacion` y con `tearDown` a
    `leaf_nodes()`. `test_lo_existente_queda_aprobado`: un producto con foto creado en 0008 queda,
    después de migrar, en `moderacion == "aprobado"` y con su foto `aprobada is True`.
- [ ] **Paso 2:** `.venv/bin/python manage.py test tienda.tests_moderacion`. Tiene que FALLAR con
  `FieldError`/`AttributeError`: los campos no existen.
- [ ] **Paso 3: los campos, `q_vendible` y los helpers**, con las firmas de arriba. Comentar en el
  modelo por qué `db_default` y por qué el default es `pendiente`: ver la spec, § Estados.
- [ ] **Paso 4: la migración.** Correr `makemigrations tienda --name moderacion`. Después,
  agregarle a mano, como ÚLTIMA operación de `operations` (con los `AddField` antes: si no,
  Postgres tira «pending trigger events» por las FK diferidas), una `RunPython` que hace `Producto.objects.update(moderacion="aprobado")` y
  `FotoProducto.objects.update(aprobada=True)` con los modelos históricos y
  `reverse_code=RunPython.noop`.
- [ ] **Paso 5:** `.venv/bin/python manage.py test tienda.tests_moderacion` → PASS.
- [ ] **Paso 6:** suite completa. Tiene que seguir igual que la línea de base, porque todavía nada
  filtra por moderación.
- [ ] **Paso 7: commit.** `feat(tienda): estados de moderación de productos y fotos (db_default
  pendiente, lo existente aprobado)`.

---

### Tarea 2: `DenunciaProducto` y los tres lugares

**Archivos:**
- Modificar: `tienda/models.py`
- Crear: `tienda/migrations/0010_denunciaproducto.py`
- Modificar: `tenants/demo.py::vaciar_gimnasio`, `tenants/tests.py::_ensuciar` y
  `tenants/exportacion.py::EXCLUIDOS`
- Test: `tienda/tests_moderacion.py`

**Interfaces:**
- Produce `DenunciaProducto(TenantOwnedModel)`:
  - `Motivo` (TextChoices): `DROGAS "drogas" «Drogas o medicamentos sin receta»`, `SEXUAL "sexual"
    «Contenido o servicios sexuales»`, `ARMAS "armas" «Armas»`, `TRATA "trata" «Trata o
    explotación de personas»`, `ESTAFA "estafa" «Estafa o producto falso»` y `OTRO "otro"
    «Otro»`.
  - `Estado` (TextChoices): `ABIERTA "abierta"` y `RESUELTA "resuelta"`.
  - Campos:
    - `producto = FK(Producto, PROTECT, related_name="denuncias")`;
    - `alumno = FK("alumnos.Alumno", SET_NULL, null=True, related_name="denuncias_tienda")`
      (decisión 10);
    - `motivo = CharField(max_length=20, choices=Motivo)`;
    - `detalle = TextField(blank=True, max_length=500)`;
    - `estado = CharField(max_length=10, choices=Estado, default=ABIERTA)`.
    - `creado` viene de `TimeStampedModel`.
  - `Meta.constraints`: `UniqueConstraint(fields=["producto", "alumno"],
    condition=Q(estado="abierta"), name="denuncia_abierta_unica")`.
  - `clean()`: `validar_gimnasio_de(self.gimnasio, producto=self.producto, alumno=self.alumno)`.

- [ ] **Paso 1: tests que fallan.**
  - `DenunciaModeloTests.test_dos_abiertas_del_mismo_alumno_chocan`: `IntegrityError` dentro de
    `transaction.atomic()`.
  - `DenunciaModeloTests.test_una_resuelta_no_impide_otra_abierta`.
  - `DenunciaModeloTests.test_aislamiento_por_gimnasio`: `for_gimnasio(otro)` no trae la del
    primero.
  - `DenunciaModeloTests.test_borrar_un_alumno_que_denuncio_no_lo_delata`: el staff abre
    `alumnos` → borrar del alumno con una denuncia. Ni la confirmación ni el mensaje contienen
    «denuncia». El POST lo borra y la denuncia queda con `alumno=None`.
  - `test_sembrar_demo_borrar_con_una_denuncia_no_revienta`, en `tenants/tests.py` y con el molde
    de `tenants/tests.py:4222`.
  - Los tests que ya cubren los tres lugares tienen que fallar solos al agregar el modelo:
    `tenants.tests_exportacion` (cobertura de `HOJAS`/`EXCLUIDOS`) y los de `vaciar_gimnasio`.
    Correrlos después del Paso 3 para confirmarlo, ANTES del Paso 4.
- [ ] **Paso 2:** `.venv/bin/python manage.py test tienda.tests_moderacion` → FAIL, porque el
  modelo no existe.
- [ ] **Paso 3: el modelo y su migración.** `makemigrations tienda --name denunciaproducto`.
- [ ] **Paso 4: los tres lugares.**
  - `EXCLUIDOS["tienda.DenunciaProducto"] = "registros internos de revisión de la plataforma"`.
    No puede repetir el motivo de `RegistroSuplantacion`, porque el LEEME imprime cada uno.
  - En `vaciar_gimnasio`: `DenunciaProducto.objects.for_gimnasio(gimnasio).delete()` justo
    después de `Pedido...delete()` y antes de `alumnos.delete()` y de `Producto...delete()`
    (`producto` es PROTECT). Agregar también el import local.
  - `borrar_demo` no necesita cambios, porque la FK al alumno es SET_NULL. Lo fija un test.
  - En `_ensuciar`: una `DenunciaProducto` abierta sobre el producto y el alumno que el fixture ya
    crea.
- [ ] **Paso 5:** `.venv/bin/python manage.py test tienda tenants` → PASS. Mutación de control:
  sacar la línea de `vaciar_gimnasio` tiene que poner rojo un test de `tenants`. Después,
  restaurarla.
- [ ] **Paso 6: commit.** `feat(tienda): DenunciaProducto (en vaciar_gimnasio, _ensuciar y
  EXCLUIDOS)`.

---

### Tarea 3: sólo se vende lo aprobado

**Archivos:**
- Modificar: `tienda/services.py:151-155` (`_variantes_vendibles`) y `tienda/views.py:629-637`
  (`CatalogoView`)
- Modificar: los tests que crean productos para venderlos (ver Paso 5)
- Test: `tienda/tests_moderacion.py`

**Interfaces:**
- Consume: `q_vendible`, `crear_producto`, `crear_foto` (Tarea 1).
- Produce:
  - `_variantes_vendibles(gimnasio)`, que ahora filtra
    `Variante.objects.filter(q_vendible("producto__"), producto__gimnasio=gimnasio, activa=True)`;
  - un catálogo que prefetchea `Prefetch("fotos", queryset=FotoProducto.objects.filter(aprobada=True))`.

- [ ] **Paso 1: tests que fallan** (`VentaTests`, con alumno logueado y tienda publicada como
  `_BaseAlumno`):
  - `test_un_producto_pendiente_no_aparece_en_el_catalogo`.
  - `test_un_producto_bajado_no_aparece_en_el_catalogo`.
  - `test_no_se_puede_agregar_al_carrito_uno_pendiente`: el POST a `tienda:carrito` muestra
    `services.MENSAJES["producto_no_disponible"]` o el mensaje que hoy da una variante no
    vendible. Leerlo en `agregar_al_carrito`.
  - `test_uno_bajado_que_ya_estaba_en_el_carrito_se_cae_y_el_resto_se_compra` (foco de revisión
    5): carrito con A y B, bajar B con `update()`, checkout. Se crea el pedido sólo con A, sin
    500.
  - `test_la_foto_sin_aprobar_no_se_muestra_en_el_catalogo`: producto aprobado con una foto
    aprobada y otra sin aprobar. El HTML trae sólo la `url_cacheable` de la aprobada y no dice
    «2 fotos».
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** cambiar los dos filtros. Al terminar, `grep -rn "publicado=True"
  tienda/*.py | grep -v tests_` sólo puede encontrarlo dentro de `q_vendible`.
- [ ] **Paso 4:** `.venv/bin/python manage.py test tienda.tests_moderacion` → PASS.
- [ ] **Paso 5: arreglar los fixtures.** Correr la suite completa. Cada test que se pone rojo
  porque su producto o su foto ahora nacen pendientes pasa a `crear_producto(...)` /
  `crear_foto(...)`. Lugares conocidos:
  - `tienda/tests_*.py`, empezando por `_BaseAlumno`, `CatalogoGaleriaTests._fotos`,
    `CatalogoEscalaTests._cargar` y `FotoTests`/`GaleriaDeFotosTests` de `tests_vistas_staff`;
  - `plataforma/tests_tienda.py`;
  - `tenants/tests.py`.
  No cambiar los tests de migraciones, que usan modelos históricos, ni los que verifican el
  default.
  **Hacerlo también con los que siguen VERDES:** `grep -rn "Producto.objects.create\|FotoProducto"`
  en los tests de catálogo, checkout, fotos y escala. Un test negativo («no aparece») o de escala
  (`CatalogoEscalaTests`, con el catálogo vacío en los dos casos) pasa ahora por el motivo
  equivocado. Cada uno tiene que seguir probando lo que dice su nombre.
- [ ] **Paso 6:** suite completa → mismo total que la línea de base + los nuevos, todo OK.
- [ ] **Paso 7: commit.** `feat(tienda): el catálogo y el checkout venden sólo lo aprobado`.

---

### Tarea 4: `FotoProductoView` con dos modos

**Archivos:**
- Modificar: `tienda/views.py:571-592`
- Test: `tienda/tests_moderacion.py`

**Interfaces:**
- Consume: `FotoProducto.aprobada` y `Producto.moderacion`.
- Produce: la misma URL `tienda:tienda_foto_producto`, con estas respuestas:
  - foto aprobada y producto APROBADO (decisión 11): igual que hoy;
  - si no, para un staff del mismo gimnasio (`perfil.rol == Perfil.Rol.STAFF`) que no esté sin
    acceso (gimnasio suspendido, con el mismo criterio que usa el middleware de plataforma), o
    para un superusuario activo: 200 con `Cache-Control: private, no-store`. El perfil se lee con
    `getattr(request.user, "perfil", None)`: un usuario sin perfil da 404, nunca 500;
  - si no: 404 con `Cache-Control: no-store`, devuelto como `HttpResponseNotFound`, no con
    `Http404`, para poder ponerle la cabecera.

**El modo público no cambia de comportamiento:** la ruta sigue en `URLS_SIN_BLOQUEO` y la vista
no escribe la sesión. El camino privado sí puede leer `request.user`. Actualizar el docstring: ya
no es cierto que «no se exige `publicado`»; ahora se exige la moderación.

- [ ] **Paso 1: tests que fallan** (`FotoModosTests`):
  - `test_aprobada_es_publica_e_immutable_sin_cookie`. Con una sesión iniciada: 200, con
    `Cache-Control == "public, max-age=31536000, immutable"`, `list(r.cookies.keys()) == []` y sin
    `ActividadDiaria` nueva (molde de `tests_vistas_staff.py:292-302`). NO verificar `Vary`.
  - `test_sin_aprobar_da_404_no_store_al_anonimo`: 404, y `no-store` aparece en `Cache-Control`.
  - `test_sin_aprobar_la_ve_el_staff_del_gimnasio_sin_cache`: 200 y `Cache-Control == "private,
    no-store"`.
  - `test_sin_aprobar_no_la_ve_el_staff_de_otro_gimnasio`: 404.
  - `test_sin_aprobar_no_la_ve_un_alumno_del_gimnasio`: 404.
  - `test_de_un_producto_bajado_da_404_aunque_este_aprobada`.
  - `test_de_un_producto_pendiente_por_denuncias_da_404_aunque_este_aprobada`.
  - `test_un_usuario_sin_perfil_recibe_404`.
  - `test_el_staff_de_un_gimnasio_suspendido_recibe_404`.
  - `test_el_superadmin_ve_una_sin_aprobar`: 200 y `private, no-store`.
  - Usar `InMemoryStorage` con un PNG de `tienda.testing.PNG`, igual que `FotoTests` de
    `tests_vistas_staff.py`.
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4: arreglar los fixtures de las fotos.** `FotoTests` y `GaleriaDeFotosTests`
  (`tests_vistas_staff.py:276-360`) arman la foto con `FotoProducto(...)` + `imagen.save(...)`:
  ponerles `aprobada=True`, aunque estén en verde. Después,
  `.venv/bin/python manage.py test tienda` → PASS. Mutación de control: servir público con sólo
  `foto.aprobada` (sin mirar el producto) tiene que poner rojo el test de «pendiente por
  denuncias».
- [ ] **Paso 5: commit.** `feat(tienda): la foto sin aprobar no es pública ni cacheable`.

---

### Tarea 5: el staff (guardar sin pisar la moderación, badges, borrar, admin)

**Archivos:**
- Modificar: `tienda/views.py` (`_ProductoFormMixin.form_valid` y `post`, `ProductoUpdateView`,
  `ProductoBorrarView`)
- Modificar: `templates/tienda/producto_list.html` (columna del badge, línea 59) y
  `templates/tienda/producto_form.html` (miniaturas)
- Modificar: `tienda/admin.py`
- Test: `tienda/tests_moderacion.py`

**Interfaces:**
- Consume: los campos de la Tarea 1 y `DenunciaProducto` (Tarea 2).
- Produce, en `tienda/views.py`:
  - `CAMPOS_EDITABLES_PRODUCTO = [*ProductoForm._meta.fields, "modificado"]`, la lista de
    `update_fields` de la edición;
  - `_ProductoFormMixin.entro_a_la_cola: bool`, que se pone en `True` cuando hay alta, un
    cambio de texto en un producto aprobado o fotos nuevas en un producto aprobado. Lo leen
    los mensajes de esta tarea y el aviso por mail de la Tarea 8.

**Comportamiento (copy fijo):**
- Alta: `form.save()` normal; el producto queda pendiente. Mensaje: `messages.info` «Producto
  guardado. Lo revisamos y lo publicamos en el día.»
- Edición:
  - `producto = form.save(commit=False)` y después
    `producto.save(update_fields=CAMPOS_EDITABLES_PRODUCTO)`, dentro del `atomic` que ya existe.
  - Si `{"nombre", "descripcion"} & set(form.changed_data)` no está vacío, SIEMPRE se corre
    `Producto.objects.filter(pk=..., moderacion=APROBADO).update(cambio_pendiente=True,
    moderacion_desde=ahora)`, dentro del mismo `atomic` que el `save(update_fields)` y sin mirar
    `self.object.moderacion` (puede estar vieja: foco 7). Si tocó la fila, va el mensaje «Producto guardado. El cambio queda en revisión de la
    plataforma; mientras tanto sigue a la venta.».
  - Si subió fotos nuevas a un producto aprobado: «Las fotos nuevas se ven en la tienda cuando la
    plataforma las apruebe.».
  - Si no pasó nada de eso, el «Producto guardado.» de siempre.
- Producto bajado: `ProductoUpdateView` (GET y POST) hace `messages.error` «Este producto fue dado
  de baja por la plataforma y no se puede editar.» y redirect a `tienda:productos`.
- `ProductoBorrarView` se niega si el producto está `bajado` o tiene alguna `DenunciaProducto`.
  Reusa el `bloqueos` del contexto con «denuncias o una baja de la plataforma» y en el POST da
  `messages.error`, igual que con las ventas.
- Listado, en la celda del badge:
  - pendiente: `<span class="badge badge--alerta">Esperando aprobación</span>`;
  - bajado: `badge--riesgo` «Dado de baja por la plataforma»;
  - aprobado con `cambio_pendiente`: `badge--alerta` «Cambio en revisión»;
  - debajo, si hay `moderacion_motivo`: `<p class="texto-suave">{{ p.moderacion_motivo }}</p>`.
  - El badge Publicado/Oculto sigue al lado.
- Form: junto a cada miniatura sin aprobar, `<span class="badge badge--alerta">En revisión</span>`.
- Admin:
  - `ProductoAdmin.readonly_fields = ("moderacion", "cambio_pendiente", "moderacion_motivo",
    "moderacion_desde")`;
  - `list_filter` suma `"moderacion"`;
  - `list_display` suma `"moderacion"`;
  - `DenunciaProductoAdmin`, con `has_add_permission`, `has_change_permission` y
    `has_delete_permission` en `False`.

- [ ] **Paso 1: tests que fallan** (`StaffModeracionTests`):
  - `test_el_alta_queda_pendiente_y_avisa`: POST de alta válido (usar el payload de
    `tests_vistas_staff`). El producto queda pendiente y aparece el mensaje.
  - `test_editar_el_nombre_de_uno_aprobado_lo_manda_a_revision_y_sigue_a_la_venta`:
    `cambio_pendiente True` y `moderacion == "aprobado"`.
  - `test_editar_el_precio_no_lo_manda_a_revision` (este ya pasa antes de implementar: no se le
    exige FAIL).
  - `test_si_lo_aprueban_mientras_se_edita_el_cambio_queda_en_revision` (foco 7): el producto
    está pendiente al cargar la vista. Parchear `ProductoForm.save` para que primero haga
    `update(moderacion="aprobado")` y después llame al original. Al terminar,
    `cambio_pendiente is True`.
  - `test_la_edicion_no_pisa_una_baja_en_paralelo`: cargar la vista y, entre `get_form` y
    `form_valid`, simular la baja con `Producto.objects.filter(pk).update(moderacion="bajado")`.
    Parchear `ProductoForm.save` para que primero haga el `update()` y después llame al original.
    Al terminar, sigue `bajado`.
  - `test_uno_bajado_no_se_puede_editar`: GET y POST redirigen con el mensaje, y el POST no cambia
    el nombre.
  - `test_uno_bajado_no_se_puede_borrar` y `test_uno_denunciado_no_se_puede_borrar`: el producto
    sigue existiendo.
  - `test_el_listado_muestra_los_badges`.
  - `test_el_admin_muestra_la_moderacion_de_solo_lectura`: `"moderacion"` está en
    `admin.site._registry[Producto].readonly_fields` (molde de `tests_modelos.py:364`), y
    `DenunciaProducto` está registrado sin permisos de alta, edición ni borrado.
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `.venv/bin/python manage.py test tienda` → PASS. Mutación de control: volver a
  `form.save()` en la edición tiene que poner rojo el test de la carrera.
- [ ] **Paso 5: commit.** `feat(tienda): el staff no puede aprobar, pisar ni borrar lo moderado`.

---

### Tarea 6: las transiciones de moderación y el aviso por mail

**Archivos:**
- Crear: `tienda/moderacion.py` y `templates/tienda/email/aviso_moderacion.txt`
- Modificar: `tienda/services.py` (`cancelar_pedidos_de_producto` y el helper compartido con
  `vencer_pedidos`)
- Test: `tienda/tests_moderacion.py`

**Interfaces:**
- Consume: los campos y el modelo de las Tareas 1 y 2, y `services._devolver_stock`.
- Produce, en `tienda/services.py`:
  - `cancelar_pedidos_de_producto(producto_id: int, *, motivo: str, ahora=None) -> list[int]`:
    los ids de los pedidos `PENDIENTE_PAGO` que contienen alguna variante del producto y que pasan
    a `CANCELADO` con `cancelado_por_staff=True`, `motivo` y `modificado=ahora`, más la devolución
    de su stock.
    - Se seleccionan con `select_for_update()` y `pk__in=Subquery(ItemPedido...values("pedido_id"))`.
      Nada de `distinct()`, porque Postgres no admite `FOR UPDATE` con `DISTINCT`.
    - Ordenados por `pk` (el `ordering` del modelo es `-creado`), para que dos bajas con pedidos
      compartidos no se bloqueen en cruz.
    - Copia el esquema de `vencer_pedidos`: UPDATE masivo y, si tocó menos filas, uno por uno.
      **`vencer_pedidos` no se toca.**
  - Contra la carrera con el checkout (foco 6): `_reservar_y_crear`, después de los UPDATE de
    stock y dentro de su `atomic`, vuelve a contar `_variantes_vendibles(gimnasio).filter(pk__in=carrito)`.
    Si falta alguna, `ErrorDeTienda("producto_no_disponible")`, y el `atomic` deshace el stock.
  - En `tienda/pagos_mp.py::_re_reservar`, antes de reservar: si algún ítem del pedido no cumple
    `q_vendible("variante__producto__")`, va directo a REVISAR por la misma rama que «sin stock»
    (decisión 1).
- Produce, en `tienda/moderacion.py`:
  - `UMBRAL_DENUNCIAS = 3`
  - `MOTIVO_PEDIDO_CANCELADO = "El producto fue dado de baja por la plataforma."`
  - `AVISO_FOTO_RECHAZADA = "Una foto fue rechazada por la plataforma."`
  - `MOTIVO_POR_DENUNCIAS = "Lo denunciaron varios alumnos: lo estamos revisando."`
  - `q_para_revisar() -> Q`, sobre `Producto`: `Q(moderacion=PENDIENTE) | (Q(moderacion=APROBADO)
    & (Q(cambio_pendiente=True) | Exists(fotos sin aprobar) | Exists(denuncias abiertas)))`.
  - `contador() -> int`: `Producto.objects.filter(q_para_revisar()).exclude(gimnasio__es_demo=True).count()`,
    UNA query (decisión 9).
  - `cola() -> dict` con `"pendientes"`, `"a_la_venta"` (las dos sin la demo) y `"demo"` (todo lo
    de la demo, en una lista): las dos listas de productos con
    `select_related("gimnasio", "categoria")`, `prefetch_related("fotos", "variantes",
    Prefetch("denuncias", queryset=abiertas.select_related("alumno"), to_attr="denuncias_abiertas"))`,
    ordenadas por `creado`. Cantidad de queries constante.
  - `aprobar_producto(producto_id, *, version: datetime, fotos_vistas, denuncias_vistas, ahora=None) -> bool`:
    - en un `atomic`, `filter(pk, moderacion__in=[PENDIENTE, APROBADO], modificado=version).update(moderacion=APROBADO,
      cambio_pendiente=False, moderacion_motivo="", moderacion_desde=ahora)`;
    - si tocó la fila, aprueba sólo las `fotos_vistas` de ese producto y resuelve sólo las
      `denuncias_vistas` que siguen abiertas;
    - devuelve si tocó la fila.
  - `bajar_producto(producto_id, *, motivo, ahora=None) -> list[int] | None`:
    - `None` si el producto no estaba pendiente ni aprobado;
    - si no, primero `Variante.objects.select_for_update().filter(producto_id=...)` (serializa contra
      el UPDATE de stock del checkout), y después pasa a `BAJADO` con el motivo cortado a 300, `moderacion_desde` y `cambio_pendiente=False`,
      resuelve todas sus denuncias abiertas y llama a `cancelar_pedidos_de_producto(...,
      motivo=MOTIVO_PEDIDO_CANCELADO)`, todo en el mismo `atomic`;
    - por `on_commit`, manda un push `notificar_pedido_al_alumno(pedido, "cancelado_plataforma")`
      por cada pedido cancelado. Es un aviso nuevo en `notificaciones/services.py::_AVISOS_ALUMNO`:
      `("Pedido cancelado", "La plataforma dio de baja un producto de tu pedido #{pk} y lo canceló.")`.
      El `"cancelado"` de hoy dice «El gimnasio canceló», que acá sería falso;
    - devuelve los ids.
  - `aprobar_foto(foto_id) -> bool`: `filter(pk, aprobada=False).exclude(producto__moderacion=BAJADO).update(aprobada=True)`.
  - `rechazar_foto(foto_id) -> bool`:
    - borra la fila de una foto de un producto no bajado (el archivo queda en R2);
    - pone `moderacion_motivo = AVISO_FOTO_RECHAZADA` en el producto sólo si el motivo está vacío
      o ya es ese mismo aviso (no pisa `MOTIVO_POR_DENUNCIAS`);
    - `False` si no existía.
  - `registrar_denuncia(*, producto, alumno, motivo, detalle, ahora=None) -> bool`:
    - `False` si ya tenía una abierta, mirada antes en Python, o si da `IntegrityError` dentro de un
      `atomic`;
    - con `UMBRAL_DENUNCIAS` alumnos distintos con denuncia abierta,
      `filter(pk, moderacion=APROBADO).update(moderacion=PENDIENTE, moderacion_motivo=MOTIVO_POR_DENUNCIAS,
      moderacion_desde=ahora)`.
  - `avisar_a_la_plataforma(gimnasio, *, url_cola: str, ahora=None) -> bool`:
    - si `gimnasio.es_demo`, devuelve `False` sin escribir nada (decisión 9);
    - llama a `obtener_configuracion(gimnasio)`;
    - hace `ConfiguracionTienda.objects.filter(gimnasio=gimnasio).filter(Q(ultimo_aviso_moderacion__isnull=True)
      | Q(ultimo_aviso_moderacion__lte=ahora - timedelta(hours=1))).update(ultimo_aviso_moderacion=ahora)`;
    - si tocó la fila, `transaction.on_commit(...)` manda el mail. Destino:
      `settings.SOLICITUDES_AVISO_EMAIL or settings.DEFAULT_FROM_EMAIL`. Asunto: «Moderación:
      {gimnasio.nombre} tiene algo para revisar». El cuerpo sale del `.txt` con el link
      `url_cola`. Va con `EmailMessage(...).send(fail_silently=True)`, por el mismo motivo que
      `solicitudes/emails.py::_enviar`;
    - devuelve si mandó.
- Esta tarea no llama al aviso desde ninguna vista: la URL de la cola nace en la Tarea 7, y
  las llamadas se conectan en la Tarea 8.

- [ ] **Paso 1: tests que fallan** (`TransicionesTests`, `BajarTests`, `DenunciaServicioTests`,
  `AvisoTests`):
  - `test_aprobar_aprueba_producto_fotos_vistas_y_resuelve_denuncias_vistas`.
  - `test_aprobar_no_aprueba_una_foto_que_no_vio` (foco 3): dos fotos, `fotos_vistas=[una]`; la
    otra sigue sin aprobar.
  - `test_aprobar_con_version_vieja_no_hace_nada` (foco 3): el texto se editó después de la vista
    con `producto.save()` (NO `update()`, que no mueve `modificado`). Devuelve `False` y el producto sigue con `cambio_pendiente True`.
  - `test_aprobar_un_bajado_no_hace_nada`.
  - `test_bajar_cancela_pendientes_devuelve_stock_y_no_toca_los_pagados` (foco 4):
    - pedido P1 `PENDIENTE_PAGO` con el producto bajado y otro producto;
    - P2 `EN_REVISION`, P3 `PAGADO` y P4 `ENTREGADO` con el producto bajado;
    - después: P1 `CANCELADO`, con `cancelado_por_staff` y el motivo, y vuelve el stock de las DOS
      variantes de P1;
    - P2, P3 y P4 quedan iguales;
    - el stock reservado por P2 y P3 no cambia.
  - `test_bajar_resuelve_las_denuncias`.
  - `test_un_pago_tardio_de_mp_sobre_un_pedido_cancelado_por_bajar_va_a_revisar`:
    - usar el molde de `tests_mercadopago_acreditar.py`, con un pago `approved` simulado por `patch`;
    - el pedido termina en `REVISAR` y no en `PAGADO`;
    - correr también `tienda.tests_mercadopago_acreditar` sin tocarlo: verde.
  - `test_un_pago_tardio_sobre_un_vencido_de_un_producto_bajado_va_a_revisar` (decisión 1):
    pedido MP VENCIDO → bajar → approved simulado → `REVISAR` y el stock no se re-reserva.
  - `test_checkout_que_cruza_una_baja_no_deja_pedido_vivo` (foco 6): parchear
    `_variantes_vendibles` o el primer UPDATE de stock para que el producto se baje en el medio. El
    checkout da `producto_no_disponible`, no queda ningún pedido y el stock vuelve a su valor.
  - `test_tres_denuncias_no_cancelan_los_pedidos_pendientes` (decisión 8).
  - `test_la_demo_no_manda_mail_ni_suma_al_contador` (decisión 9): `cola()["demo"]` la trae.
  - `test_rechazar_foto_no_pisa_el_motivo_de_denuncias`.
  - Además, `tienda.tests_services` y `tienda.tests_comandos` siguen verdes sin tocarlos.
  - `test_rechazar_foto_borra_la_fila_y_avisa_al_staff`: la fila no está, el archivo sigue en el
    storage y `moderacion_motivo == AVISO_FOTO_RECHAZADA`.
  - `test_tres_denuncias_de_alumnos_distintos_lo_vuelven_pendiente` y
    `test_dos_no_alcanzan`.
  - `test_la_misma_persona_no_denuncia_dos_veces`: la segunda devuelve `False` y sigue habiendo
    una sola.
  - `test_despues_de_aprobar_solo_cuentan_las_nuevas`: 3 denuncias → pendiente → aprobar con las
    3 vistas → una denuncia nueva → sigue aprobado.
  - `test_el_aviso_sale_una_vez_por_hora_por_gimnasio`:
    - con `self.captureOnCommitCallbacks(execute=True)`, dos avisos seguidos dan un solo mail en
      `mail.outbox`;
    - con el reloj a +61 minutos, un segundo mail;
    - otro gimnasio recibe el suyo aparte.
  - `test_contador_y_cola_tienen_queries_constantes`: con `CaptureQueriesContext`, crear 2 y
    después 6 productos para revisar (con fotos, variantes y denuncias). Las queries de
    `contador()` dan 1 en los dos casos, y las de `list(cola()["pendientes"]) +
    list(cola()["a_la_venta"])` tienen que ser iguales entre los dos casos.
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** implementar `cancelar_pedidos_de_producto`, la revalidación en
  `_reservar_y_crear` y la rama nueva de `_re_reservar`. Correr `tienda.tests_services
  tienda.tests_comandos tienda.tests_mercadopago_acreditar tienda.tests_mercadopago_compra` →
  verde ANTES de seguir.
- [ ] **Paso 4:** implementar `tienda/moderacion.py` y el template del mail.
- [ ] **Paso 5:** `.venv/bin/python manage.py test tienda` → PASS.
- [ ] **Paso 6: commit.** `feat(tienda): aprobar, bajar (cancela pedidos sin pagar), fotos,
  denuncias y aviso a la plataforma`.

---

### Tarea 7: la cola del superadmin

**Archivos:**
- Crear: `plataforma/views_moderacion.py`, `templates/plataforma/moderacion.html` y
  `templates/plataforma/moderacion_confirmar.html`
- Modificar: `plataforma/urls.py`, `plataforma/views.py::InicioView` y
  `templates/plataforma/inicio.html`
- Test: `plataforma/tests_moderacion.py`

**Interfaces:**
- Consume: `moderacion.cola`, `contador`, `aprobar_producto`, `bajar_producto`, `aprobar_foto` y
  `rechazar_foto`.
- Produce las URLs (namespace `plataforma:`):
  - `moderacion/` → `ModeracionView` (`never_cache`), `name="moderacion"`.
  - `moderacion/fotos/<int:pk>/` → `FotoModeracionView`, `name="moderacion_foto"`. Devuelve los
    bytes de cualquier foto de cualquier gimnasio, con `Cache-Control: private, no-store`. Nunca
    usa `imagen.url` (la firmada de R2).
  - `moderacion/productos/<int:pk>/<str:accion>/` → `ProductoModeracionView`, con
    `accion ∈ {"aprobar", "bajar"}` (cualquier otra da 404) y `name="moderacion_producto"`.
    - El GET muestra la confirmación con todas las fotos, el texto, el precio, las variantes, las
      denuncias y, para «Aprobar», los hidden `version` (`value="{{ producto.modificado.isoformat }}"`),
      `fotos_vistas` y `denuncias_vistas`.
    - El POST lee `version` con `django.utils.dateparse.parse_datetime`. Si viene `None` o tira
      `ValueError`, se trata como versión vieja (mensaje de «cambió mientras lo mirabas»), nunca
      un 500.
    - «Bajar» lleva el campo `motivo`: obligatorio, hasta 300 caracteres, con su línea de error.
    - El POST aplica la acción.
  - `moderacion/fotos/<int:pk>/<str:accion>/` → `FotoModeracionAccionView`, con
    `accion ∈ {"aprobar", "rechazar"}` y `name="moderacion_foto_accion"`. «Aprobar» es POST
    directo desde la cola. «Rechazar» tiene un GET de confirmación (el mismo template) y después
    el POST.
  - Todas heredan de `SuperadminRequiredMixin`: el resto recibe 403.
- `InicioView`: `context["moderacion_para_revisar"] = moderacion.contador()`. En `inicio.html` va
  un botón «Moderación» con su badge, junto a «Solicitudes», y una tarjeta `aviso-urgente`
  «Productos por revisar» con el mismo molde que la de solicitudes (`inicio.html:28-36`).

**Mensajes (copy fijo):**
- Aprobado: «Aprobado: {nombre} ya está a la venta.»
- Versión vieja: `messages.warning` «El producto cambió mientras lo mirabas: revisalo de nuevo.»
- Bajado: «{nombre} quedó dado de baja. Se cancelaron {n} pedido(s) sin pagar.»
- Ya no estaba para bajar o aprobar: `messages.info` «Este producto ya no estaba en la cola.»
- Foto aprobada: «Foto aprobada.» Foto rechazada: «Foto rechazada; le avisamos al gimnasio.»

**Pantalla:**
- Cada tarjeta muestra:
  - el gimnasio, con link a `plataforma:gimnasio_detalle`;
  - el nombre, la descripción, el precio y las variantes (nombre y stock);
  - TODAS las fotos en miniatura (`<a href="{% url 'plataforma:moderacion_foto' f.pk %}"
    target="_blank" hx-boost="false"><img src="..." width="120" height="120" loading="lazy"></a>`).
    Un click las abre en grande, en otra pestaña;
  - un badge «Aprobada» o «Nueva» por foto;
  - las denuncias abiertas, con motivo, detalle y alumno (nombre y apellido);
  - las marcas «Texto cambiado» y «N fotos nuevas»;
  - los botones «Aprobar…», «Bajar…», «Aprobar foto»/«Rechazar foto…» por foto y «Suspender el
    gimnasio…», que es un link a `plataforma:estado_cuenta` con `estado=suspendida`.
- Estilo de las tarjetas del panel de plataforma. Sin `extra_style`: si hace falta CSS, va a
  `styles/input.css` + `npm run build:css`.
- Sin nada para revisar: «No hay nada para revisar.»
- Al final, `<details><summary>Cuenta demo ({{ n }})</summary>…</details>` con lo de la demo y las
  mismas acciones (decisión 9).

- [ ] **Paso 1: tests que fallan:**
  - `AccesoTests.test_staff_y_alumno_reciben_403` en las cuatro URLs.
  - `ColaTests.test_muestra_pendientes_y_cambios_con_sus_fotos`: las URLs de
    `moderacion_foto` de TODAS las fotos y el nombre del gimnasio.
  - `ColaTests.test_queries_constantes`: con `CaptureQueriesContext`, 2 contra 6 productos.
  - `FotoSuperadminTests.test_sirve_bytes_sin_cache`: `private, no-store` y el contenido es el PNG.
  - `AccionesTests.test_aprobar_desde_la_confirmacion`: los hidden se SACAN DEL HTML del GET (no
    de `producto.modificado`) y el POST con esos valores deja el producto aprobado.
  - `AccionesTests.test_version_mal_formada_no_da_500`.
  - `AccionesTests.test_aprobar_con_version_vieja_avisa`.
  - `AccionesTests.test_bajar_sin_motivo_muestra_el_error` y `test_bajar_con_motivo_cancela_y_redirige`.
  - `AccionesTests.test_accion_desconocida_da_404`.
  - `AccionesTests.test_aprobar_y_rechazar_foto`.
  - `InicioTests.test_contador_en_el_inicio`, y que `plataforma.tests` siga verde.
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `.venv/bin/python manage.py test plataforma tienda` → PASS.
- [ ] **Paso 5: commit.** `feat(plataforma): cola de moderación con fotos visibles y acciones`.

---

### Tarea 8: «Denunciar» para el alumno y el aviso desde las vistas

**Archivos:**
- Modificar: `tienda/forms.py` (`DenunciaForm`), `tienda/views.py` (`DenunciarView`, `CatalogoView` y
  `_ProductoFormMixin.form_valid`) y `tienda/urls.py`
- Crear: `templates/tienda/denunciar.html`
- Modificar: `templates/tienda/catalogo.html`
- Test: `tienda/tests_moderacion.py` y `tienda/tests_vistas_alumno.py::CatalogoEscalaTests`

**Interfaces:**
- Consume: `moderacion.registrar_denuncia` y `moderacion.avisar_a_la_plataforma` (Tarea 6),
  `plataforma:moderacion` (Tarea 7) y `_ProductoFormMixin.entro_a_la_cola` (Tarea 5).
- Produce:
  - `DenunciaForm(forms.Form)`: `motivo = ChoiceField(choices=DenunciaProducto.Motivo.choices)` y
    `detalle = CharField(required=False, max_length=500, widget=Textarea)`;
  - `DenunciarView(TiendaAlumnoMixin, TemplateView)`, con template `tienda/denunciar.html` y
    `procesar()` para el POST;
  - la URL `path("productos/<int:pk>/denunciar/", ..., name="denunciar")`.

**Comportamiento (copy fijo):**
- El producto se busca con `Producto.objects.for_gimnasio(self.gimnasio).filter(q_vendible(), pk=...).first()`. Si no está, `messages.info` «Ese producto ya no está en la tienda.» y redirect al catálogo (GET y POST).
- Con `suplantacion.esta_activa(request)`: `messages.error` «Mientras ves la app como un alumno no
  podés denunciar productos.» y redirect al catálogo.
- Si es inválido, redibuja con los errores de los dos campos (cada uno en su línea, con 200).
- Si es válido y nuevo: `messages.success` «Gracias. La plataforma va a revisar este producto.»,
  aviso a la plataforma y redirect a `tienda:catalogo`.
- Si ya lo había denunciado: `messages.info` «Ya lo denunciaste: lo estamos revisando.» y
  redirect.
- Aviso desde el form del staff: `_ProductoFormMixin.form_valid`, cuando `entro_a_la_cola`, llama
  a `moderacion.avisar_a_la_plataforma(self.gimnasio,
  url_cola=self.request.build_absolute_uri(reverse("plataforma:moderacion")))`. La denuncia usa
  la misma llamada.
- Catálogo:
  - `context["denunciados"]` es el set de `producto_id` con denuncia abierta del alumno, en UNA
    query;
  - cada tarjeta muestra, al pie, el link «Denunciar» o el texto «Ya lo denunciaste»;
  - el staff nunca ve quién denunció: no hay nada de esto en las vistas de staff.

- [ ] **Paso 1: tests que fallan** (`DenunciarVistaTests`):
  - `test_denunciar_crea_la_denuncia_y_avisa`.
  - `test_sin_motivo_muestra_el_error_en_su_linea`: 200 y el error está en el HTML.
  - `test_suplantando_no_se_puede`: usar el helper de suplantación de `tests_vistas_alumno` (el
    que prueba `crear_pedido` con suplantación).
  - `test_un_producto_de_otro_gimnasio_avisa_y_vuelve_al_catalogo` y
    `test_un_producto_pendiente_avisa_y_vuelve_al_catalogo`: `messages.info` «Ese producto ya no
    está en la tienda.» y redirect a `tienda:catalogo` (no un 404, que bajo `hx-boost` es un click
    mudo). No se crea ninguna denuncia.
  - `test_el_catalogo_dice_ya_lo_denunciaste`.
  - `test_el_alta_de_un_producto_avisa_a_la_plataforma`: el POST de alta del staff, con
    `captureOnCommitCallbacks(execute=True)`, deja 1 mail en `mail.outbox` con el link a la cola.
  - En `CatalogoEscalaTests._cargar`, sumar una denuncia abierta del alumno por producto. El test
    de escala existente tiene que seguir verde.
- [ ] **Paso 2:** correrlos → FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `.venv/bin/python manage.py test tienda` → PASS. Mutación de control: armar
  «Ya lo denunciaste» con una query por producto tiene que poner rojo el test de escala.
- [ ] **Paso 5: commit.** `feat(tienda): el alumno puede denunciar un producto y la plataforma recibe el aviso`.

---

### Tarea 9: demo, condiciones de uso y documentación

**Archivos:**
- Modificar: `tenants/demo.py::_sembrar_tienda`
- Modificar: `templates/tienda/configuracion_form.html` y `templates/plataforma/gimnasio_detalle.html`
  (junto al botón «Habilitar tienda», línea 269)
- Modificar: `ARQUITECTURA.md` (subsección «Moderación» dentro de «Tienda del gimnasio»),
  `CLAUDE.md` (una fila en la tabla «Si vas a tocar…») e `ISSUES.md`
- Test: `tenants/tests.py` (tests de la demo)

**Comportamiento:**
- `_sembrar_tienda`, después del `get_or_create` de cada producto del catálogo de muestra, hace
  `Producto.objects.filter(pk=producto.pk, moderacion=PENDIENTE).update(moderacion=APROBADO)`. Nunca
  pisa una baja del superadmin.
  Así también se aprueba un producto que sobrevivió a `--borrar`. Lo que creó un prospecto no se
  toca.
- La cláusula va igual en las dos pantallas, como `<p class="texto-suave">`. El texto lo revisa el
  usuario:
  > Está prohibido vender drogas o medicamentos sin receta, armas, contenido o servicios sexuales,
  > o cualquier cosa ilegal. La plataforma revisa cada producto antes de publicarlo y puede darlo
  > de baja, cancelar sus pedidos sin pagar y suspender la cuenta del gimnasio.
- `ISSUES.md`, con la fecha 2026-10-19, anota los riesgos aceptados:
  - el logo, el fondo, las variantes, las categorías e `instrucciones_retiro` no se moderan;
  - las copias de una foto ya aprobada que se rechaza o se baja siguen en los navegadores que la
    bajaron (`immutable`);
  - un pago tardío de MP sobre un pedido cancelado por la baja, o sobre un vencido de un producto
    que ya no está a la venta, queda en «Revisar MP» y el staff lo devuelve;
  - si el `cf-cache-status` de la Tarea 10 da HIT, hay que purgar la URL de la foto al bajar.
- `ARQUITECTURA.md`, en la subsección nueva:
  - los estados;
  - `q_vendible` como única puerta;
  - los dos modos de la foto y por qué no se lee la sesión en el modo público;
  - las transiciones;
  - qué pasa con los pedidos;
  - el tope del mail;
  - las decisiones 1 a 11 de este plan.
  - También en `ARQUITECTURA.md` § «Datos de demostración» (la demo se siembra aprobada, queda fuera
    de la cola y `vaciar_gimnasio` borra las denuncias) y § «Panel de plataforma» (la cola, el
    contador y la vista de fotos del superadmin).
- `CLAUDE.md`: la fila «Moderación de la tienda (cola, denuncias, fotos sin aprobar)» → «Tienda
  del gimnasio → Moderación».

- [ ] **Paso 1: test que falla.** `test_la_tienda_demo_se_siembra_aprobada` (junto a
  `tenants/tests.py:3872`): después de `sembrar_demo` todos los productos sembrados están
  aprobados, y `q_vendible` los trae.
- [ ] **Paso 2:** correrlo → FAIL.
- [ ] **Paso 3:** implementar el cambio en la demo, la cláusula y la documentación.
- [ ] **Paso 4:** `.venv/bin/python manage.py test tenants tienda plataforma` → PASS.
- [ ] **Paso 5:** `git status --porcelain`: no puede quedar ningún archivo NUEVO sin agregar (la
  migración, los templates y los tests).
- [ ] **Paso 6: commit.** `docs(tienda): moderación en ARQUITECTURA/ISSUES, demo aprobada y
  cláusula de uso`.

---

### Tarea 10: verificación final

- [ ] **Paso 1:** suite completa con `.env`. Tiene que salir todo OK: la línea de base más los
  tests nuevos.
- [ ] **Paso 2:** `.venv/bin/python manage.py test tienda tenants plataforma` juntos: verde.
- [ ] **Paso 3: Postgres 16.** Levantar `docker run -d --rm --name pg-moderacion -e
  POSTGRES_PASSWORD=pg -p 55432:5432 postgres:16`. Después, `DATABASE_URL=postgres://postgres:pg@localhost:55432/postgres
  .venv/bin/python manage.py test` (la suite COMPLETA). Tiene que salir verde, incluidos el
  INSERT viejo, el `select_for_update` de la baja y la migración de datos. Al terminar,
  `docker stop pg-moderacion`.
- [ ] **Paso 4:** `.venv/bin/python manage.py makemigrations --check --dry-run` → «No changes
  detected».
- [ ] **Paso 5: QA en el navegador** con `runserver` y el service worker sin caché (ver
  `CLAUDE.md` «Verificar en el navegador»):
  - un producto nuevo queda pendiente y no aparece en el catálogo; aprobarlo desde la cola lo hace
    aparecer;
  - una foto nueva sobre un producto aprobado no la ve el alumno, la ve el staff con «En revisión»
    y la ve el superadmin en la cola;
  - un cambio de texto entra a la cola;
  - denunciar desde un alumno; con 3 alumnos distintos, el producto vuelve a pendiente;
  - bajar un producto con un pedido pendiente cancela ese pedido y no toca uno pagado;
  - los mensajes se ven bajo `hx-boost`.
- [ ] **Paso 6:** probar las cabeceras con `curl -sI` contra `runserver`. La foto sin aprobar da
  404 con `no-store` sin sesión. Con la cookie de sesión del staff da 200 y `private, no-store`.
  La foto aprobada da `public, max-age=31536000, immutable` y no trae `Set-Cookie`.
  **Compuerta después del deploy:** `curl -sI` a una foto en producción y mirar `cf-cache-status`.
  Si da `HIT`, Cloudflare la cachea en el borde: anotarlo en `ISSUES.md` con el procedimiento de
  purga al bajar y avisarle al usuario.
- [ ] **Paso 7:** review de toda la rama con un reviewer nuevo, y sólo después el merge que pida
  el usuario. La suite verde en la rama no prueba el merge: hay que correrla otra vez sobre el
  merge.
