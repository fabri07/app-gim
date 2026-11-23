# Reconocimientos, Pieza 1: bandera, T&C, foto y «Mi perfil» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un gimnasio con la bandera prendida le pida a cada alumno aceptar los términos una sola vez, y que el alumno pueda tener foto y decidir si aparece en el ranking.

**Architecture:** Bandera `Gimnasio.reconocimientos_habilitado` (la decide el dueño en «Mi gimnasio»). Un middleware de alumno, copiado del patrón de `PlataformaMiddleware`, responde 200 con un cartel hasta que acepta. La foto es `Alumno.foto`, se re-codifica con `tienda.fotos.preparar_foto` y se sirve por una vista autenticada del mismo gimnasio.

**Tech Stack:** Django 5.2, Pillow, django-storages (R2; en tests `InMemoryStorage`), HTMX `hx-boost`, Tailwind.

**Spec:** `docs/superpowers/specs/2026-11-21-reconocimientos-design.md` (§ «Pieza 1»). Este es el plan 1 de 5; las piezas 2 (ranking), 3 (descuento), 4 (asistencia) y 5 (premios) tendrán su propio plan. Esta pieza no cambia nada visible mientras la bandera esté apagada.

## Global Constraints

- Español rioplatense en toda la UI (voseo). `timezone.localdate()` para fechas del dominio, nunca `timezone.now().date()`.
- Cada campo de form con su `{% if form.<campo>.errors %}` en el mismo commit (`.errorlist` no tiene estilo). Renderizar campo por campo.
- `{# #}` de una sola línea; varias → `{% comment %}`. Si se toca `styles/input.css`, correr `npm run build:css` antes de commitear.
- `hx-boost="false"` en todo form que sube archivos. Los carteles responden **200** (un 4xx bajo `hx-boost` es un click mudo). Datos por-request DENTRO de `<body>`.
- Tokens semánticos del modo oscuro en pantallas nuevas (no colores literales).
- `Gimnasio.save()` cambia `modificado` (versiona logo/fondo/ícono): las escrituras de máquina usan `update()`; la del form de «Mi gimnasio» es inofensiva.
- La suite no sale a la red. Archivos de usuario van a R2, nunca al filesystem.
- Tests: `python manage.py test <app>.<módulo>`. Baseline de la suite completa con `.env` real: 1723 OK.
- Un `TenantOwnedModel` nuevo va a TRES lugares (`tenants/demo.py::vaciar_gimnasio`, `_ensuciar` en `tenants/tests.py`, `HOJAS/EXCLUIDOS` en `tenants/exportacion.py`). **Esta pieza no crea ninguno** (solo campos en `Gimnasio`, `Perfil`, `Alumno`).
- Nunca reintroducir registro self-service. La aceptación de términos nunca puede escribirse con una sesión suplantada.
- Trabajar en un worktree/rama nueva desde `main` (`superpowers:using-git-worktrees`); el worktree necesita el `.env` real (5 tests no herméticos, ver memoria).

## Review Focus

1. El staff suplanta a un alumno (`suplantacion.esta_activa`) con la bandera prendida y términos sin aceptar: no ve el cartel y no puede aceptar por el alumno. (Task 3)
2. Gimnasio con la cuenta congelada: el alumno ve el cartel de bloqueo de plataforma, nunca el de términos. (Task 3)
3. Alumno sube un `.txt` renombrado a `.jpg`, una foto de 12 MB o un PNG de 100×100: mensaje de error en el campo, nunca 500. (Task 5)
4. Alumno de otro gimnasio pide la foto de un compañero por URL: 404 con `no-store`. (Task 4)
5. Perfil de rol alumno sin ficha de `Alumno` (`perfil.alumno` no existe): «Mi perfil» muestra estado vacío y el POST da 403, sin 500. (Task 5)
6. Reemplazar o quitar la foto, o borrar al alumno, borra el archivo viejo del storage (sin huérfanos en R2). (Task 4)
7. La bandera es un checkbox estándar: un POST que no la manda la deja apagada (igual que `cuotas_con_mercadopago`). Queda escrito como regla y con test, no como inmunidad. (Task 1)
8. El cartel de términos no muestra el menú del alumno (todos sus links devolverían el mismo cartel). (Task 3)
9. Un alumno con `Perfil` pero sin ficha no queda atrapado en el cartel: puede aceptar sin ficha. (Task 3)
10. Si el alumno apaga «aparecer en el ranking», quita la foto o el dueño apaga la bandera, los compañeros dejan de ver la foto en una hora como máximo (caché corta, no `immutable`). (Task 4)

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `tenants/models.py` (mod.) | `Gimnasio.reconocimientos_habilitado`; `Perfil.terminos_aceptados_en`, `Perfil.terminos_version` |
| `tenants/forms.py` (mod.) | la bandera en `GimnasioForm` |
| `templates/tenants/gimnasio_form.html` (mod.) | checkbox de la bandera con su error |
| `tenants/terminos.py` (nuevo) | `VERSION_TERMINOS`, `debe_aceptar_terminos` (sin imports de `plataforma`) |
| `plataforma/terminos.py` (nuevo) | `TerminosMiddleware` (vive en `plataforma/` porque `tenants` no debe importar `plataforma` a nivel módulo: `plataforma.models` ya importa `tenants.models`) |
| `tenants/views.py`, `tenants/urls.py` (mod.) | `AceptarTerminosView`, ruta `terminos_aceptar` |
| `tenants/forms.py` (mod.) | `AceptarTerminosForm` |
| `templates/tenants/terminos.html` (nuevo) | cartel de términos |
| `templates/base.html` (mod.) | ocultar el ☰ y el nav del alumno con `terminos_pendientes` (líneas ~269 y ~332) |
| `config/settings.py` (mod.) | registrar `plataforma.terminos.TerminosMiddleware` después de `PlataformaMiddleware` |
| `alumnos/models.py` (mod.) | `Alumno.foto`, `Alumno.aparece_en_ranking`, `Alumno.foto_url` |
| `alumnos/fotos.py` (nuevo) | `guardar_foto`, `quitar_foto` |
| `alumnos/signals.py` (mod.) | borrar el archivo al borrar el alumno |
| `alumnos/views.py`, `alumnos/urls.py` (mod.) | `AlumnoFotoView`, `MiPerfilView` |
| `alumnos/forms.py` (mod.) | `MiPerfilForm` |
| `templates/alumnos/mi_perfil.html` (nuevo) | pantalla «Mi perfil» |
| `templates/partials/nav_alumno.html` (mod.) | ítem «Mi perfil» |
| `templates/tenants/privacidad.html` (mod.) | foto, ranking y premios |
| `tenants/demo.py` (mod.) | alumnos demo con términos aceptados; limpiar fotos en `vaciar_gimnasio` |
| Tests nuevos | `tenants/tests_terminos.py`, `alumnos/tests_perfil.py` |
| Docs | sección nueva en `ARQUITECTURA.md`, fila en la tabla de `CLAUDE.md`, entrada en `ISSUES.md` |

---

### Task 1: Bandera `reconocimientos_habilitado` en «Mi gimnasio»

**Files:**
- Modify: `tenants/models.py` (junto a `cuotas_con_mercadopago`, ~línea 411), `tenants/forms.py` (`GimnasioForm.Meta.fields`, ~136-150), `templates/tenants/gimnasio_form.html` (cerca de la línea 180)
- Create: `tenants/migrations/0017_gimnasio_reconocimientos_habilitado.py` (la genera `makemigrations`)
- Test: `tenants/tests_terminos.py` (clase `BanderaReconocimientosTests`)

**Interfaces:**
- Produces: `Gimnasio.reconocimientos_habilitado: bool` (`default=False, db_default=False`, verbose «Ranking y premios para los alumnos»).

- [ ] **Step 1: Tests que fallan** en `tenants/tests_terminos.py`. Copiar los helpers `_gimnasio`, `_staff`, `_alumno` de `tenants/tests_nav_alumno.py`. Casos:
  - `test_default_apagada`: `Gimnasio.objects.create(...)` → `reconocimientos_habilitado is False`.
  - `test_el_dueño_la_prende_desde_mi_gimnasio`: staff hace POST a `reverse("gimnasio_editar")` (**sin namespace**: `tenants/urls.py` no define `app_name` y no hay que agregarlo; ver el POST de `tenants/tests.py:244` como molde de campos obligatorios) con `reconocimientos_habilitado=on` → recarga el gimnasio y es `True`.
  - `test_un_post_sin_el_campo_la_apaga_como_cualquier_checkbox`: prender, y después POST sin el campo → queda `False`. Documenta la regla estándar de checkbox (igual que `cuotas_con_mercadopago`); no hay inmunidad a un POST viejo.
- [ ] **Step 2: Correr** `python manage.py test tenants.tests_terminos` → FAIL (campo inexistente).
- [ ] **Step 3: Implementar** el campo en `Gimnasio` (con comentario del porqué, mismo estilo que `cuotas_con_mercadopago`), agregarlo a `GimnasioForm.Meta.fields`, y en `gimnasio_form.html` incluir `partials/campo_form.html` con `campo=form.reconocimientos_habilitado` (ese partial ya renderiza el error; confirmarlo leyéndolo). `python manage.py makemigrations tenants -n gimnasio_reconocimientos_habilitado` y chequear que el AddField lleve `db_default=False`.
- [ ] **Step 4: Correr** `python manage.py test tenants.tests_terminos tenants.tests` → PASS.
- [ ] **Step 5: Commit** `feat(reconocimientos): bandera por gimnasio (apagada por defecto)`.

---

### Task 2: Campos de aceptación en `Perfil` y regla `debe_aceptar_terminos`

**Files:**
- Modify: `tenants/models.py` (`Perfil`)
- Create: `tenants/terminos.py`, migración `tenants/migrations/0018_perfil_terminos.py`
- Test: `tenants/tests_terminos.py` (clase `DebeAceptarTerminosTests`)

**Interfaces:**
- Produces: `Perfil.terminos_aceptados_en: DateTimeField(null=True, blank=True)`, `Perfil.terminos_version: PositiveSmallIntegerField(default=0, db_default=0)`; en `tenants/terminos.py`: `VERSION_TERMINOS = 1` y `def debe_aceptar_terminos(perfil: Perfil | None) -> bool`.

- [ ] **Step 1: Tests que fallan.** `debe_aceptar_terminos`:
  - `True`: perfil alumno, gimnasio con bandera prendida, `terminos_version=0`.
  - `False`: bandera apagada; rol staff; `perfil=None`; `terminos_version == VERSION_TERMINOS`.
  - `True` de nuevo si `terminos_version = VERSION_TERMINOS - 1` (se subió la versión; patchear `tenants.terminos.VERSION_TERMINOS` a 2 con `unittest.mock.patch`).
- [ ] **Step 2: Correr** → FAIL (módulo inexistente).
- [ ] **Step 3: Implementar** los campos, la migración (`python manage.py makemigrations tenants -n perfil_terminos`, para que se llame `0018_perfil_terminos.py`) y `debe_aceptar_terminos` (sin queries: lee `perfil.gimnasio`, que el request ya resolvió). `tenants/terminos.py` no importa nada de `plataforma`.
- [ ] **Step 4: Correr** → PASS.
- [ ] **Step 5: Commit** `feat(reconocimientos): aceptación de términos en el Perfil`.

---

### Task 3: Cartel de términos (middleware + vista + template)

**Files:**
- Modify: `tenants/forms.py` (`AceptarTerminosForm`), `tenants/views.py` (`AceptarTerminosView`), `tenants/urls.py`, `templates/base.html` (líneas ~269 y ~332), `config/settings.py` (`MIDDLEWARE`)
- Create: `plataforma/terminos.py` (`TerminosMiddleware`), `templates/tenants/terminos.html`
- Test: `tenants/tests_terminos.py` (clase `CartelTerminosTests`)

**Interfaces:**
- Consumes: `debe_aceptar_terminos` (Task 2); `perfil_de`, `APPS_SIN_BLOQUEO`, `URLS_SIN_BLOQUEO` de `plataforma/middleware.py` (existen con esos nombres); `suplantacion.esta_activa(request)` (= `CLAVE_SESION in request.session`, válido dentro de `process_view`).
- Produces: URL `terminos_aceptar` (sin namespace, `path("terminos/", ...)`), clase `plataforma.terminos.TerminosMiddleware`, variable de contexto `terminos_pendientes=True` que usa `base.html`, `AceptarTerminosForm` con `acepto = BooleanField(required=True, error_messages={"required": "Tenés que aceptar los términos para seguir."})`.

- [ ] **Step 1: Tests que fallan** (cliente de tests; bandera prendida salvo que se diga):
  - `test_alumno_sin_aceptar_ve_el_cartel_en_cualquier_pantalla`: GET `home` y GET `turnos:mis_turnos` → status 200 y el contenido incluye «Términos y condiciones» y no incluye el contenido normal de la pantalla; `Cache-Control` incluye `no-store`.
  - `test_bandera_apagada_no_muestra_nada`.
  - `test_staff_nunca_ve_el_cartel`.
  - `test_aceptar_guarda_fecha_y_version_y_no_vuelve_a_aparecer`: POST a `terminos_aceptar` con `acepto=on` → redirige a `home`; `perfil.terminos_aceptados_en` no es `None`, `terminos_version == VERSION_TERMINOS`; GET `home` → 200 sin cartel.
  - `test_sin_tildar_responde_200_con_el_error_en_el_campo`: POST sin `acepto` → 200, contiene el mensaje de error, no guarda.
  - `test_la_pantalla_de_login_y_la_privacidad_no_se_bloquean`: GET `politica_privacidad` anónimo y logueado → 200 con su contenido.
  - **Review Focus 1** `test_suplantando_no_muestra_el_cartel_ni_deja_aceptar`: `SuplantarView` es POST-only y exige staff: loguear al staff, `self.client.post(reverse("suplantar", args=[alumno.pk]))` (**sin namespace; el pk es el del `Alumno`**), y recién entonces GET `home` → contenido normal; POST a `terminos_aceptar` con `acepto=on` → `perfil.terminos_aceptados_en` sigue `None`.
  - **Review Focus 2** `test_cuenta_congelada_gana_el_cartel_de_bloqueo`: gimnasio creado con `estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS, estado_cuenta_desde=timezone.now()` (molde: `plataforma/tests_bloqueo.py:84-92`; `_gimnasio` de `tests_nav_alumno` no acepta dos veces el mismo slug, pasar `slug` propio si hace falta) → el cartel es el de `plataforma/cuenta_bloqueada.html`, no el de términos.
  - **Review Focus 8** `test_el_cartel_no_trae_el_menu_del_alumno`: el HTML del cartel no contiene `id="nav-alumno"` ni `aria-controls="nav-alumno"`.
  - **Review Focus 9** `test_perfil_alumno_sin_ficha_puede_aceptar`: Perfil de rol alumno sin `Alumno` → el POST con `acepto=on` guarda la aceptación y redirige.
  - `test_superusuario_nunca_ve_el_cartel`.
  - `test_un_post_a_otra_ruta_con_terminos_pendientes_redirige_a_home` (no escribe).
  - `test_los_webhooks_y_la_api_sin_sesion_pasan`: POST anónimo a `suscripcion:webhook` y a una ruta de `ingreso` con token inválido no devuelven el cartel (responden lo suyo).
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.**
  - `plataforma/terminos.py::TerminosMiddleware` con `process_view`, mismo esqueleto que `PlataformaMiddleware`: (1) allowlist por `match.app_name in APPS_SIN_BLOQUEO or match.url_name in URLS_SIN_BLOQUEO or match.url_name == "terminos_aceptar"` ANTES de leer `request.user`; (2) anónimo o `is_superuser` → `None` (simétrico a `PlataformaMiddleware`); (3) `suplantacion.esta_activa(request)` → `None`; (4) `perfil = perfil_de(request)`; si `not debe_aceptar_terminos(perfil)` → `None`; (5) GET/HEAD → `render(request, "tenants/terminos.html", {"gimnasio": perfil.gimnasio, "form": AceptarTerminosForm(), "terminos_pendientes": True}, status=200)` + `add_never_cache_headers`; otro método → `redirect("home")`. Importa `debe_aceptar_terminos` y `AceptarTerminosForm` dentro de la función si hace falta evitar ciclos. Docstring que explique el porqué de cada decisión (mismo estilo que `plataforma/middleware.py`). Los webhooks (`suscripcion`, `tienda`), la API de `ingreso` (token) y `solicitudes` no llevan sesión: `request.user` es anónimo y pasan por (2); no hace falta ampliar la allowlist.
  - **Orden en `MIDDLEWARE`**: inmediatamente DESPUÉS de `plataforma.middleware.PlataformaMiddleware`, con comentario («la cuenta congelada ve primero su cartel»). Como el `process_view` de `PlataformaMiddleware` devuelve respuesta antes, el de términos no corre para cuentas bloqueadas.
  - `templates/base.html`: agregar `and not terminos_pendientes` en las dos condiciones que hoy dicen `not cuenta_suspendida and not cuenta_alumnos_bloqueados` (el botón ☰ ~línea 269 y el `{% include 'partials/nav_alumno.html' %}` ~línea 332).
  - `AceptarTerminosView(LoginRequiredMixin, View)` (NO `AlumnoRequiredMixin`: ese 403 cuando el rol no es alumno y expone `self.alumno`, que acá no hace falta): resuelve `perfil = getattr(request.user, "perfil", None)`; `get` → mismo template; `post` → si `suplantacion.esta_activa(request)`, o no hay perfil, o `perfil.rol != ALUMNO` → `redirect("home")` sin escribir; valida el form; inválido → 200 con el form; válido → `perfil.terminos_aceptados_en = timezone.now(); perfil.terminos_version = VERSION_TERMINOS; perfil.save(update_fields=["terminos_aceptados_en", "terminos_version", "modificado"])` y `redirect("home")`. No exige ficha de `Alumno` (los campos viven en `Perfil`), así que un perfil sin ficha no queda atrapado.
  - `terminos.html`: extiende `base.html`; texto **borrador** de los términos (consentimiento para mostrar nombre + inicial del apellido y foto a los demás alumnos del gimnasio en el ranking y los reconocimientos, qué se muestra, que puede dejar de aparecer desde «Mi perfil», que los datos los trata su gimnasio, link a `politica_privacidad`) con un `{% comment %}` «TEXTO A REVISAR POR EL DUEÑO DEL PRODUCTO ANTES DE PRENDER LA BANDERA EN PRODUCCIÓN». El campo se renderiza con `{% include 'partials/campo_form.html' with campo=form.acepto %}` (ese partial ya muestra el error; `core/tests.py::ErroresDeFormularioVisiblesTests` rompe con un `{{ form.x }}` pelado).
- [ ] **Step 4: Correr** `python manage.py test tenants.tests_terminos tenants.tests_nav_alumno tenants.tests_portal_pantallas plataforma core` → PASS (`plataforma` confirma que no se rompió el bloqueo; `core` incluye el barrido de errores de formularios).
- [ ] **Step 5: Commit** `feat(reconocimientos): cartel de términos para el alumno (una sola vez)`.

---

### Task 4: Foto del alumno y su vista de servicio

**Files:**
- Modify: `alumnos/models.py` (`Alumno`), `alumnos/signals.py`, `alumnos/views.py`, `alumnos/urls.py`
- Create: `alumnos/fotos.py`, migración `alumnos/migrations/0008_alumno_foto_aparece_en_ranking.py`
- Test: `alumnos/tests_perfil.py` (clase `FotoAlumnoTests`)

**Interfaces:**
- Produces: `Alumno.foto = ImageField(upload_to="alumnos/fotos/", blank=True, editable=False)`; `Alumno.aparece_en_ranking = BooleanField(default=True, db_default=True)`; propiedad `Alumno.foto_url -> str` (`""` sin foto; si no, `reverse("alumnos:foto_alumno", args=[self.pk]) + "?v=" + <nombre del archivo>`); `alumnos.fotos.guardar_foto(alumno: Alumno, archivo) -> None` (valida y re-codifica con `tienda.fotos.preparar_foto`, guarda el archivo nuevo, escribe la columna con `Alumno.objects.filter(pk=alumno.pk).update(foto=nombre, modificado=timezone.now())` —**no** `alumno.save()`: dispararía las señales de `Alumno` (`registrar_fecha_de_baja` hace un SELECT extra, `sincronizar_acceso_con_estado` lee `perfil.usuario`)— y borra la foto anterior con `transaction.on_commit`; deja pasar `forms.ValidationError`); `alumnos.fotos.quitar_foto(alumno) -> None`; URL `alumnos:foto_alumno` = `path("<int:pk>/foto/", AlumnoFotoView.as_view())`.

- [ ] **Step 1: Tests que fallan** (storage de tests = `InMemoryStorage`; generar imágenes con `PIL.Image.new` + `ContentFile`/`SimpleUploadedFile`):
  - `test_guardar_foto_recodifica_a_jpeg_con_nombre_uuid`: sube un PNG de 400×400 → `alumno.foto.name` empieza con `alumnos/fotos/` y termina en `.jpg`.
  - **Review Focus 3** `test_rechaza_archivo_que_no_es_imagen_y_muy_chica_y_muy_pesada`: `guardar_foto` con bytes de texto, con PNG 100×100 y con un archivo > 8 MB levanta `forms.ValidationError` y no deja archivo guardado.
  - **Review Focus 6** `test_reemplazar_borra_la_anterior`, `test_quitar_foto_borra_el_archivo`, `test_borrar_el_alumno_borra_el_archivo` (verificar con `default_storage.exists`). El borrado va con `transaction.on_commit`, así que los tres tests corren con `with self.captureOnCommitCallbacks(execute=True):` (molde: `calendario/tests.py:312`). Agregar `test_si_la_transaccion_se_revierte_el_archivo_no_se_borra` (el borrado diferido no corre si hay rollback, p. ej. `vaciar_gimnasio` corre dentro de `transaction.atomic()`).
  - `test_foto_url_vacia_sin_foto_y_versionada_con_foto`.
  - Vista (`AlumnoFotoView`, mixin `LoginRequiredMixin`): staff del mismo gimnasio → 200 `image/jpeg` con `Cache-Control: private, max-age=3600` (**no** `immutable`: la URL versionada ayuda a reemplazar pero no a revocar; si el alumno quita la foto, apaga el ranking o el dueño apaga la bandera, el navegador de un compañero no debe conservarla un año); alumno del mismo gimnasio → 200 (la foto es visible entre compañeros) **solo si `gimnasio.reconocimientos_habilitado` y el dueño de la foto tiene `aparece_en_ranking`, o si es la foto propia**; **Review Focus 4** alumno/staff de OTRO gimnasio → 404 con `Cache-Control: no-store`; anónimo → redirige al login; alumno sin foto → 404 `no-store`; con la cuenta congelada la respuesta es el cartel de plataforma (no está en la allowlist).
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar** campos + migración; `alumnos/fotos.py` con **import tardío** de `tienda.fotos.preparar_foto` dentro de la función (evita ciclo `alumnos`↔`tienda`); handler `post_delete` de `Alumno` en `alumnos/signals.py` que, si `instance.foto` no está vacío, hace `transaction.on_commit(lambda: storage.delete(nombre))` (captura `nombre` y `storage` antes) y no falla si el archivo ya no existe (`try/except` acotado + `logger.warning`). Cubre también `vaciar_gimnasio` y `AlumnoDeleteView`: ambos borran por instancia, así que Django dispara `post_delete` por cada alumno; no hace falta código extra en `vaciar_gimnasio`; `AlumnoFotoView`: resuelve el alumno con `Alumno.objects.filter(pk=pk, gimnasio=perfil.gimnasio)` (si el usuario no tiene perfil → 404), aplica la regla de visibilidad de arriba, lee bytes con `with alumno.foto.open("rb")`, `mimetypes.guess_type`, y devuelve 404 con `no-store` en todos los demás casos (mismo `_no_existe` que `FotoProductoView`).
- [ ] **Step 4: Correr** `python manage.py test alumnos tenants.tests` → PASS.
- [ ] **Step 5: Commit** `feat(reconocimientos): foto del alumno (R2, vista privada del gimnasio)`.

---

### Task 5: Pantalla «Mi perfil» y menú

**Files:**
- Modify: `alumnos/forms.py`, `alumnos/views.py`, `alumnos/urls.py`, `templates/partials/nav_alumno.html`
- Create: `templates/alumnos/mi_perfil.html`
- Test: `alumnos/tests_perfil.py` (clase `MiPerfilTests`); ampliar `tenants/tests_nav_alumno.py`

**Interfaces:**
- Consumes: `guardar_foto`, `quitar_foto` (Task 4).
- Produces: URL `alumnos:mi_perfil` = `path("mi-perfil/", MiPerfilView.as_view())` (declararla ANTES de las rutas con `<int:pk>`); `MiPerfilForm(forms.Form)` con `foto = forms.FileField(required=False)` (**FileField y no ImageField**: Django validaría con Pillow antes y el mensaje saldría distinto del de `guardar_foto`; así `preparar_foto` es la única fuente de mensajes; el `<input>` lleva `accept="image/jpeg,image/png"` y un texto de ayuda: HEIC/WebP se rechazan), `quitar_foto = BooleanField(required=False)`, `aparece_en_ranking = BooleanField(required=False)`; `MiPerfilView(AlumnoRequiredMixin, FormView)`.

- [ ] **Step 1: Tests que fallan:**
  - `test_get_muestra_el_formulario_con_el_estado_actual` (foto actual y el tilde de `aparece_en_ranking` precargado).
  - `test_post_cambia_aparece_en_ranking_y_sube_foto`: POST multipart con foto válida y sin `aparece_en_ranking` → queda `False`, foto guardada, redirige a `alumnos:mi_perfil`.
  - `test_quitar_foto`.
  - **Review Focus 3** `test_foto_invalida_responde_200_con_el_error_en_el_campo` (texto con extensión `.jpg`; PNG 100×100): contiene el mensaje, no hay 500, la foto anterior queda intacta.
  - **Review Focus 5** `test_perfil_sin_ficha_get_vacio_y_post_403`.
  - `test_bandera_apagada_da_404` y `test_staff_da_403`.
  - Nav: con bandera prendida el alumno ve «Mi perfil» con `aria-current="page"` en esa pantalla; con la bandera apagada no aparece.
  - `test_el_formulario_sube_archivos_sin_hx_boost`: el HTML contiene `enctype="multipart/form-data"` y `hx-boost="false"`.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.** `MiPerfilForm.clean_foto` no valida contenido (lo hace `guardar_foto`); `form_valid` llama `guardar_foto` dentro de `try/except forms.ValidationError as e: form.add_error("foto", e)` y re-renderiza (200). **No** sobreescribir `dispatch` (correría antes que el de `AlumnoRequiredMixin`: `self.perfil` aún no existe y un anónimo daría 500). La comprobación va en `get` y en `post`, en este orden: si `not self.gimnasio.reconocimientos_habilitado` → `Http404`; y en `post`, después, si `self.alumno is None` → `PermissionDenied`. Template: campo por campo con `{% include 'partials/campo_form.html' with campo=form.<campo> %}` (ese partial renderiza el error; un `{{ form.x }}` pelado rompe `core/tests.py::ErroresDeFormularioVisiblesTests`); muestra la foto actual (`alumno.foto_url`) con alt; texto que explique qué ven los compañeros («nombre y la inicial de tu apellido»). Menú: en `nav_alumno.html` agregar `{% if user.perfil.gimnasio.reconocimientos_habilitado %}<a href="{% url 'alumnos:mi_perfil' %}"{% if namespace == "alumnos" and url_name == "mi_perfil" %} aria-current="page"{% endif %}>Mi perfil</a>{% endif %}` (cero queries nuevas: el gimnasio ya está resuelto).
- [ ] **Step 4: Correr** `python manage.py test alumnos tenants.tests_nav_alumno core` → PASS.
- [ ] **Step 5: Commit** `feat(reconocimientos): pantalla «Mi perfil» del alumno`.

---

### Task 6: Privacidad, demo, documentación y suite completa

**Files:**
- Modify: `templates/tenants/privacidad.html`, `tenants/demo.py`, `ARQUITECTURA.md`, `CLAUDE.md` (tabla), `ISSUES.md`
- Test: `tenants/tests.py` (demo), `tenants/tests_terminos.py`

**Interfaces:**
- Consumes: todo lo anterior.

- [ ] **Step 1: Tests que fallan:** (a) `restaurar_demo` deja `reconocimientos_habilitado == False` aunque antes estuviera prendida (un prospecto la prende en la demo compartida); (b) tras `sembrar_demo`/`restaurar_demo`, los alumnos demo con acceso tienen `terminos_version == VERSION_TERMINOS`; (c) `vaciar_gimnasio` borra los archivos de foto de los alumnos del gimnasio (alumno con foto, vaciar con `captureOnCommitCallbacks(execute=True)`, `default_storage.exists` es `False`; esto prueba el handler de la Task 4, sin código nuevo en `vaciar_gimnasio`); (d) `privacidad.html` menciona «foto» y «ranking».
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.** `tenants/demo.py`: agregar `"reconocimientos_habilitado": False` a `_GIMNASIO_CANONICO` (~línea 920; `restaurar_demo` lo reaplica con `setattr` + `save`). Los `Perfil` demo los crea `alumnos.services.crear_acceso` (llamado desde `demo.py` ~línea 422), **no** `Perfil.objects.create`: después de `crear_acceso`, `Perfil.objects.filter(pk=alumno.perfil_id).update(terminos_aceptados_en=timezone.now(), terminos_version=VERSION_TERMINOS)`. `privacidad.html`: agregar los datos nuevos (foto, nombre + inicial en el ranking, premios), cuándo se muestran y cómo dejar de aparecer, y actualizar «Última actualización» a la fecha del día (`timezone.localdate()` al implementar). Documentar: sección «Reconocimientos (ranking y premios)» en `ARQUITECTURA.md` (por qué el cartel es middleware y no mixin, el orden respecto de `PlataformaMiddleware`, la regla de suplantación, la foto privada), fila en la tabla de `CLAUDE.md`, y entrada en `ISSUES.md` con el texto de los términos pendiente de revisión del dueño.
- [ ] **Step 4: Verificación completa.** `git status -sb` (cero archivos nuevos sin trackear que falten); `python manage.py makemigrations --check --dry-run` → «No changes detected»; `python manage.py test` completo → verde (esperado: baseline 1723 + los nuevos). Probar en el navegador con `runserver`: bandera prendida, alumno nuevo ve el cartel, acepta, no vuelve a verlo; subir una foto desde el celular (iframe de 390 px); staff suplanta y no ve el cartel. Si algún `.js`/`.css` no cambia, sospechar del service worker.
- [ ] **Step 5: Commit** `feat(reconocimientos): privacidad, demo y documentación de la pieza 1`.

---

## Self-review (hecha)

- **Cobertura del spec (Pieza 1):** bandera (T1), campos de T&C (T2), middleware con orden y suplantación (T3), texto + `privacidad.html` (T3, T6), foto privada con `preparar_foto` y limpieza en R2 (T4, T6), `aparece_en_ranking` y «Mi perfil» + nav (T5), demo (T6). El spec pide `aparece_en_ranking` «tras aceptar»: el default `True` + el cartel previo cumple el consentimiento; el opt-out vive en T5.
- **Consistencia de nombres:** `debe_aceptar_terminos`, `VERSION_TERMINOS`, `terminos_aceptar`, `guardar_foto`/`quitar_foto`, `foto_url`, `alumnos:foto_alumno`, `alumnos:mi_perfil` se usan igual en todas las tareas.
- **Decisiones mías a validar:** el cartel solo corre con la bandera prendida (para no mostrar términos que hablan de un ranking inexistente); la foto del compañero solo se sirve a alumnos del mismo gimnasio con la bandera prendida y `aparece_en_ranking`; el texto de los términos es un borrador a revisar antes de producción.
- **Segunda pasada adversarial (hecha):** corrigió 3 bloqueantes (URL names sin namespace `tenants`, menú del alumno visible bajo el cartel, `dispatch` de `MiPerfilView`) y 9 puntos más (checkbox de la bandera, `FileField`, `on_commit` en el borrado de archivos, `update()` en vez de `save()`, demo con `crear_acceso` y `_GIMNASIO_CANONICO`, caché corta de la foto, middleware en `plataforma/`, `-n perfil_terminos`). Confirmó que la allowlist alcanza (webhooks, API de ingreso y `solicitudes` no llevan sesión) y que el orden de `MIDDLEWARE` hace ganar el cartel de bloqueo.
- **Pendiente de decisión de producto (no bloquea):** las hojas `alumnos` y `gimnasio` del exportador no incluyen `aparece_en_ranking`, el archivo de la foto ni la bandera. Agregarlas es seguro; por defecto no se agregan en esta pieza.
- **Fuera de esta pieza:** el ranking (Pieza 2) lee `aparece_en_ranking` y `terminos_version`; el mini-perfil de compañero y las insignias llegan con la Pieza 5.
