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

_NOTAS_MAX_CARACTERES_EN_TABLA = 160
"""Tope de caracteres de `notas` que entran en la celda de la tabla.
`pdf.table()` no puede partir una fila entre dos páginas -- si una sola
celda crece más de lo que entra en una página vacía, la generación entera
revienta con `ValueError` (reproducido con notas de ~600-700 caracteres).
Con este tope una celda nunca se acerca a ese límite. El texto NUNCA se
descarta (puede ser una indicación de seguridad del profesor, ver docstring
de `_celda_semana`): lo que no entra va completo en el apéndice "Notas
completas" al final del PDF, armado con `multi_cell` -- que sí pagina sola,
a diferencia de una fila de tabla."""


def _hex_a_rgb(hexadecimal):
    valor = hexadecimal.lstrip("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))


def _celda_semana(item):
    """Texto de una celda semana x ejercicio, con un campo por línea
    (Series/Repeticiones/Kilos/Descanso/Calificación/Notas) -- mismo
    desglose que la vista del alumno desde "tabla ancha por columna"
    (`templates/rutinas/mi_dia_detalle.html`, columnas separadas en vez
    de un texto compacto tipo "3x12"). Calificación usa
    `get_rpe_display()` (el texto que el alumno eligió, p. ej. "Estoy al
    límite"), no un genérico "(hecho)". Sin el símbolo ✓: las core fonts
    de fpdf2 solo soportan cp1252 (ver comentario de
    `core_fonts_encoding` más abajo), que no lo incluye -- rompería la
    generación. Cada línea solo aparece si está cargada: son el fallback
    en papel de un alumno sin acceso a la app, así que la indicación de
    descanso o cualquier nota de técnica/seguridad del profesor no puede
    perderse acá aunque en pantalla quede en su propia columna."""
    if item is None:
        return "—"
    lineas = [f"Series: {item.series}", f"Repeticiones: {item.repeticiones}"]
    if item.kilos:
        lineas.append(f"Kilos: {item.kilos}")
    if item.descanso:
        lineas.append(f"Descanso: {item.descanso}")
    if item.rpe:
        lineas.append(f"Calificación: {item.get_rpe_display()}")
    if item.notas:
        if len(item.notas) > _NOTAS_MAX_CARACTERES_EN_TABLA:
            lineas.append(
                f"Notas: {item.notas[:_NOTAS_MAX_CARACTERES_EN_TABLA]}… "
                "(completa al final del PDF)"
            )
        else:
            lineas.append(f"Notas: {item.notas}")
    return "\n".join(lineas)


def _fila_ejercicio(ejercicio, grupo_muscular_display):
    nombre = ejercicio["nombre"]
    if grupo_muscular_display:
        nombre += f"\n{grupo_muscular_display}"
    return [
        nombre,
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
                    for valor in _fila_ejercicio(
                        ejercicio, grupo["grupo_muscular_display"]
                    ):
                        fila.cell(valor)
            pdf.ln(2)
        pdf.ln(4)

    notas_largas = sorted(
        (
            item
            for item in items
            if len(item.notas) > _NOTAS_MAX_CARACTERES_EN_TABLA
        ),
        key=lambda item: (item.dia, item.semana, item.orden),
    )
    if notas_largas:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Notas completas", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        for item in notas_largas:
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(
                0,
                6,
                f"Día {item.dia} · Semana {item.semana} · "
                f"{item.ejercicio_nombre_snapshot}",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, item.notas, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

    return bytes(pdf.output())
