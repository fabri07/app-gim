// Registro del service worker + flujo de instalación y suscripción push.
//
// hx-boost reemplaza el <body> ENTERO en cada navegación boosteada (nodos
// nuevos, no una mutación in-place) -- así que listeners atados con
// addEventListener a botones puntuales (encontrados una sola vez en
// DOMContentLoaded) se pierden en la primera navegación boosteada. Delegamos
// los clicks en `document` (que nunca se reemplaza) en vez de atar un
// listener por botón -- mismo espíritu que el resto del proyecto evita
// depender de que un evento "de carga" vuelva a dispararse (ver el comentario
// de `item_form.html` sobre por qué su script no usa DOMContentLoaded).
let promptDiferido = null;

function sincronizarVisibilidadInstalar() {
  document.querySelectorAll("[data-pwa-instalar]").forEach((btn) => {
    btn.hidden = !promptDiferido;
  });
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js");
}

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  promptDiferido = e;
  sincronizarVisibilidadInstalar();
});

// htmx:load se dispara tanto en la carga inicial como después de cada swap
// boosteado -- sincroniza la visibilidad del botón "Instalar app" (oculto
// por default en el HTML servido) para los botones nuevos que trae cada
// navegación. Atado a `document` (nunca se reemplaza), NO a `document.body`
// (que sí se reemplaza en cada boost -- un listener atado ahí se perdería
// exactamente por el mismo motivo que los clicks de los botones).
document.addEventListener("htmx:load", sincronizarVisibilidadInstalar);

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-pwa-instalar]")) {
    if (!promptDiferido) return;
    promptDiferido.prompt();
    promptDiferido = null;
    sincronizarVisibilidadInstalar();
    return;
  }
  if (event.target.closest("[data-pwa-activar-push]")) {
    activarPush();
    return;
  }
  const botonCopiar = event.target.closest("[data-copiar-alias]");
  if (botonCopiar) {
    copiarAlias(botonCopiar);
  }
});

async function activarPush() {
  // `document.body.dataset` se re-lee en cada click (no se cachea en un
  // closure): el <body> es reemplazado en cada navegación boosteada, así
  // que un valor cacheado en la carga inicial podría quedar desactualizado
  // (p.ej. si la suplantación arranca durante la sesión).
  if (document.body.dataset.suplantacion === "true") return;
  if (!("Notification" in window) || !("PushManager" in window)) return;

  const vapidKey = document.body.dataset.vapidPublicKey;
  if (!vapidKey || Notification.permission === "denied") return;

  const permiso = await Notification.requestPermission();
  if (permiso !== "granted") return;

  const registro = await navigator.serviceWorker.ready;
  const suscripcion = await registro.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidKey),
  });

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  await fetch("/push/suscribir/", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfMeta ? csrfMeta.content : "",
    },
    body: JSON.stringify(suscripcion.toJSON()),
  });
}

// Splash de instalación (mancuerna) -- se dispara en CADA apertura en modo
// standalone (pedido explícito: "debería aparecer siempre que se abra la
// app"), no en `appinstalled`: ese evento no existe en iOS, que instala vía
// "Compartir > Agregar a inicio" sin que el navegador nunca confirme la
// instalación. `display-mode: standalone` + `navigator.standalone`
// (Safari/iOS legacy) cubren ambas plataformas. `sessionStorage` (no
// `localStorage`) es la marca correcta acá: dura mientras la app sigue
// abierta (evita repetirlo en cada navegación con hx-boost="false" -- login,
// logout, confirmar pago -- dentro de la MISMA apertura) pero se resetea
// sola en la apertura siguiente, sin lógica propia de "sesión de app".
// No depende de htmx:load a propósito (a diferencia de sincronizarVisibilidadInstalar):
// es un evento de arranque de la app, no algo que deba resincronizarse en
// cada navegación boosteada.
function correPWAInstalada() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

function mostrarSplashInstalacionSiCorresponde() {
  const splash = document.getElementById("pwa-splash");
  if (!splash) return;
  if (!correPWAInstalada()) return;
  if (sessionStorage.getItem("pwa_splash_visto")) return;
  sessionStorage.setItem("pwa_splash_visto", "true");

  splash.hidden = false;
  splash.classList.add("pwa-splash--activo");
  // Duración fija ligada a la animación CSS (@keyframes pwa-splash-envolvente,
  // ~2s) -- con prefers-reduced-motion la animación queda apagada (opacity:0
  // fijo) pero igual se remueve del DOM a este mismo tiempo, así nunca
  // bloquea la interacción con la app de fondo.
  setTimeout(() => splash.remove(), 2200);
}

document.addEventListener("DOMContentLoaded", mostrarSplashInstalacionSiCorresponde);

async function copiarAlias(boton) {
  const alias = boton.dataset.copiarAlias;
  try {
    await navigator.clipboard.writeText(alias);
  } catch (e) {
    return; // clipboard no disponible (permiso denegado, contexto no seguro): sin feedback, sin romper nada
  }
  const textoOriginal = boton.textContent;
  boton.textContent = "Copiado";
  boton.disabled = true;
  setTimeout(() => {
    boton.textContent = textoOriginal;
    boton.disabled = false;
  }, 1500);
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}
