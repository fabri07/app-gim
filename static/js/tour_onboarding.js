// Tour de bienvenida para staff nuevo: notas dismissibles que guían los
// primeros pasos (logo, colores, fondo, importar ejercicios/rutinas),
// mostradas de a una según la pantalla en la que está el staff.
//
// El progreso vive en localStorage (namespaced por gimnasio, en el raro caso
// de que el mismo navegador se use para más de un gimnasio) -- ver
// tenants.context_processors.tour_onboarding_disponible para la única pieza
// server-side (habilitar el tour solo a Perfiles de staff nuevos).
//
// hx-boost reemplaza el <body> ENTERO en cada navegación boosteada, así que
// (a) cualquier tarjeta agregada por JS se pierde en la siguiente navegación
// y hay que reconstruirla, y (b) los clicks se delegan en `document` (que
// nunca se reemplaza), nunca atados a un botón puntual -- mismo criterio que
// static/js/pwa.js.
const TOUR_PASOS = [
  {
    pagina: "home",
    titulo: "¡Bienvenido a tu gimnasio!",
    texto: "Te vamos a mostrar en unos pasos cómo dejar tu app lista. Podés saltear cualquier nota con la (x) o cerrarlas todas con \"No mostrar más\".",
  },
  {
    pagina: "gimnasio_editar",
    titulo: "Subí el logo",
    texto: "Acá en \"Mi gimnasio\" podés subir tu logo para que la app se vea con tu marca.",
  },
  {
    pagina: "gimnasio_editar",
    titulo: "Elegí colores y tipografía",
    texto: "Más abajo elegís un paisaje de colores y una tipografía para tu panel y el portal de tus alumnos.",
  },
  {
    pagina: "gimnasio_editar",
    titulo: "Elegí el fondo",
    texto: "También podés elegir qué se ve de fondo: un paisaje de color, una imagen propia, o un doodle temático.",
  },
  {
    pagina: "ejercicios",
    titulo: "Importá tus ejercicios",
    texto: "Cargá tu biblioteca de ejercicios de una sola vez importando un Excel, o agregalos uno por uno con \"Nuevo ejercicio\".",
  },
  {
    pagina: "rutinas",
    titulo: "Creá tu primera rutina",
    texto: "Importá tus planes de entrenamiento desde Excel o armá tu primera plantilla de rutina desde cero.",
  },
];

function claveTourStorage() {
  const marcador = document.getElementById("tour-datos-marcador");
  const slug = (marcador && marcador.dataset.tourGimnasio) || "sin-gimnasio";
  return `tour_onboarding_paso_${slug}`;
}

function leerPasoActual() {
  let valor;
  try {
    valor = parseInt(localStorage.getItem(claveTourStorage()), 10);
  } catch (e) {
    return 0; // localStorage no disponible (navegación privada, etc.): sin tour, sin romper nada
  }
  return Number.isNaN(valor) ? 0 : valor;
}

function guardarPasoActual(indice) {
  try {
    localStorage.setItem(claveTourStorage(), String(indice));
  } catch (e) {
    // sin persistencia disponible, la tarjeta simplemente no vuelve a aparecer en esta carga
  }
}

function crearTarjetaTour(paso, indice) {
  const tarjeta = document.createElement("div");
  tarjeta.id = "tour-onboarding-card";
  // "tarjeta" reusa el look ya definido (fondo, bordes, sombra, padding);
  // "tour-tarjeta" solo agrega el posicionamiento fijo y el acento de color.
  tarjeta.className = "tarjeta tour-tarjeta";
  tarjeta.setAttribute("role", "dialog");
  tarjeta.setAttribute("aria-label", "Tour de bienvenida");

  const encabezado = document.createElement("div");
  encabezado.className = "tour-tarjeta__encabezado";

  const contador = document.createElement("span");
  contador.className = "tour-tarjeta__contador";
  contador.textContent = `Paso ${indice + 1} de ${TOUR_PASOS.length}`;

  const cerrar = document.createElement("button");
  cerrar.type = "button";
  cerrar.className = "tour-tarjeta__cerrar";
  cerrar.setAttribute("aria-label", "Saltear este paso");
  cerrar.setAttribute("data-tour-cerrar", "");
  cerrar.textContent = "×";

  encabezado.append(contador, cerrar);

  const titulo = document.createElement("h3");
  titulo.className = "tour-tarjeta__titulo";
  titulo.textContent = paso.titulo;

  const texto = document.createElement("p");
  texto.className = "tour-tarjeta__texto";
  texto.textContent = paso.texto;

  const acciones = document.createElement("div");
  acciones.className = "tour-tarjeta__acciones";

  const esUltimoPaso = indice === TOUR_PASOS.length - 1;
  const siguiente = document.createElement("button");
  siguiente.type = "button";
  siguiente.className = "boton";
  siguiente.setAttribute("data-tour-siguiente", "");
  siguiente.textContent = esUltimoPaso ? "Entendido" : "Siguiente";
  acciones.append(siguiente);

  const descartar = document.createElement("button");
  descartar.type = "button";
  descartar.className = "tour-tarjeta__descartar";
  descartar.setAttribute("data-tour-descartar", "");
  descartar.textContent = "No mostrar más";

  tarjeta.append(encabezado, titulo, texto, acciones, descartar);
  return tarjeta;
}

function sincronizarTour() {
  const existente = document.getElementById("tour-onboarding-card");
  if (existente) existente.remove();

  // Todo se lee de #tour-datos-marcador, NUNCA de document.body.dataset:
  // hx-boost reemplaza el innerHTML de <body> en cada navegación, pero
  // nunca los atributos del propio tag <body> -- un data-* puesto ahí
  // quedaría pegado en el valor de la primera carga completa, aun cuando
  // el swap boosteado (p.ej. suplantar/volver) cambió de usuario.
  const marcador = document.getElementById("tour-datos-marcador");
  if (!marcador || marcador.dataset.tourHabilitado !== "true") return;

  const indice = leerPasoActual();
  if (indice >= TOUR_PASOS.length) return;

  const paso = TOUR_PASOS[indice];
  if (paso.pagina !== marcador.dataset.tourPagina) return;

  document.body.appendChild(crearTarjetaTour(paso, indice));
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-tour-siguiente], [data-tour-cerrar]")) {
    guardarPasoActual(leerPasoActual() + 1);
    sincronizarTour();
    return;
  }
  if (event.target.closest("[data-tour-descartar]")) {
    guardarPasoActual(TOUR_PASOS.length);
    sincronizarTour();
  }
});

// htmx:load se dispara tanto en la carga inicial como después de cada swap
// boosteado -- atado a `document` (nunca se reemplaza), no a
// `document.body` (que sí se reemplaza en cada boost).
document.addEventListener("DOMContentLoaded", sincronizarTour);
document.addEventListener("htmx:load", sincronizarTour);
