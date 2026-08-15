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

from fpdf import FPDF, FontFace

from rutinas.agrupacion import agrupar_items_por_grupo_muscular

_COLUMNAS = ["Ejercicio", "Video", "Semana 1", "Semana 2", "Semana 3", "Semana 4"]


def _hex_a_rgb(hexadecimal):
    valor = hexadecimal.lstrip("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))


def _celda_semana(item):
    """Texto compacto de una celda semana x ejercicio, p. ej. "3x12 ·
    20kg (hecho)\\nDescanso: 90s\\nCuidado con la zona lumbar". Sin el
    símbolo ✓: las core fonts de fpdf2 solo soportan cp1252 (ver
    comentario de `core_fonts_encoding` más abajo), que no incluye ese
    carácter -- rompería la generación. Descanso/notas van en líneas
    aparte y solo si están cargados: son el fallback en papel de un
    alumno sin acceso a la app, así que la indicación de descanso o
    cualquier nota de técnica/seguridad del profesor no puede perderse
    acá aunque en pantalla quede compacta."""
    if item is None:
        return "—"
    texto = f"{item.series}x{item.repeticiones}"
    if item.kilos:
        texto += f" · {item.kilos}"
    if item.rpe:
        texto += " (hecho)"
    if item.descanso:
        texto += f"\nDescanso: {item.descanso}"
    if item.notas:
        texto += f"\n{item.notas}"
    return texto


def _fila_ejercicio(ejercicio):
    return [
        ejercicio["nombre"],
        ejercicio["video"] or "—",
        *[_celda_semana(semana["item"]) for semana in ejercicio["semanas"]],
    ]


def generar_pdf_rutina_asignada(asignada):
    """Arma el PDF de `asignada`: una sección por día, y dentro de cada
    día los ejercicios agrupados por grupo muscular con las 4 semanas
    lado a lado (`rutinas/agrupacion.py`, el mismo criterio que usa el
    portal del alumno -- así el PDF y la vista en pantalla se leen
    igual). Devuelve los bytes del archivo."""
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
    items_por_dia = {}
    for item in items:
        items_por_dia.setdefault(item.dia, []).append(item)

    for dia, items_del_dia in sorted(items_por_dia.items()):
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, f"Día {dia}", new_x="LMARGIN", new_y="NEXT")

        for grupo in agrupar_items_por_grupo_muscular(items_del_dia):
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(
                0, 8, grupo["grupo_muscular_display"], new_x="LMARGIN", new_y="NEXT"
            )

            pdf.set_font("Helvetica", "", 9)
            with pdf.table(headings_style=encabezado_tabla) as table:
                fila_encabezado = table.row()
                for columna in _COLUMNAS:
                    fila_encabezado.cell(columna)
                for ejercicio in grupo["ejercicios"]:
                    fila = table.row()
                    for valor in _fila_ejercicio(ejercicio):
                        fila.cell(valor)
            pdf.ln(2)
        pdf.ln(4)

    return bytes(pdf.output())
