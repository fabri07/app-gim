"""Utilidades para iterar un catálogo cerrado (`TextChoices`) en su orden
de declaración, con un bucket final para datos sin categorizar.

Patrón que se repite cada vez que un campo `choices` con `blank=True`
necesita mostrarse agrupado sin perder ni mezclar los casos sin cargar
(p. ej. `Alumno.sexo` en `tenants/analitica.py::distribucion_por_genero`,
`RutinaAsignadaItem.categoria_snapshot` en `rutinas/agrupacion.py`) --
un solo lugar evita que la regla de "el bucket vacío va al final, no se
descarta" diverja entre esos lugares.
"""


def orden_con_bucket_vacio(choices):
    """Valores de `choices` (iterable de tuplas `(valor, etiqueta)`, como
    `TextChoices.choices`) en su orden de declaración, más `""` al final
    -- para iterar catálogos cerrados donde el dato puede no estar
    cargado, mostrando esos casos al final en vez de mezclados o
    perdidos."""
    return [valor for valor, _ in choices] + [""]
