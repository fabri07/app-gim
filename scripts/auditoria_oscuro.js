// Auditoría del modo oscuro: para cada URL con la sesión iniciada, pinta la
// pantalla (fetch + iframe `srcdoc`, las vistas mandan X-Frame-Options: DENY) con
// el tema OSCURO y marca dos cosas:
//   1. texto cuyo contraste contra su fondo efectivo es < 3 (o < 4.5 si es normal);
//   2. fondos casi blancos que se colaron (un color fijo que el tema no remapeó).
// Pegar en la consola (o la herramienta de JS del navegador) de una página del
// mismo origen, con `MODO_OSCURO=1` y la sesión iniciada:
//   await window.__auditar(["/", "/alumnos/"], 1280)
// No reemplaza mirar la pantalla: encuentra candidatos, no veredictos.
(() => {
  const parse = (s) => {
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; }
    return null;
  };
  const conv = (doc, color) => {
    // el navegador devuelve oklch()/color-mix(): se normaliza pintando en un canvas
    const c = doc.createElement("canvas"); c.width = c.height = 1;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.clearRect(0, 0, 1, 1); x.fillStyle = "#000"; x.fillStyle = color; x.fillRect(0, 0, 1, 1);
    const d = x.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const cr = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const mezcla = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });

  const cargar = async (url, ancho) => {
    const html = await fetch(url, { credentials: "same-origin" }).then((r) => r.text());
    return new Promise((resolve) => {
      const f = document.createElement("iframe");
      f.style.cssText = `position:fixed;left:-99999px;top:0;width:${ancho}px;height:900px;border:0`;
      f.onload = () => setTimeout(() => resolve(f), 900);
      f.srcdoc = html; document.body.appendChild(f);
    });
  };

  window.__auditar = async (urls, ancho = 1280) => {
    const salida = {};
    for (const url of urls) {
      const f = await cargar(url, ancho);
      const w = f.contentWindow, d = f.contentDocument;
      const raiz = d.documentElement;
      const tema = w.getComputedStyle(raiz).colorScheme;
      const fondoBody = conv(d, w.getComputedStyle(d.body).backgroundColor);
      const base = fondoBody.a > 0.5 ? fondoBody : conv(d, w.getComputedStyle(raiz).getPropertyValue("--color-fondo") || "#15201b");
      const problemas = [];
      const blancos = [];
      d.body.querySelectorAll("*").forEach((el) => {
        if (["SCRIPT", "STYLE", "NOSCRIPT", "SVG", "PATH"].includes(el.tagName.toUpperCase())) return;
        if (el.closest("[data-demo], .maqueta, [hidden]")) return;
        const cs = w.getComputedStyle(el);
        if (cs.display === "none" || cs.visibility === "hidden") return;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return;
        // fondo efectivo: se apilan los fondos de los ancestros
        const pila = []; let n = el;
        while (n && n.nodeType === 1) { const c = conv(d, w.getComputedStyle(n).backgroundColor); if (c.a > 0) pila.push(c); if (c.a >= 1) break; n = n.parentElement; }
        let bg = pila.length && pila[pila.length - 1].a >= 1 ? pila.pop() : base;
        while (pila.length) bg = mezcla(pila.pop(), bg);
        const propio = conv(d, cs.backgroundColor);
        if (propio.a > 0.6 && lum(propio) > 0.6 && r.width > 12 && r.height > 12)
          blancos.push(`${el.tagName}.${(el.className || "").toString().slice(0, 40)} bg=${cs.backgroundColor}`);
        const tieneTexto = [...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim().length > 1);
        if (!tieneTexto) return;
        const fg = mezcla(conv(d, cs.color), bg);
        const ratio = cr(fg, bg);
        const grande = parseFloat(cs.fontSize) >= 24 || (parseFloat(cs.fontSize) >= 18.66 && parseInt(cs.fontWeight) >= 700);
        if (ratio < (grande ? 3 : 4.5))
          problemas.push(`${ratio.toFixed(2)} ${el.tagName}.${(el.className || "").toString().slice(0, 40)} «${el.textContent.trim().slice(0, 28)}» fg=${cs.color.slice(0, 30)}`);
      });
      // listas COMPLETAS: comparar sólo las primeras N entre temas ocultó un defecto real
      salida[url] = { tema, texto: problemas, nTexto: problemas.length, blancos: [...new Set(blancos)], nBlancos: blancos.length };
      f.remove();
    }
    return salida;
  };
})();
