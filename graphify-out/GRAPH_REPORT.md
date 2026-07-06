# Graph Report - .  (2026-07-06)

## Corpus Check
- Corpus is ~26,360 words - fits in a single context window. You may not need a graph.

## Summary
- 660 nodes · 1326 edges · 66 communities (50 shown, 16 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 140 edges (avg confidence: 0.71)
- Token cost: 420,205 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Alumnos Gestión y Accesos|Alumnos: Gestión y Accesos]]
- [[_COMMUNITY_Rutinas Modelos y Admin|Rutinas: Modelos y Admin]]
- [[_COMMUNITY_Docs Decisiones e Issues|Docs: Decisiones e Issues]]
- [[_COMMUNITY_Biblioteca de Ejercicios|Biblioteca de Ejercicios]]
- [[_COMMUNITY_Tests de Aislamiento|Tests de Aislamiento]]
- [[_COMMUNITY_Portal del Alumno|Portal del Alumno]]
- [[_COMMUNITY_Gestión de Novedades|Gestión de Novedades]]
- [[_COMMUNITY_AppConfigs Django|AppConfigs Django]]
- [[_COMMUNITY_Núcleo Multi-tenant|Núcleo Multi-tenant]]
- [[_COMMUNITY_Vistas de Rutinas|Vistas de Rutinas]]
- [[_COMMUNITY_Build Tailwind CSS|Build Tailwind CSS]]
- [[_COMMUNITY_Tenant Gimnasio y Perfil|Tenant: Gimnasio y Perfil]]
- [[_COMMUNITY_Tenant Scoping (Mixins)|Tenant Scoping (Mixins)]]
- [[_COMMUNITY_White-label y Personalización|White-label y Personalización]]
- [[_COMMUNITY_Tests Vistas Rutinas|Tests Vistas Rutinas]]
- [[_COMMUNITY_Tests Tenancy Base|Tests Tenancy Base]]
- [[_COMMUNITY_Cron Generar Pagos|Cron Generar Pagos]]
- [[_COMMUNITY_Modelo PagoMensual|Modelo PagoMensual]]
- [[_COMMUNITY_Items de Plantilla|Items de Plantilla]]
- [[_COMMUNITY_Modelo Novedad|Modelo Novedad]]
- [[_COMMUNITY_Tests Vistas Alumnos|Tests Vistas Alumnos]]
- [[_COMMUNITY_Visibilidad de Novedades|Visibilidad de Novedades]]
- [[_COMMUNITY_Vistas de Pagos|Vistas de Pagos]]
- [[_COMMUNITY_Tests TenantScopedMixin|Tests TenantScopedMixin]]
- [[_COMMUNITY_Tests Vistas Pagos|Tests Vistas Pagos]]
- [[_COMMUNITY_Registro Self-serve|Registro Self-serve]]
- [[_COMMUNITY_CRUD Plantillas Rutina|CRUD Plantillas Rutina]]
- [[_COMMUNITY_Ocultar Novedad|Ocultar Novedad]]
- [[_COMMUNITY_Vencimiento de Pagos|Vencimiento de Pagos]]
- [[_COMMUNITY_Asignación de Rutinas|Asignación de Rutinas]]
- [[_COMMUNITY_Tests White-label|Tests White-label]]
- [[_COMMUNITY_Señal de Activación|Señal de Activación]]
- [[_COMMUNITY_Deploy Render + R2|Deploy Render + R2]]
- [[_COMMUNITY_URLconfs de Apps|URLconfs de Apps]]
- [[_COMMUNITY_Tests Staff Novedades|Tests Staff Novedades]]
- [[_COMMUNITY_Tests Modelo Pago|Tests Modelo Pago]]
- [[_COMMUNITY_Settings Django|Settings Django]]
- [[_COMMUNITY_Entry Point manage.py|Entry Point manage.py]]
- [[_COMMUNITY_Migración Alumnos Inicial|Migración Alumnos Inicial]]
- [[_COMMUNITY_Migración Acceso Alumno|Migración Acceso Alumno]]
- [[_COMMUNITY_Config ASGI|Config ASGI]]
- [[_COMMUNITY_URLs Raíz|URLs Raíz]]
- [[_COMMUNITY_Config WSGI|Config WSGI]]
- [[_COMMUNITY_Migración Inicial (app)|Migración Inicial (app)]]
- [[_COMMUNITY_Migración Inicial (app)|Migración Inicial (app)]]
- [[_COMMUNITY_Migración Inicial (app)|Migración Inicial (app)]]
- [[_COMMUNITY_Módulo Auxiliar|Módulo Auxiliar]]

## God Nodes (most connected - your core abstractions)
1. `Alumno` - 68 edges
2. `TenantScopedMixin` - 51 edges
3. `StaffRequiredMixin` - 40 edges
4. `Perfil` - 33 edges
5. `Gimnasio` - 28 edges
6. `PagoMensual` - 27 edges
7. `templates/base.html (layout global)` - 27 edges
8. `RutinaAsignada` - 26 edges
9. `CrearAccesoView` - 25 edges
10. `CrearAccesoForm` - 24 edges

## Surprising Connections (you probably didn't know these)
- `templates/alumnos/alumno_list.html` --shares_data_with--> `Alumno`  [INFERRED]
  templates/alumnos/alumno_list.html → alumnos/models.py
- `Retiro soft de ejercicios via activo=False (sin vista de borrado)` --semantically_similar_to--> `NovedadOcultarView`  [INFERRED] [semantically similar]
  ejercicios/views.py → novedades/views.py
- `CrearAccesoView` --references--> `templates/alumnos/acceso_form.html (crear acceso / cambiar contraseña)`  [INFERRED]
  alumnos/views.py → templates/alumnos/acceso_form.html
- `TenantScopedMixin` --semantically_similar_to--> `StaffRequiredMixin`  [INFERRED] [semantically similar]
  core/mixins.py → tenants/mixins.py
- `Ejercicio` --semantically_similar_to--> `Novedad`  [INFERRED] [semantically similar]
  ejercicios/models.py → novedades/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Flujo de acceso del alumno Fase 3 (alta de credenciales por el staff + activación en primer login)** — alumnos_views_crearaccesoview, alumnos_views_cambiarpasswordalumnoview, alumnos_forms_crearaccesoform, alumnos_forms_cambiarpasswordalumnoform, alumnos_models_alumno, tenants_models_perfil, alumnos_signals_registrar_primera_activacion [EXTRACTED 1.00]
- **Patrón StaffRequiredMixin + TenantScopedMixin en todas las vistas de gestión de alumnos** — tenants_mixins_staffrequiredmixin, core_mixins_tenantscopedmixin, alumnos_views_alumnolistview, alumnos_views_alumnocreateview, alumnos_views_alumnoupdateview, alumnos_views_alumnodetailview, alumnos_views_alumnotoggleestadoview, alumnos_views_crearaccesoview, alumnos_views_cambiarpasswordalumnoview [EXTRACTED 1.00]
- **Pipeline de deploy Fase 5 (Render Blueprint + Postgres + WhiteNoise + Cloudflare R2 + CSS precompilado)** — render_blueprint, config_settings_storages, config_wsgi_application, requirements_dependencies, package_build_css [EXTRACTED 1.00]
- **Row-level multi-tenant isolation mechanism (model + view + form layers)** — core_models_tenantownedmodel, core_models_tenantqueryset, core_mixins_tenantscopedmixin, core_forms_tenantscopedmodelform [EXTRACTED 1.00]
- **Repeated pattern: user without Perfil raises PermissionDenied (403, not 500)** — core_mixins_tenantscopedmixin, tenants_mixins_staffrequiredmixin, tenants_views_homeview [INFERRED 0.95]
- **Self-serve gym registration flow (form -> atomic User+Gimnasio+Perfil -> login)** — tenants_forms_registroform, tenants_views_registerview, tenants_models_gimnasio, tenants_models_perfil [EXTRACTED 1.00]
- **Vistas de gestion staff: StaffRequiredMixin + TenantScopedMixin en toda vista de dominio** — ejercicios_views_ejerciciolistview, ejercicios_views_ejerciciocreateview, ejercicios_views_ejercicioupdateview, novedades_views_novedadlistview, novedades_views_novedadcreateview, novedades_views_novedadupdateview, novedades_views_novedadocultarview, tenants_mixins_staffrequiredmixin, core_mixins_tenantscopedmixin [EXTRACTED 1.00]
- **Regla 'visible ahora' de Novedad: definida una vez en NovedadQuerySet.visibles() y reusada por vista y tests** — novedades_models_regla_visibles, novedades_models_novedadqueryset, novedades_views_novedadlistview, novedades_tests_novedadvisiblestests, novedades_tests_novedadlistadovisibleahoratests [EXTRACTED 1.00]
- **Patron de tests de aislamiento por gimnasio (referencia tenants/tests.py::TenantIsolationTests)** — ejercicios_tests_ejerciciotenantisolationtests, novedades_tests_novedadtenantisolationtests, novedades_tests_novedadtenantisolationviewstests, core_models_tenantqueryset [EXTRACTED 1.00]
- **Ciclo mensual de pagos autogenerados por cron** — pagos_management_commands_generar_pagos_command, pagos_models_generar_pagos_pendientes, pagos_models_marcar_vencidos, pagos_models_pagomensual, pagos_views_confirmarpagoview [EXTRACTED 1.00]
- **Flujo de asignacion de rutina por snapshot congelado** — rutinas_views_asignarrutinaview, rutinas_forms_asignarrutinaform, rutinas_models_rutinaplantilla, rutinas_models_rutinaasignada, rutinas_models_rutinaasignadaitem [EXTRACTED 1.00]
- **Patron StaffRequiredMixin + TenantScopedMixin en vistas de gestion** — tenants_mixins_staffrequiredmixin, core_mixins_tenantscopedmixin, pagos_views_pagomensuallistview, pagos_views_confirmarpagoview, rutinas_views_rutinaplantillalistview, rutinas_views_rutinaplantillacreateview, rutinas_views_rutinaplantillaupdateview, rutinas_views_rutinaplantilladetailview, rutinas_views_rutinaplantilladuplicarview, rutinas_views_asignarrutinaview, rutinas_views_rutinaasignadadetailview [EXTRACTED 1.00]
- **Templates anchos que sobreescriben el block main_class a contenido--ancho** — templates_base_block_main_class, templates_alumnos_alumno_list, templates_alumnos_alumno_detail, templates_ejercicios_ejercicio_list, templates_novedades_novedad_list, templates_pagos_pago_list, templates_rutinas_plantilla_list, templates_rutinas_plantilla_detail, templates_rutinas_asignada_detail, templates_tenants_home [EXTRACTED 1.00]
- **Fase 5: deploy a Render + R2 y sus issues asociados** — app_gim_roadmap_fase_5, app_gim_roadmap_object_storage_r2, app_gim_issues_free_tier_render, app_gim_issues_input_css_collectstatic, app_gim_issues_whitenoise_manifest_debug [EXTRACTED 1.00]
- **Patrón compartido de badges de estado de pago (pagado/pendiente/vencido)** — templates_tenants_home, templates_pagos_pago_list, templates_alumnos_alumno_detail, pagos_models_pagomensual [INFERRED 0.85]

## Communities (66 total, 16 thin omitted)

### Community 0 - "Alumnos: Gestión y Accesos"
Cohesion: 0.05
Nodes (41): AlumnoAdmin, AlumnoForm, CambiarPasswordAlumnoForm, CrearAccesoForm, Meta, Form de alta/edición de alumnos de un gimnasio.  `fecha_activacion` queda afuera, Alta del login (usuario/contraseña) de un alumno que todavía no     tiene uno. V, Reseteo de la contraseña de un alumno que ya tiene login. Mismo     criterio de (+33 more)

### Community 1 - "Rutinas: Modelos y Admin"
Cohesion: 0.06
Nodes (36): Audita creación y última modificación de cualquier fila.      Se separa de la ló, TimeStampedModel, RutinaAsignadaAdmin, RutinaAsignadaItemAdmin, RutinaAsignadaItemInline, RutinaPlantillaAdmin, RutinaPlantillaItemAdmin, RutinaPlantillaItemInline (+28 more)

### Community 2 - "Docs: Decisiones e Issues"
Cohesion: 0.06
Nodes (46): ISSUES.md (registro de problemas y riesgos), Issue: arranque en free tier de Render (Postgres expira a 90 días, sin cron), Issue: input.css dentro de static/ rompía collectstatic, Issue: generar_pagos_pendientes crea PagoMensual con monto=0, Decisión Fase 4: redefinir clases existentes con @apply en vez de reescribir templates, Issue: manifest de WhiteNoise rompía {% static %} en dev/tests, REUSO.md (Fase 0: extracción del esqueleto), gestor-pedidos (repo fuente real del esqueleto Django) (+38 more)

### Community 3 - "Biblioteca de Ejercicios"
Cohesion: 0.07
Nodes (22): EjercicioAdmin, EjercicioForm, Meta, Form de alta/edición de ejercicios de la biblioteca de un gimnasio.  Hereda de `, Biblioteca de ejercicios por gimnasio (no global), Ejercicio, GrupoMuscular, Meta (+14 more)

### Community 4 - "Tests de Aislamiento"
Cohesion: 0.09
Nodes (13): EjercicioTenantIsolationTests, Confirma que la biblioteca de ejercicios de un gimnasio no se mezcla     con la, NovedadModelTests, NovedadTenantIsolationTests, NovedadTenantIsolationViewsTests, NovedadViewsAccesoTests, NovedadVisiblesTests, Tests de Fase 1 para el modelo `Novedad`: creación básica, la regla de "visible (+5 more)

### Community 5 - "Portal del Alumno"
Cohesion: 0.12
Nodes (8): Late (in-method) imports of domain apps in HomeView, LoginRequiredMixin, HomeViewAlumnoTests, LoginLogoutTests, Portal del alumno (Fase 3): rutina activa, cuota del mes, novedades., HomeView, Página de inicio tras login.      Para `staff` es el dashboard de Fase 2 §1 (alu, Datos del portal de Fase 3: rutina activa, cuota del mes y novedades.          `

### Community 6 - "Gestión de Novedades"
Cohesion: 0.18
Nodes (10): Meta, NovedadForm, Form de alta/edición de novedades (comunicados) de un gimnasio.  Incluye `activa, URLs de gestión de novedades (Fase 2).  No se incluye acá en `config/urls.py` --, NovedadCreateView, NovedadUpdateView, Vistas de gestión (Fase 2) de novedades: publicar, editar y ocultar avisos para, Autorización por rol en la capa de vista.  Separado de `core.mixins.TenantScoped (+2 more)

### Community 7 - "AppConfigs Django"
Cohesion: 0.12
Nodes (8): AlumnosConfig, AppConfig, CoreConfig, EjerciciosConfig, NovedadesConfig, PagosConfig, RutinasConfig, TenantsConfig

### Community 8 - "Núcleo Multi-tenant"
Cohesion: 0.18
Nodes (12): CLAUDE.md (app_gim project guide), Multi-tenancy: base compartida + aislamiento por fila (FK gimnasio), FK-injection prevention via tenant-scoped form querysets, Shared DB + row-level tenant isolation via gimnasio FK, Form-base que cierra el hueco de FK-injection.  Stampar `gimnasio` en el objeto, TenantScopedModelForm, Meta, Abstracciones compartidas por todo el dominio.  Esta app NO define tablas propia (+4 more)

### Community 9 - "Vistas de Rutinas"
Cohesion: 0.19
Nodes (10): DetailView, RutinaPlantillaItemForm, URLs de gestión de rutinas (Fase 2): plantillas, sus items, duplicar y asignació, Vistas de gestión (Fase 2) de rutinas: CRUD de plantillas, alta/edición/borrado, POST-only: crea una copia independiente de la plantilla (y sus items)     vía `R, RutinaAsignadaDetailView, RutinaPlantillaDetailView, RutinaPlantillaDuplicarView (+2 more)

### Community 10 - "Build Tailwind CSS"
Cohesion: 0.13
Nodes (14): author, description, devDependencies, tailwindcss, @tailwindcss/cli, keywords, license, main (+6 more)

### Community 11 - "Tenant: Gimnasio y Perfil"
Cohesion: 0.19
Nodes (9): Perfil: composition over inheritance for auth User, GimnasioAdmin, PerfilAdmin, tenants migration 0001_initial, tenants migration 0002 (white-label fields), Gimnasio, Perfil, Un gimnasio/entrenador que usa el sistema. Unidad de aislamiento de     datos (t (+1 more)

### Community 12 - "Tenant Scoping (Mixins)"
Cohesion: 0.15
Nodes (8): Explicit tenant filtering (for_gimnasio) over thread-local middleware, Authorization (role) separated from tenant isolation, Server-side gimnasio stamping in form_valid, Scoping de tenant en la capa de vista (no en el modelo).  Decisión (ver CLAUDE.m, Gimnasio del usuario autenticado. 403 si no tiene Perfil.          El panel oper, TenantScopedMixin, QuerySet con filtrado de tenant EXPLÍCITO.      Decisión: NO usamos thread-local, TenantQuerySet

### Community 13 - "White-label y Personalización"
Cohesion: 0.20
Nodes (10): White-label per-gym customization, GimnasioForm, Meta, Forms de `tenants`: registro (alta de un gimnasio) y personalización white-label, Personalización del gimnasio (Fase 4, "Personalización por     gimnasio"). No es, RegistroForm, GimnasioUpdateView, Vista de registro: alta self-serve de un gimnasio nuevo (rol staff/dueño), y vis (+2 more)

### Community 15 - "Tests Tenancy Base"
Cohesion: 0.19
Nodes (7): TemplateView, Tests de Fase 0: registro, login y aislamiento básico de datos entre gimnasios., Vista mínima de prueba; no se registra en urls., Confirma que dos gimnasios no comparten datos ni perfiles., StaffRequiredMixinTests, TenantIsolationTests, _VistaDeStaff

### Community 16 - "Cron Generar Pagos"
Cohesion: 0.22
Nodes (7): BaseCommand, management command generar_pagos, Command de management que dispara la autogeneración mensual de pagos.  Este es e, generar_pagos_pendientes(), Crea un `PagoMensual` PENDIENTE para cada alumno activo de cada     gimnasio act, GenerarPagosPendientesTests, Tests de Fase 1 para `PagoMensual`: creación básica, unicidad por (gimnasio, alu

### Community 17 - "Modelo PagoMensual"
Cohesion: 0.19
Nodes (9): PagoMensualAdmin, Meta, Form de confirmación de pago (Fase 2 §6).  El staff NUNCA crea un `PagoMensual`, Estado, Meta, PagoMensual, Modelo de dominio: pagos mensuales de cada alumno.  `PagoMensual` es un `TenantO, La cuota de un alumno para un mes/año calendario puntual.      `unique_together` (+1 more)

### Community 18 - "Items de Plantilla"
Cohesion: 0.21
Nodes (6): ItemPlantillaMixin, Mixin común a los views de `RutinaPlantillaItem`.      Resuelve la `RutinaPlanti, POST-only: no hay página de confirmación por GET, el botón de borrar     ya es l, RutinaPlantillaItemDeleteView, RutinaPlantillaItemUpdateView, UpdateView

### Community 19 - "Modelo Novedad"
Cohesion: 0.21
Nodes (8): NovedadAdmin, Migration, _hoy(), Meta, Novedad, Comunicados que el staff publica para sus alumnos (avisos de gimnasio cerrado, c, Aviso publicado por el staff de un gimnasio.      `fecha_publicacion` es editabl, templates/novedades/novedad_list.html

### Community 21 - "Visibilidad de Novedades"
Cohesion: 0.20
Nodes (7): NovedadQuerySet, Extiende `TenantQuerySet` (core) con reglas propias de `Novedad`.      No se agr, Novedades que corresponde mostrarle HOY a un alumno.          "Visible" = activa, Regla de visibilidad de Novedad (visibles()), NovedadListadoVisibleAhoraTests, El listado marca correctamente qué novedades son visibles ahora,     reusando `N, NovedadListView

### Community 22 - "Vistas de Pagos"
Cohesion: 0.25
Nodes (6): ConfirmarPagoForm, Pagos autogenerados por cron (staff solo confirma), URLs de gestión de pagos mensuales (Fase 2 §6).  No se incluye acá en `config/ur, ConfirmarPagoView, PagoMensualListView, Vistas de gestión (Fase 2 §6) de pagos mensuales de un gimnasio.  Solo staff (`S

### Community 23 - "Tests TenantScopedMixin"
Cohesion: 0.31
Nodes (4): _AlumnoListView, Tests de `TenantScopedMixin` (capa de vista) contra un `TenantOwnedModel` de dom, Vista mínima de prueba; no se registra en urls., TenantScopedMixinTests

### Community 25 - "Registro Self-serve"
Cohesion: 0.25
Nodes (4): Atomic self-serve gym registration, templates/registration/register.html (registrar gimnasio), RegisterViewTests, RegisterView

### Community 26 - "CRUD Plantillas Rutina"
Cohesion: 0.25
Nodes (4): CreateView, RutinaPlantillaForm, RutinaPlantillaCreateView, RutinaPlantillaUpdateView

### Community 27 - "Ocultar Novedad"
Cohesion: 0.25
Nodes (4): NovedadOcultarViewTests, El atajo de un clic "ocultar" solo acepta POST y apaga `activa`., NovedadOcultarView, Atajo de un clic desde el listado: pone `activa=False` sin abrir el     form com

### Community 28 - "Vencimiento de Pagos"
Cohesion: 0.46
Nodes (3): marcar_vencidos(), Pasa a VENCIDO todo `PagoMensual` PENDIENTE cuyo mes/año sea     estrictamente a, MarcarVencidosTests

### Community 29 - "Asignación de Rutinas"
Cohesion: 0.29
Nodes (5): FormView, AsignarRutinaForm, Elegir alumno + plantilla + fecha de inicio para generar el snapshot     (ROADMA, AsignarRutinaView, `RutinaAsignada` no se crea vía `form.save()` (no es un `ModelForm`):     la cre

### Community 31 - "Señal de Activación"
Cohesion: 0.33
Nodes (4): Registra `Alumno.fecha_activacion` en el primer login exitoso.  Se activa acá (s, Meta, Núcleo de la arquitectura multi-tenant.  `Gimnasio` ES el tenant. `Perfil` conec, Rol

### Community 32 - "Deploy Render + R2"
Cohesion: 0.33
Nodes (6): Config R2 todo-o-nada (fail-fast si faltan variables), STORAGES / configuración R2-WhiteNoise, WSGI application, npm scripts build:css/watch:css (Tailwind), Render Blueprint (web free + Postgres free, cron comentado), requirements.txt (Django 5.2 + stack Fase 5: gunicorn, whitenoise, django-storages, psycopg, dj-database-url)

### Community 33 - "URLconfs de Apps"
Cohesion: 0.33
Nodes (6): URLconf raíz, ejercicios URLconf, novedades URLconf, pagos URLconf, rutinas URLconf, tenants URLconf

### Community 36 - "Settings Django"
Cohesion: 0.50
Nodes (3): _env_bool(), Django settings for config project.  Generated by 'django-admin startproject' us, Lee un booleano de entorno de forma tolerante ("1"/"true"/"yes"/"on").

## Knowledge Gaps
- **54 isolated node(s):** `Migration`, `Migration`, `Estado`, `Meta`, `Meta` (+49 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Alumno` connect `Alumnos: Gestión y Accesos` to `Rutinas: Modelos y Admin`, `Docs: Decisiones e Issues`, `Portal del Alumno`, `Núcleo Multi-tenant`, `Vistas de Rutinas`, `Tenant: Gimnasio y Perfil`, `White-label y Personalización`, `Tests Vistas Rutinas`, `Tests Tenancy Base`, `Cron Generar Pagos`, `Modelo PagoMensual`, `Tests Vistas Alumnos`, `Tests TenantScopedMixin`, `Tests Vistas Pagos`, `Registro Self-serve`, `CRUD Plantillas Rutina`, `Vencimiento de Pagos`, `Asignación de Rutinas`, `Tests White-label`, `Tests Modelo Pago`?**
  _High betweenness centrality (0.233) - this node is a cross-community bridge._
- **Why does `TenantScopedMixin` connect `Tenant Scoping (Mixins)` to `Alumnos: Gestión y Accesos`, `Docs: Decisiones e Issues`, `Biblioteca de Ejercicios`, `Portal del Alumno`, `Gestión de Novedades`, `Núcleo Multi-tenant`, `Vistas de Rutinas`, `Tenant: Gimnasio y Perfil`, `Items de Plantilla`, `Visibilidad de Novedades`, `Vistas de Pagos`, `Tests TenantScopedMixin`, `CRUD Plantillas Rutina`, `Ocultar Novedad`, `Asignación de Rutinas`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `Perfil` connect `Tenant: Gimnasio y Perfil` to `Alumnos: Gestión y Accesos`, `Rutinas: Modelos y Admin`, `Docs: Decisiones e Issues`, `Biblioteca de Ejercicios`, `Tests de Aislamiento`, `Portal del Alumno`, `Gestión de Novedades`, `Núcleo Multi-tenant`, `Tenant Scoping (Mixins)`, `White-label y Personalización`, `Tests Tenancy Base`, `Cron Generar Pagos`, `Tests TenantScopedMixin`, `Registro Self-serve`, `Señal de Activación`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 49 inferred relationships involving `Alumno` (e.g. with `AlumnoAdmin` and `AlumnoForm`) actually correct?**
  _`Alumno` has 49 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `TenantScopedMixin` (e.g. with `AlumnoCreateView` and `AlumnoDetailView`) actually correct?**
  _`TenantScopedMixin` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Form de alta/edición de alumnos de un gimnasio.  `fecha_activacion` queda afuera`, `Alta del login (usuario/contraseña) de un alumno que todavía no     tiene uno. V`, `Reseteo de la contraseña de un alumno que ya tiene login. Mismo     criterio de` to the rest of the system?**
  _173 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Alumnos: Gestión y Accesos` be split into smaller, more focused modules?**
  _Cohesion score 0.05238095238095238 - nodes in this community are weakly interconnected._