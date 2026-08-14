"""Genera el PDF descargable de una rutina asignada.

Django-free a propósito, mismo criterio que `tenants/paisaje_matching.py`:
una función pura que arma bytes, sin tocar `django.http` -- la vista
(`rutinas/views.py::RutinaAsignadaPdfView`) es la única capa que sabe de
`HttpResponse`/`Content-Disposition`.

Pensado como fallback confiable ("el alumno olvidó el celular"), no como un
documento de marketing: sin logo (evitaría I/O contra R2 y manejar un
archivo de logo corrupto/vacío para algo que tiene que funcionar siempre),
pero sí con el color de acento del gimnasio (`Gimnasio.color_primario_css`,
que siempre tiene valor, sin I/O) para mantener coherencia de white-label
con el resto de la app.
"""

from itertools import groupby

from fpdf import FPDF, FontFace

_COLUMNAS = [
    "Orden",
    "Ejercicio",
    "Series",
    "Repeticiones",
    "Descanso",
    "Notas",
    "RPE reportado",
]


def _hex_a_rgb(hexadecimal):
    valor = hexadecimal.lstrip("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))


def _fila_item(item):
    return [
        str(item.orden),
        item.ejercicio_nombre_snapshot,
        str(item.series),
        item.repeticiones,
        item.descanso or "—",
        item.notas or "—",
        item.get_rpe_display() if item.rpe else "Sin calificar",
    ]


def generar_pdf_rutina_asignada(asignada):
    """Arma el PDF de `asignada`, agrupando sus `items` por semana y día
    (ya llegan ordenados así por `Meta.ordering` del modelo). Devuelve los
    bytes del archivo."""
    color_acento = _hex_a_rgb(asignada.gimnasio.color_primario_css)

    pdf = FPDF()
    # Las core fonts de fpdf2 (Helvetica) solo soportan Latin-1 por defecto,
    # que NO incluye la raya "—" que ya usan los templates HTML como
    # placeholder ("|default:—"). cp1252 sí la tiene (y sigue cubriendo
    # tildes/ñ/¿/¡) sin tener que embeber una fuente Unicode.
    pdf.core_fonts_encoding = "cp1252"
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_fill_color(*color_acento)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, asignada.nombre_snapshot, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 11)
    datos = [
        ("Gimnasio", asignada.gimnasio.nombre),
        ("Alumno", str(asignada.alumno)),
        ("Objetivo", asignada.objetivo_snapshot),
        ("Fecha de inicio", asignada.fecha_inicio.isoformat()),
        (
            "Fecha de fin",
            asignada.fecha_fin.isoformat() if asignada.fecha_fin else "—",
        ),
        ("Semana actual", f"{asignada.semana_actual} de {4}"),
    ]
    for etiqueta, valor in datos:
        pdf.cell(0, 7, f"{etiqueta}: {valor}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    items = list(asignada.items.all())
    if not items:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(
            0,
            7,
            "Esta rutina no tiene ejercicios.",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        return bytes(pdf.output())

    encabezado_tabla = FontFace(
        emphasis="BOLD", color=(255, 255, 255), fill_color=color_acento
    )
    for (semana, dia), items_del_dia in groupby(
        items, key=lambda item: (item.semana, item.dia)
    ):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, f"Semana {semana} · Día {dia}", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=encabezado_tabla) as table:
            fila_encabezado = table.row()
            for columna in _COLUMNAS:
                fila_encabezado.cell(columna)
            for item in items_del_dia:
                fila = table.row()
                for valor in _fila_item(item):
                    fila.cell(valor)
        pdf.ln(4)

    return bytes(pdf.output())
