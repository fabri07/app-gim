---
type: "query"
date: "2026-07-06T04:11:41.383340+00:00"
question: "El staff/dueño del gimnasio es el cliente que paga; los alumnos de cada gimnasio reciben planes de entrenamiento, mensajes y noticias del staff. ¿Esto se está cumpliendo en el código?"
contributor: "graphify"
source_nodes: ["Alumno", "Gimnasio", "Perfil", "StaffRequiredMixin", "TenantScopedMixin", "RutinaAsignada", "HomeView", "NovedadQuerySet", "PagoMensual", "CrearAccesoView"]
---

# Q: El staff/dueño del gimnasio es el cliente que paga; los alumnos de cada gimnasio reciben planes de entrenamiento, mensajes y noticias del staff. ¿Esto se está cumpliendo en el código?

## Answer

Sí. Expanded from original query via vocab: [alumno, gimnasio, staff, rutina, asignada, plantilla, novedad, visibles, pago, perfil, portal, tenant]. El modelo B2B2C está implementado: Gimnasio es el tenant, Perfil vincula User↔Gimnasio con rol staff/alumno. El staff crea el acceso del alumno (CrearAccesoView, credenciales asignadas, no magic-link). Planes: RutinaPlantilla → AsignarRutinaView → RutinaAsignada.crear_desde_plantilla (snapshot congelado) → el alumno la ve en HomeView (portal Fase 3). Noticias: staff publica Novedad; NovedadQuerySet.visibles() filtra lo que ve el alumno. Pagos: cron generar_pagos crea PagoMensual pendiente, staff confirma, alumno ve su cuota en el portal. Aislamiento garantizado por TenantScopedMixin + for_gimnasio + TenantScopedModelForm, con tests de aislamiento por app. Matiz: no hay mensajes 1-a-1 (chat interno está excluido del MVP a propósito); la comunicación staff→alumno es broadcast vía novedades.

## Source Nodes

- Alumno
- Gimnasio
- Perfil
- StaffRequiredMixin
- TenantScopedMixin
- RutinaAsignada
- HomeView
- NovedadQuerySet
- PagoMensual
- CrearAccesoView