// Formulario de producto de la tienda: las filas de variantes.
//
// 1. El lector de código de barras USB es un teclado que tipea el código y
//    aprieta Enter. En un input de código ese Enter enviaría el formulario a
//    medio cargar: acá se convierte en «pasar al campo siguiente».
// 2. Las características («Talle», «Color»): las etiquetas de los valores de
//    cada fila siguen a lo que se tipea, y los valores de una característica
//    vacía se esconden (nunca si tienen un error: el servidor los dibuja
//    siempre, así que sin JS todo se ve).
// 3. El generador: casillas con las sugerencias + «Otros valores»; «Agregar
//    combinaciones» arma el producto cartesiano, saltea las que ya están
//    (activas o no), reusa las filas sin valor y clona la fila vacía del
//    `<template>` para el resto.
//
// IIFE con guarda y sin DOMContentLoaded: htmx re-ejecuta este script en cada
// visita boosteada, y el contenedor es nuevo en cada visita (el `dataset`
// marca uno ya enganchado si el script corre dos veces sobre la misma página).
// Listeners sobre los elementos de la página, nunca sobre `document`.
(function () {
  var contenedor = document.querySelector("[data-variantes]");
  if (!contenedor || contenedor.dataset.enganchado) return;
  contenedor.dataset.enganchado = "1";

  var MAXIMO = 50;

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

  var form = contenedor.closest("form");
  var caracteristicas = [
    form.querySelector('[data-caracteristica="1"]'),
    form.querySelector('[data-caracteristica="2"]'),
  ];
  if (!caracteristicas[0] || !caracteristicas[1]) return;
  var plantilla = form.querySelector("template[data-fila-vacia]");
  var total = form.querySelector('input[name$="-TOTAL_FORMS"]');
  var generador = form.querySelector("[data-generador]");
  var aviso = form.querySelector("[data-generador-aviso]");
  var sugerencias = {};
  var json = document.getElementById("sugerencias-variantes");
  if (json) {
    try {
      sugerencias = JSON.parse(json.textContent);
    } catch (e) {
      sugerencias = {};
    }
  }

  // Sin mayúsculas ni acentos, como `tienda.variantes.clave`.
  function clave(texto) {
    return (texto || "").trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function nombreDe(i) {
    return caracteristicas[i].value.trim();
  }

  function filas() {
    return Array.prototype.slice.call(contenedor.querySelectorAll("[data-fila]"));
  }

  // Sólo el nodo de texto del `<label>`: no borra el `*` ni el `.sr-only`.
  function ponerEtiqueta(label, texto) {
    for (var n = label.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === Node.TEXT_NODE && n.nodeValue.trim()) {
        n.nodeValue = texto;
        return;
      }
    }
    label.insertBefore(document.createTextNode(texto), label.firstChild);
  }

  function actualizarFilas() {
    filas().forEach(function (fila) {
      [0, 1].forEach(function (i) {
        var envoltorio = fila.querySelector('[data-campo-valor="' + (i + 1) + '"]');
        if (!envoltorio) return;
        var nombre = nombreDe(i);
        var label = envoltorio.querySelector("label");
        if (label) ponerEtiqueta(label, nombre || "Valor " + (i + 1));
        var conError = !!envoltorio.querySelector(".config-error");
        // La 2 sin la 1 no cuenta (el servidor lo marca como error).
        var activa = i === 0 ? !!nombre : !!nombre && !!nombreDe(0);
        envoltorio.hidden = !activa && !conError;
      });
    });
  }

  function sugerenciasDe(nombre) {
    var buscada = clave(nombre);
    for (var k in sugerencias) {
      if (Object.prototype.hasOwnProperty.call(sugerencias, k) && clave(k) === buscada) {
        return sugerencias[k];
      }
    }
    return [];
  }

  function actualizarGenerador() {
    if (!generador) return;
    generador.hidden = !nombreDe(0);
    [0, 1].forEach(function (i) {
      var bloque = generador.querySelector('[data-generador-caracteristica="' + (i + 1) + '"]');
      if (!bloque) return;
      var nombre = nombreDe(i);
      bloque.hidden = !nombre || (i === 1 && !nombreDe(0));
      bloque.querySelector("[data-generador-titulo]").textContent = nombre;
      var casillas = bloque.querySelector("[data-generador-casillas]");
      // Sólo se rearma si cambió la característica: así no se pierden las
      // casillas tildadas al tipear en otro lado.
      if (casillas.dataset.para === clave(nombre)) return;
      casillas.dataset.para = clave(nombre);
      casillas.textContent = "";
      sugerenciasDe(nombre).forEach(function (valor) {
        var etiqueta = document.createElement("label");
        etiqueta.className = "generador-variantes__casilla";
        var casilla = document.createElement("input");
        casilla.type = "checkbox";
        casilla.value = valor;
        etiqueta.appendChild(casilla);
        etiqueta.appendChild(document.createTextNode(" " + valor));
        casillas.appendChild(etiqueta);
      });
    });
  }

  function valoresElegidos(i) {
    var bloque = generador.querySelector('[data-generador-caracteristica="' + (i + 1) + '"]');
    var valores = [];
    var vistos = {};
    function sumar(valor) {
      valor = valor.trim();
      if (!valor || vistos[clave(valor)]) return;
      vistos[clave(valor)] = true;
      valores.push(valor);
    }
    bloque.querySelectorAll("[data-generador-casillas] input:checked").forEach(function (c) {
      sumar(c.value);
    });
    bloque.querySelector("[data-generador-otros]").value.split(",").forEach(sumar);
    return valores;
  }

  function campoDe(fila, sufijo) {
    return fila.querySelector('[name$="-' + sufijo + '"]');
  }

  function avisar(texto) {
    aviso.textContent = texto;
    aviso.hidden = !texto;
  }

  function agregarCombinaciones() {
    avisar("");
    var nombre1 = nombreDe(0);
    var nombre2 = nombreDe(1);
    if (!nombre1) {
      avisar("Poné primero la característica 1.");
      return;
    }
    var valores1 = valoresElegidos(0);
    var valores2 = nombre2 ? valoresElegidos(1) : [""];
    if (!valores1.length) {
      avisar("Tildá o escribí al menos un " + nombre1 + ".");
      return;
    }
    if (!valores2.length) {
      avisar("Tildá o escribí al menos un " + nombre2 + ".");
      return;
    }
    // Las que ya están, activas o no (una desactivada se reactiva a mano).
    var presentes = {};
    filas().forEach(function (fila) {
      var v1 = campoDe(fila, "valor_1");
      var v2 = campoDe(fila, "valor_2");
      var quitar = campoDe(fila, "DELETE");
      // Una fila marcada «Quitar» no ocupa su combinación: se puede volver a
      // generar.
      if (quitar && quitar.checked) return;
      if (v1 && v1.value.trim()) {
        presentes[clave(v1.value) + "|" + (nombre2 && v2 ? clave(v2.value) : "")] = true;
      }
    });
    var nuevas = [];
    valores1.forEach(function (a) {
      valores2.forEach(function (b) {
        if (!presentes[clave(a) + "|" + clave(b)]) nuevas.push([a, b]);
      });
    });
    if (!nuevas.length) {
      avisar("Esas combinaciones ya están cargadas.");
      return;
    }
    // Primero las filas sin valor: las existentes (la «» de un producto al
    // que se le agrega Talle) y después la vacía del final; recién ahí se
    // clona.
    var libres = filas().filter(function (fila) {
      var v1 = campoDe(fila, "valor_1");
      var quitar = campoDe(fila, "DELETE");
      return v1 && !v1.value.trim() && !(quitar && quitar.checked);
    });
    libres.sort(function (a, b) {
      var idA = campoDe(a, "id");
      var idB = campoDe(b, "id");
      return (idB && idB.value ? 1 : 0) - (idA && idA.value ? 1 : 0);
    });
    var aClonar = Math.max(0, nuevas.length - libres.length);
    var cantidad = parseInt(total.value, 10) || 0;
    if (cantidad + aClonar > MAXIMO) {
      avisar(
        "Serían " + (cantidad + aClonar) + " variantes y el máximo es " + MAXIMO +
        ": elegí menos valores."
      );
      return;
    }
    nuevas.forEach(function (combinacion, n) {
      var fila = libres[n];
      if (!fila) {
        var html = plantilla.innerHTML.replace(/__prefix__/g, String(cantidad));
        cantidad += 1;
        var envoltura = document.createElement("div");
        envoltura.innerHTML = html.trim();
        fila = envoltura.firstElementChild;
        contenedor.appendChild(fila);
      }
      campoDe(fila, "valor_1").value = combinacion[0];
      campoDe(fila, "valor_2").value = combinacion[1];
      var activa = campoDe(fila, "activa");
      if (activa) activa.checked = true;
    });
    total.value = String(cantidad);
    generador.querySelectorAll("[data-generador-otros]").forEach(function (c) {
      c.value = "";
    });
    generador.querySelectorAll("[data-generador-casillas] input:checked").forEach(function (c) {
      c.checked = false;
    });
    actualizarFilas();
  }

  caracteristicas.forEach(function (campo) {
    campo.addEventListener("input", function () {
      actualizarFilas();
      actualizarGenerador();
    });
    // Enter en una característica (elegir del datalist) no envía el form.
    campo.addEventListener("keydown", function (evento) {
      if (evento.key === "Enter") evento.preventDefault();
    });
  });
  if (generador && plantilla && total && aviso) {
    generador.querySelector("[data-agregar-combinaciones]").addEventListener("click", agregarCombinaciones);
    // Enter en «Otros valores» agrega las combinaciones en vez de enviar el
    // form a medio cargar.
    generador.querySelectorAll("[data-generador-otros]").forEach(function (campo) {
      campo.addEventListener("keydown", function (evento) {
        if (evento.key !== "Enter") return;
        evento.preventDefault();
        agregarCombinaciones();
      });
    });
  }
  actualizarFilas();
  actualizarGenerador();
})();
