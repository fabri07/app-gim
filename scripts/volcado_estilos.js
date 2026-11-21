// Volcado y comparación de estilos computados, para probar que un refactor de CSS
// no cambió nada visible. Se pega en la consola (o con la herramienta de JS del
// navegador) de una página del MISMO origen que la app, con la sesión iniciada.
//
//   await window.__volcar(["/", "/alumnos/"], 1280, "antes")   // guarda en localStorage
//   ... se cambia el CSS y se recarga la página ...
//   await window.__comparar(["/", "/alumnos/"], 1280, "antes") // diferencias vs lo guardado
//
// Carga cada URL (fetch + iframe `srcdoc`) del ancho pedido, recorre TODOS los elementos y
// guarda, por elemento, los colores y la sombra computados. Es una prueba de
// «idéntico», no de «bonito». Ver `ARQUITECTURA.md`, «Pase visual…» / modo oscuro.
(() => {
  const PROPS = [
    "color", "backgroundColor", "borderTopColor", "borderRightColor",
    "borderBottomColor", "borderLeftColor", "outlineColor", "boxShadow",
    "textDecorationColor", "caretColor", "accentColor",
  ];

  // Las vistas mandan `X-Frame-Options: DENY`, así que no se puede poner la URL en
  // un iframe: se trae el HTML con la sesión y se pinta con `srcdoc` (hereda el
  // origen, y `/static/` se resuelve igual).
  const cargar = async (url, ancho) => {
    const html = await fetch(url, { credentials: "same-origin" }).then((r) => r.text());
    return new Promise((resolve) => {
      const f = document.createElement("iframe");
      f.style.cssText = `position:fixed;left:-99999px;top:0;width:${ancho}px;height:900px;border:0`;
      f.onload = () => setTimeout(() => resolve(f), 700);
      f.srcdoc = html;
      document.body.appendChild(f);
    });
  };

  const leer = (f) => {
    const w = f.contentWindow;
    const filas = [];
    const todos = f.contentDocument.body.querySelectorAll("*");
    todos.forEach((el, i) => {
      if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(el.tagName)) return;
      const cs = w.getComputedStyle(el);
      filas.push(`${i}:${el.tagName}.${(el.className && el.className.baseVal === undefined ? el.className : "").toString().slice(0, 40)}|` +
        PROPS.map((p) => cs[p]).join("|"));
    });
    const cuerpo = w.getComputedStyle(f.contentDocument.body);
    filas.unshift(`body|${cuerpo.backgroundColor}|${cuerpo.backgroundImage.slice(0, 200)}|${cuerpo.color}`);
    return filas;
  };

  window.__volcar = async (urls, ancho, etiqueta) => {
    const resumen = {};
    for (const u of urls) {
      const f = await cargar(u, ancho);
      const filas = leer(f);
      localStorage.setItem(`volcado:${etiqueta}:${ancho}:${u}`, JSON.stringify(filas));
      resumen[u] = filas.length;
      f.remove();
    }
    return resumen;
  };

  window.__comparar = async (urls, ancho, etiqueta) => {
    const resumen = {};
    for (const u of urls) {
      const antes = JSON.parse(localStorage.getItem(`volcado:${etiqueta}:${ancho}:${u}`) || "null");
      const f = await cargar(u, ancho);
      const ahora = leer(f);
      f.remove();
      if (!antes) { resumen[u] = "sin línea base"; continue; }
      const dif = [];
      const n = Math.max(antes.length, ahora.length);
      for (let i = 0; i < n; i++) if (antes[i] !== ahora[i]) dif.push([antes[i], ahora[i]]);
      resumen[u] = { elementos: ahora.length, diferencias: dif.length, primeras: dif.slice(0, 3) };
    }
    return resumen;
  };
})();
