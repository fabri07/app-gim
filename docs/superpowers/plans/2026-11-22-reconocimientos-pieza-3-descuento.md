# Reconocimientos — Pieza 3: Descuento en la cuota Implementation Plan (v2, revisado)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> Al aprobarse se copia a `docs/superpowers/plans/2026-11-22-reconocimientos-pieza-3-descuento.md` (primer commit de la rama).

**Goal:** Que una cuota impaga pueda llevar un descuento (monto + motivo) que se respete en TODO lugar donde hoy se lee `Cuota.monto`: cobro por Mercado Pago, pantallas del alumno y del staff, ingresos, avisos y exportación. El staff lo carga a mano; la Pieza 5 (premios) lo aplicará con el mismo servicio.

**Architecture:** `Cuota.monto` sigue siendo el monto de LISTA. Se agregan `descuento` y `descuento_motivo`. **Única fuente del neto:** `Cuota.monto_a_cobrar = monto - descuento` (+ expresión `Cuota.NETO` para SQL). **Invariante de una cuota PAGADA: `monto - descuento = lo realmente cobrado`.** Un servicio nuevo (`pagos/descuentos.py`) es la única puerta para escribir `descuento` en cuotas impagas. Sin descuento (default 0) nada cambia.

**Rama:** `worktree-reconocimientos-pieza-3` **desde `main`** (no apilada sobre las piezas 1-2): esta pieza no usa la bandera ni los rankings y puede mergearse sola, antes o después de los PR #4/#5.

**Tech Stack:** Django 5.2, `Decimal`, `CheckConstraint`.

**Spec:** `docs/superpowers/specs/2026-11-21-reconocimientos-design.md` § Pieza 3 (ese archivo vive en la rama de la pieza 1; la pieza 3 no lo necesita para compilar).

## Global Constraints
- **Es plata y datos en vivo**: migración aditiva con `db_default` (deploy sin ventana), reversible; `makemigrations --check` limpio.
- Tope de descuento **90%** del monto de lista (el 100% se resuelve anulando la cuota, acción existente). Con `monto = 0` el descuento es 0.
- El descuento **solo se escribe en cuotas impagas** (PENDIENTE/VENCIDO), nunca en PAGADAS ni ANULADAS.
- Todo cambio del neto de una cuota impaga pasa por `cobro._bloquear` + `cancelar_links_vivos` (un link de MP abierto cobra el monto viejo).
- Toda llamada a MP lleva `token=` del gimnasio (`pagos/tests_token_del_gimnasio.py` lo vigila; no se toca).
- Fechas con `timezone.localdate()`. Cada campo de formulario con su línea de error. `{# #}` de una línea. Sin N+1.
- Un alumno sin descuento ve exactamente lo mismo que hoy (regresión cero).
- La suite no sale a la red. Baseline = el de `main` al empezar; cualquier rojo es regresión.

## Decisiones propias (rulings, corregibles)
1. **`monto` = lista, `descuento` aparte** (opción A del usuario).
2. **Constraint tolerante con datos viejos:** `descuento = 0 OR (descuento > 0 AND descuento <= monto * 0.9)`. Un `monto` raro/negativo ya cargado en producción no hace fallar la migración mientras `descuento = 0`.
3. **Al acreditar por MP o confirmar a mano, `monto` guardado = cobrado + descuento**, para cumplir la invariante. Si se guardara solo lo cobrado, el descuento se restaría dos veces y `ingresos_por_mes` subcontaría.
4. **El campo «monto» de «Confirmar pago» significa «lo que cobraste»**: valor inicial = neto; al guardar se suma el descuento.
5. **Solo descuento en pesos** en la UI del staff (+ motivo). El porcentaje lo calcula la Pieza 5 con el servicio.
6. **`Cuota.observaciones` no reemplaza al motivo**: el motivo es corto (80), visible para el alumno y estructurado.
7. **Exportación: 2 columnas nuevas al final de `cuotas` (`descuento`, `descuento_motivo`).** No va `monto_a_cobrar` (es una propiedad, no una columna: se deriva restando).
8. **Rama desde `main`**, no apilada (ver arriba).

## Revisión del plan v1 — qué se corrigió y por qué (verificado contra el código)
- **Carrera acreditar vs descuento (importante):** `marcar_pagada(desde=…)` recibía `monto=intento.monto + cuota.descuento` con la instancia leída antes; si el staff aplica un descuento justo antes del UPDATE, el `descuento` quedaba viejo y se rompía la invariante. → el UPDATE usa **`monto=F("descuento") + intento.monto`**, resuelto por la base bajo el lock. Test: aplicar descuento después de leer la instancia y antes de acreditar.
- **Carrera confirmar a mano vs descuento:** `ConfirmarPagoForm.clean_monto` suma el descuento leído al abrir el form. → `marcar_pagada(desde=None)`, ya con el lock tomado, relee `descuento` y, si cambió respecto de la instancia, **no guarda y devuelve `False`** (la vista ya maneja `False` con un cartel «la cuota cambió, volvé a abrirla»).
- **Exportación:** la v1 prometía una columna `monto_a_cobrar` que no es un campo del modelo (`Hoja` usa rutas de campo). Se exportan `descuento` y `descuento_motivo`. Los tests de exportación existentes comparan las columnas con las del CSV, y hay un guardián que exige que todo campo de un modelo exportado esté en `HOJAS` o `EXCLUIDOS`: los campos nuevos tienen que ir en `HOJAS` en la misma tarea o la suite se pone roja.
- **Admin:** el superusuario podría editar `descuento` de una cuota PAGADA y romper la invariante → `descuento` y `descuento_motivo` quedan de solo lectura salvo en cuotas impagas.
- **Constraint:** la expresión `F("monto") * Decimal("0.9")` se verifica en T1 mirando el SQL generado (SQLite y la forma Postgres); si el ORM no la acepta se usa `GreaterThanOrEqual(F("monto") * 9, F("descuento") * 10)` (enteros, sin decimales flotantes).
- **`descuento_maximo(monto)`** redondea hacia ABAJO a centavos, para no pasar nunca el constraint (ej. 100,05 × 0,9 = 90,045 → 90,04).
- **Volver a impaga** (cuota PAGADA con cobro MP devuelto): como se guardó `monto = cobrado + descuento`, `monto` vuelve a ser la lista y `descuento` sigue intacto: queda consistente sin tocar `volver_a_impaga`. Test explícito.
- **Rama:** desde `main` para poder mergearla sola.

## Review Focus (cada línea tiene su test en la tarea que la posee)
1. Pagar por MP una cuota con descuento deja `monto − descuento == cobrado` y NO dispara «monto corregido» falso; sigue cumpliéndose si el descuento cambia justo antes de acreditar. → T3
2. Cambiar monto o descuento con un link abierto cancela el link; no se reusa un link por el neto viejo. → T2/T3
3. Una cuota pagada con descuento suma lo cobrado (no el bruto) en `ingresos_por_mes`. → T5
4. 91% rechazado, 90% aceptado, descuento negativo rechazado, descuento en pagada/anulada rechazado. → T1/T2
5. Cuota con monto 0 y «aplicar precio a impagas sin monto» siguen funcionando. → T2
6. Alumno sin descuento: pantallas y mails idénticos a hoy. → T4/T5
7. «Volver a impaga» tras un cobro devuelto deja `monto_a_cobrar` igual al neto original. → T3

## File Structure
- Modify `pagos/models.py` (campos, property, constraint) + migración `pagos/00NN_cuota_descuento`
- Create `pagos/descuentos.py`, `pagos/tests_descuentos.py`
- Modify `pagos/cobro.py`, `pagos/cobro_mp.py`, `pagos/forms.py`, `pagos/views.py`, `pagos/admin.py`
- Modify `tenants/analitica.py`, `notificaciones/services.py`, `tenants/exportacion.py`
- Modify templates: `pagos/pago_list.html`, `pagos/pago_monto.html`, `pagos/comprobante_alumno_form.html`, `partials/cuotas_impagas.html`, `alumnos/alumno_detail.html`, `tenants/home.html`
- Docs: `ARQUITECTURA.md` («Apps de dominio → pagos», «Cuotas por Mercado Pago»), `ISSUES.md`

---

### Task 1: Modelo — `descuento`, `descuento_motivo`, `monto_a_cobrar`, constraint

**Files:** Modify `pagos/models.py`; migración; tests en `pagos/tests_descuentos.py`.

**Interfaces:**
- Produces: `Cuota.descuento` (Decimal 10,2, default 0, `db_default=0`); `Cuota.descuento_motivo` (Char 80, blank, `db_default=""`); property `monto_a_cobrar -> Decimal`; `Cuota.NETO` (`F("monto") - F("descuento")` con `output_field=DecimalField(10, 2)`); `TOPE_DESCUENTO = Decimal("0.90")`; `CheckConstraint(name="cuota_descuento_valido")`.

- [ ] **Step 1: tests**: neto = monto − descuento; sin descuento neto = monto; el constraint rechaza descuento negativo, 91%, y descuento > 0 con monto 0; acepta exactamente 90%; acepta `descuento=0` con `monto` negativo (dato viejo); una cuota insertada sin la columna nueva (SQL crudo, patrón de `tienda.tests_modelos.MigracionDeReferenciaExternaTests`) queda con descuento 0.
- [ ] **Step 2:** correr → falla.
- [ ] **Step 3:** implementar campos + constraint; `makemigrations -n cuota_descuento`; revisar el SQL (`sqlmigrate`): `ADD COLUMN … DEFAULT 0 NOT NULL`, constraint con la expresión, migración reversible; `makemigrations --check` limpio.
- [ ] **Step 4:** `python manage.py test pagos` verde. **Step 5:** commit `feat(pagos): descuento en la cuota (modelo)`.

---

### Task 2: Servicio de descuentos y corrección de montos

**Files:** Create `pagos/descuentos.py`; modify `pagos/cobro.py`; tests.

**Interfaces:**
- Consumes: `cobro._bloquear`, `cobro.cancelar_links_vivos`, `TOPE_DESCUENTO`.
- Produces:
  - `descuento_maximo(monto) -> Decimal` (90% hacia abajo a centavos).
  - `aplicar_descuento(cuota_pk, gimnasio, descuento: Decimal, motivo: str, ahora=None) -> bool` — solo impagas del gimnasio; `0` quita el descuento; False si excede el tope, es negativo o la cuota no es impaga; bloquea, cancela links vivos, `modificado=ahora`.
  - `cobro.corregir_monto(cuota_pk, gimnasio, monto, ahora=None, *, descuento=None, motivo=None) -> bool` — firma retrocompatible; False sin tocar nada si el descuento vigente (o el nuevo) pasa del tope del monto nuevo.

- [ ] **Step 1: tests**: aplicar sobre impaga OK y cancela el link CREADO; PAGADA/ANULADA → False y sin cambios; otro gimnasio → False; 90% ok, 90,01% falla; negativo falla; quitar con 0 → `monto_a_cobrar == monto`; `corregir_monto` que baja el monto bajo el descuento vigente → False; `corregir_monto` clásico intacto (tests existentes verdes); `descuento_maximo(Decimal("100.05")) == Decimal("90.04")`; `aplicar_precio_a_impagas_sin_monto` sigue funcionando con monto 0 y no toca descuento.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (patrón de `corregir_monto`: `atomic` + `_bloquear` + UPDATE condicional + `cancelar_links_vivos`). **Step 4:** tests verdes. **Step 5:** commit `feat(pagos): servicio de descuentos`.

---

### Task 3: Mercado Pago y confirmación manual respetan el neto

**Files:** Modify `pagos/cobro_mp.py`, `pagos/forms.py` (`ConfirmarPagoForm`), `pagos/cobro.py` (`marcar_pagada`); tests (ampliar los de `pagos` con el cliente MP simulado + nuevos).

- [ ] **Step 1: tests**:
  - crear link de una cuota de 10000 con descuento 2000 → `intento.monto == 8000` y la preferencia por 8000.
  - un link vivo por el neto sigue reusable; si el staff cambia el descuento, el viejo queda CANCELADO y se crea otro.
  - pago aprobado de 8000 → cuota PAGADA con `monto == 10000`, `descuento == 2000`, `monto_a_cobrar == 8000`, **sin** `MONTO_CORREGIDO` ni revisión.
  - **carrera**: se lee la cuota, el staff aplica un descuento distinto, y recién entonces se acredita → `monto − descuento == cobrado` igual (UPDATE con `F`).
  - pago con monto distinto del intento → `MONTO_DISTINTO` (sin cambios).
  - descuento corregido con el link abierto y el alumno paga el viejo → `MONTO_CORREGIDO` (se compara contra el neto).
  - `sin_monto`: monto 0 sigue «sin monto»; con descuento 90% el neto es > 0.
  - `volver_a_impaga` tras un cobro devuelto: `monto_a_cobrar` vuelve al neto original.
  - `ConfirmarPagoForm`: valor inicial de `monto` = neto; guardar sin tocar deja `monto − descuento == neto`; guardar 7000 con descuento 2000 deja `monto == 9000`; el form no expone `descuento`; editar una pagada por MP (campo disabled) no cambia nada; **si el descuento cambió entre abrir y guardar, `marcar_pagada` devuelve `False` y no escribe**.
- [ ] **Step 2:** correr → falla.
- [ ] **Step 3:** implementar: `cobro_mp.py` `:248` compara `cuota.monto_a_cobrar == intento.monto`; `:257` `monto=F("descuento") + intento.monto`; `:512-518`, `:543-544`, `:551/562/570` usan `monto_a_cobrar`; `ConfirmarPagoForm.__init__` fija `initial["monto"] = instance.monto_a_cobrar` y `clean_monto` devuelve `monto + instance.descuento`; `marcar_pagada(desde=None)` relee `descuento` bajo el lock y aborta si difiere. No cambia el orden de locks.
- [ ] **Step 4:** `python manage.py test pagos pagos.tests_token_del_gimnasio`. **Step 5:** commit `feat(pagos): el cobro usa el monto neto`.

---

### Task 4: Pantallas del staff y del alumno

**Files:** Modify `pagos/forms.py` (`CuotaMontoForm`), `pagos/views.py` (`CuotaMontoView`), `pagos/admin.py`, templates; tests.

- [ ] **Step 1: tests**: la pantalla de monto muestra y guarda `descuento` + `motivo` (error por campo para tope, negativo, motivo > 80, motivo vacío con descuento); sin descuento los textos de hoy no cambian; con descuento el listado de pagos, el detalle del alumno, el home del staff, `cuotas_impagas` (botón «Pagar $ NETO con Mercado Pago», condición `neto > 0`) y `comprobante_alumno_form` muestran neto, bruto tachado y motivo; staff de otro gimnasio → 404; el admin deja `descuento` de solo lectura en cuotas pagadas/anuladas.
- [ ] **Step 2:** correr → falla.
- [ ] **Step 3:** implementar: `CuotaMontoForm` con `fields = ["monto", "descuento", "descuento_motivo"]`, `clean` con tope (`descuento_maximo`) y motivo obligatorio si hay descuento; `CuotaMontoView.form_valid` llama a `cobro.corregir_monto(..., descuento=..., motivo=...)`; templates con `{% if pago.descuento %}`; cada campo con su `{% if form.campo.errors %}`.
- [ ] **Step 4:** tests verdes. **Step 5:** commit `feat(pagos): pantallas con descuento`.

---

### Task 5: Lecturas (ingresos, avisos, exportación) y documentación

**Files:** Modify `tenants/analitica.py:~513`, `notificaciones/services.py:~258`, `tenants/exportacion.py`; docs; tests.

- [ ] **Step 1: tests**: `ingresos_por_mes` con una cuota pagada de lista 10000 y descuento 2000 suma 8000, y sin descuento suma como antes; el aviso de cuota usa el neto y no ofrece «pagá con MP» si el neto es 0; la exportación agrega `descuento` y `descuento_motivo` al final de `cuotas` y los tests de exportación existentes siguen verdes (incluido el guardián de campos).
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (`Sum(Cuota.NETO)`; `pago.monto_a_cobrar`; 2 columnas al final). **Step 4:** suite completa `python manage.py test` verde, `makemigrations --check` limpio, `git status -sb` sin untracked. **Step 5:** `ARQUITECTURA.md` + `ISSUES.md` (reglas: monto=lista, invariante de pagada, tope 90%, el servicio como única puerta); commit `docs(pagos): descuentos`.

## Verificación end-to-end
- Tests por tarea + suite completa sobre la rama y sobre el merge.
- QA en navegador (local, R2 vacío): cuota de $10.000 → aplicar $2.000 con motivo → el alumno ve «$ 8.000» con $10.000 tachado y el botón de MP por 8.000; el staff cambia a $3.000 → el link anterior queda cancelado; confirmar pago manual y ver el ingreso del mes; probar el tope del 90%; celular 390 px y modo oscuro.
- El primer pago real por MP nunca se probó en producción (`ISSUES.md` 2026-10-24): el descuento no agrega riesgo propio, pero tampoco prueba ese flujo. No hay interruptor: el descuento solo existe si el staff lo carga.

## Qué NO se puede romper
| Área | Riesgo | Cómo se cuida |
|---|---|---|
| Cobro por MP | Descuento doble, link con monto viejo, carrera con el staff | `monto_a_cobrar` única fuente; UPDATE con `F`; todo cambio de neto cancela links |
| Ingresos del panel | Subcuenta/infla | `Sum(NETO)`; test con descuento |
| Migración en vivo | Falla por datos viejos | `db_default`, constraint tolerante a `descuento=0`, test de migración |
| Bloqueo por falta de pago | Deuda mal calculada | No se toca `acceso.py` (depende de estado, no de monto) |
| Exportador | Guardián de campos / columnas | Columnas nuevas en `HOJAS` en la misma tarea |
| Alumno sin descuento | Cambios visibles | Tests de textos idénticos |

## Abierto
- Si el dueño quiere cargar el descuento en porcentaje desde la pantalla del staff (hoy solo pesos).
- Las Piezas 4 (asistencia) y 5 (premios, que usa `aplicar_descuento` y necesita el hook posterior al `bulk_create` de `generar_pagos_pendientes`).
