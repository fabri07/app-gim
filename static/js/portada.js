/* Portada de producto (templates/tenants/portada.html).
 *
 * Dos cosas, y ninguna es necesaria para leer la página: sin este archivo todo
 * el contenido se ve igual.
 *  1. Selector de paisaje: recolorea las pantallas de ejemplo (`[data-demo]`:
 *     el hero y las cuatro funciones), nunca `:root` -- la página en sí sigue
 *     siendo de TuGimApp. Es el único momento animado de la página.
 *  2. Nav fija: se marca al dejar el tope.
 *
 * Idempotente: htmx vuelve a ejecutar los <script> de un swap boosteado, así
 * que cada nodo se marca como inicializado.
 */
(function () {
  "use strict";

  function iniciarPaisajes() {
    var demos = document.querySelectorAll("[data-demo]");
    var chips = document.querySelectorAll("[data-paisaje]");
    if (!demos.length || !chips.length || chips[0].dataset.listo) return;

    chips.forEach(function (chip) {
      chip.dataset.listo = "1";
      chip.addEventListener("click", function () {
        demos.forEach(function (demo) {
          demo.style.setProperty("--color-fondo", chip.dataset.fondo);
          demo.style.setProperty("--color-primario", chip.dataset.primario);
          demo.style.setProperty("--color-secundario", chip.dataset.secundario);
        });
        chips.forEach(function (otro) {
          otro.setAttribute("aria-pressed", otro === chip ? "true" : "false");
        });
      });
    });
  }

  function iniciarNav() {
    var nav = document.querySelector("[data-portada-nav]");
    if (!nav || nav.dataset.listo) return;
    nav.dataset.listo = "1";
    var marcar = function () {
      nav.classList.toggle("portada-nav--scroll", window.scrollY > 8);
    };
    window.addEventListener("scroll", marcar, { passive: true });
    marcar();
  }

  function iniciar() {
    iniciarPaisajes();
    iniciarNav();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
