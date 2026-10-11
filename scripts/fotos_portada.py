"""Convierte las fotos de contexto de la portada a WebP con la colorimetría Bosque.

Uso (desde la raíz del repo, con el .venv):
    .venv/bin/python scripts/fotos_portada.py ~/Downloads/tugimapp-fotos static/img/portada

Busca en la carpeta de origen un archivo por escena (`.png`/`.jpg`/`.webp`,
con el nombre de la clave de ESCENAS) y procesa sólo las que encuentra: una
escena que falta NO se toca, así una corrida parcial nunca pisa una foto ya
commiteada. Con `--placeholders` genera un degradé del tamaño exacto para las
que falten (solo para maquetar). Nunca agranda: si el original es más chico que
un ancho pedido, ese ancho se omite y se avisa.
"""
import os
import sys
from pathlib import Path

import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # noqa: E402

from tenants.models import Gimnasio  # noqa: E402

PALETA = Gimnasio.PALETAS[Gimnasio.Paleta.BOSQUE]

# clave: (proporción ancho/alto, anchos, punto focal x 0..1, punto focal y 0..1)
ESCENAS = {
    "hero-gimnasio": (16 / 9, [800, 1280, 1672], 0.5, 0.5),
    "hero-gimnasio-movil": (4 / 5, [600, 1000], 0.62, 0.5),
    "problema-escritorio": (3 / 2, [600, 1100], 0.5, 0.5),
    "alumno-celular": (4 / 5, [600, 1100], 0.5, 0.45),
    "ingreso-puerta": (3 / 2, [600, 1100], 0.5, 0.5),
    "duenio-compu": (16 / 9, [800, 1600], 0.5, 0.5),
}
EXTENSIONES = (".png", ".jpg", ".jpeg", ".webp")


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def buscar(origen, clave):
    for ext in EXTENSIONES:
        p = origen / f"{clave}{ext}"
        if p.exists():
            return p
    return None


def placeholder(ratio):
    """Degradé fondo → primario con un acento secundario, 2400 px de ancho."""
    w = 2400
    h = round(w / ratio)
    fondo, prim, sec = (hex_rgb(PALETA[k]) for k in ("fondo", "primario", "secundario"))
    chico = Image.new("RGB", (48, max(2, round(48 / ratio))))
    cw, ch = chico.size
    px = chico.load()
    for y in range(ch):
        for x in range(cw):
            t = (x / (cw - 1)) * 0.7 + (y / (ch - 1)) * 0.3
            c = [round(fondo[i] * (1 - t) + prim[i] * t) for i in range(3)]
            d = ((x - cw * 0.75) ** 2 + (y - ch * 0.3) ** 2) ** 0.5 / cw
            a = max(0.0, 0.35 - d)
            c = [round(c[i] * (1 - a) + sec[i] * a) for i in range(3)]
            px[x, y] = tuple(c)
    return chico.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(30))


def recortar(img, ratio, fx, fy):
    w, h = img.size
    if w / h > ratio:
        nw = round(h * ratio)
        x0 = min(max(0, round(fx * w - nw / 2)), w - nw)
        return img.crop((x0, 0, x0 + nw, h))
    nh = round(w / ratio)
    y0 = min(max(0, round(fy * h - nh / 2)), h - nh)
    return img.crop((0, y0, w, y0 + nh))


def graduar(img):
    """Misma corrección suave para todas: menos saturación y un velo cálido del `fondo`."""
    img = ImageEnhance.Color(img).enhance(0.9)
    capa = Image.new("RGB", img.size, hex_rgb(PALETA["fondo"]))
    img = Image.blend(img, ImageOps.autocontrast(img, cutoff=0.3), 0.5)
    return Image.blend(img, Image.composite(capa, img, ImageOps.invert(img.convert("L"))), 0.06)


def main(origen, destino, con_placeholders=False):
    origen, destino = Path(origen), Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    for clave, (ratio, anchos, fx, fy) in ESCENAS.items():
        fuente = buscar(origen, clave)
        if fuente:
            img = ImageOps.exif_transpose(Image.open(fuente))
            if img.info.get("icc_profile"):
                from io import BytesIO
                from PIL import ImageCms
                perfil = ImageCms.ImageCmsProfile(BytesIO(img.info["icc_profile"]))
                img = ImageCms.profileToProfile(img, perfil, ImageCms.createProfile("sRGB"))
            img = graduar(recortar(img.convert("RGB"), ratio, fx, fy))
            tipo = "foto"
        elif con_placeholders:
            img = placeholder(ratio)
            tipo = "PLACEHOLDER"
        else:
            print(f"  - {clave}: no está en {origen}, no se toca")
            continue
        for ancho in anchos:
            if ancho > img.width:
                print(f"  ! {clave}: {ancho}px pediría agrandar (nativo {img.width}px), se omite")
                continue
            alto = round(ancho / ratio)
            salida = destino / f"{clave}-{ancho}.webp"
            img.resize((ancho, alto), Image.LANCZOS).save(salida, "WEBP", quality=72, method=6)
            print(f"{tipo:11} {salida.name:32} {ancho}x{alto} {salida.stat().st_size // 1024} KB")


if __name__ == "__main__":
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(*argumentos[:2], con_placeholders="--placeholders" in sys.argv)
