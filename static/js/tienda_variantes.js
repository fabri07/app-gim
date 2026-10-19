// Formulario de producto de la tienda: las filas de variantes.
//
// El lector de código de barras USB es un teclado que tipea el código y
// aprieta Enter. En un input de código ese Enter enviaría el formulario a
// medio cargar: acá se convierte en «pasar al campo siguiente».
//
// IIFE con guarda y sin DOMContentLoaded: htmx re-ejecuta este script en cada
// visita boosteada, y el contenedor es nuevo en cada visita (el `dataset`
// marca uno ya enganchado si el script corre dos veces sobre la misma página).
(function () {
  var contenedor = document.querySelector("[data-variantes]");
  if (!contenedor || contenedor.dataset.enganchado) return;
  contenedor.dataset.enganchado = "1";

  contenedor.addEventListener("keydown", function (evento) {
    var campo = evento.target;
    if (evento.key !== "Enter" || !campo.matches("[data-codigo-barras]")) return;
    evento.preventDefault();
    var form = campo.form;
    if (!form) return;
    // Sólo los campos que se ven: el siguiente de verdad para quien escanea.
    var campos = Array.prototype.filter.call(form.elements, function (el) {
      return !el.disabled && el.type !== "hidden" && !el.hidden && el.offsetParent !== null;
    });
    var siguiente = campos[campos.indexOf(campo) + 1];
    if (siguiente) siguiente.focus();
  });
})();
