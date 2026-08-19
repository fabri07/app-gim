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

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}
