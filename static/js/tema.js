// Interruptor de tema (modo oscuro). Ver `ARQUITECTURA.md`, «Modo oscuro».
//
// El estado vive en `localStorage["tema"]` ("claro" | "oscuro"; sin clave =
// Automático) y se refleja en `<html data-tema>`. El atributo inicial lo pone un
// script síncrono del <head> (sin parpadeo); acá se maneja el cambio.
// hx-boost reemplaza el <body> en cada navegación pero no los atributos de
// <html>: por eso el click va por delegación y `aria-pressed` se re-sincroniza
// en `htmx:afterSwap`. (`htmx:afterSettle` NO se dispara en la navegación boosteada
// del <body>: verificado en el navegador con htmx 2.0.3.)
(function () {
  var CLAVE = "tema";
  var ORDEN = ["auto", "claro", "oscuro"];

  function leer() {
    try {
      var t = localStorage.getItem(CLAVE);
      return t === "claro" || t === "oscuro" ? t : "auto";
    } catch (e) {
      return "auto";
    }
  }

  function guardar(valor) {
    try {
      if (valor === "auto") localStorage.removeItem(CLAVE);
      else localStorage.setItem(CLAVE, valor);
    } catch (e) {
      /* sin almacenamiento: el cambio vale sólo para esta carga */
    }
  }

  function efectivo(valor) {
    if (valor !== "auto") return valor;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "oscuro" : "claro";
  }

  function sincronizar() {
    var actual = leer();
    document.querySelectorAll("[data-tema-opcion]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-tema-opcion") === actual ? "true" : "false");
    });
  }

  function actualizarMeta() {
    var oscuro = efectivo(leer()) === "oscuro";
    document.querySelectorAll('meta[name="theme-color"][data-claro]').forEach(function (m) {
      m.setAttribute("content", m.getAttribute(oscuro ? "data-oscuro" : "data-claro"));
    });
  }

  // Chart.js no sigue al tema por sí solo: las pantallas con gráficos definen
  // `window.temaGraficos` (colores de ejes y grilla) y acá se re-aplica y se
  // actualizan los gráficos ya dibujados.
  function actualizarGraficos() {
    if (typeof window.temaGraficos !== "function" || !window.Chart) return;
    window.temaGraficos();
    Object.keys(Chart.instances).forEach(function (id) {
      Chart.instances[id].update("none");
    });
  }

  function aplicar(valor) {
    var raiz = document.documentElement;
    if (valor === "auto") raiz.removeAttribute("data-tema");
    else raiz.setAttribute("data-tema", valor);
    sincronizar();
    actualizarMeta();
    actualizarGraficos();
  }

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-tema-opcion]");
    if (!boton) return;
    var valor = boton.getAttribute("data-tema-opcion");
    // En celular sólo se ve el botón activo: un click avanza al siguiente estado.
    if (window.matchMedia("(max-width: 639px)").matches) {
      valor = ORDEN[(ORDEN.indexOf(leer()) + 1) % ORDEN.length];
    }
    guardar(valor);
    aplicar(valor);
  });

  document.addEventListener("htmx:afterSwap", sincronizar);
  // Otra pestaña cambió el tema, o el sistema pasó de claro a oscuro en Automático.
  window.addEventListener("storage", function (e) {
    if (e.key === CLAVE) aplicar(leer());
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    actualizarMeta();
    actualizarGraficos();
  });

  sincronizar();
  actualizarMeta();
})();
