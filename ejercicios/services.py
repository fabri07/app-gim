"""Servicios de la biblioteca de ejercicios.

Hoy solo la siembra del catálogo inicial de categorías. Vive acá y no en
`tenants/services.py` para que `tenants` no tenga que conocer el modelo de
categorías: `crear_gimnasio` lo llama con un import tardío, mismo criterio que
`HomeView._metricas_dashboard`.
"""

from ejercicios.models import CategoriaEjercicio
from importaciones.parsing import normalizar_texto

# Las 8 del `TextChoices` que `Ejercicio.grupo_muscular` usó hasta 2026-08-26.
# Un gimnasio nuevo arranca con ellas como punto de partida editable, no como
# catálogo cerrado: puede renombrarlas, desactivarlas o agregar las suyas.
# El orden es el de declaración original, no alfabético.
CATEGORIAS_INICIALES = [
    "Pecho",
    "Espalda",
    "Piernas",
    "Hombros",
    "Brazos",
    "Core",
    "Cardio",
    "Cuerpo completo",
]


def sembrar_categorias_iniciales(gimnasio):
    """Crea el catálogo sugerido para `gimnasio`. Idempotente: reusa las que
    ya existan (por `nombre_normalizado`, la misma clave que la
    `UniqueConstraint`), así que llamarla dos veces no duplica nada.

    Devuelve la lista de categorías creadas en esta llamada.
    """
    creadas = []
    for orden, nombre in enumerate(CATEGORIAS_INICIALES):
        categoria, fue_creada = CategoriaEjercicio.objects.get_or_create(
            gimnasio=gimnasio,
            nombre_normalizado=normalizar_texto(nombre),
            defaults={"nombre": nombre, "orden": orden},
        )
        if fue_creada:
            creadas.append(categoria)
    return creadas
