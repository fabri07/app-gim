// Service worker de la PWA de app_gim.
//
// Multi-tenant + hx-boost: el sitio es white-label (colores/logo/tema por
// gimnasio) resueltos por `user.perfil.gimnasio` en el servidor, no por
// slug en la URL para rutas autenticadas. Este SW NUNCA cachea HTML ni
// /media/ -- solo cachea assets de /static/ (versionados/inmutables por el
// fingerprinting de WhiteNoise). Sin esto, un dispositivo compartido podría
// servir el HTML/tema de un gimnasio a un usuario de otro. Tampoco hay
// fallback offline en esta primera versión (YAGNI, ver ISSUES.md).
//
// Las fotos de la portada (`/static/img/portada/`) tampoco se cachean: las
// baja cualquier visitante anónimo, pesan cientos de KB y, como la caché
// nunca expira, quedarían para siempre en su teléfono (y una copia más por
// cada re-encodeo). WhiteNoise ya las sirve con `immutable` por un año: la
// caché HTTP del navegador alcanza.
const CACHE_ESTATICOS = "app-gim-estaticos-v2";
const SIN_CACHE = ["/static/img/portada/"];

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Poda las cachés de versiones anteriores (la v1 guardaba todo /static/).
  event.waitUntil(
    caches
      .keys()
      .then((claves) =>
        Promise.all(claves.filter((c) => c !== CACHE_ESTATICOS).map((c) => caches.delete(c)))
      )
      .then(() => clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/") && !SIN_CACHE.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(
      caches.open(CACHE_ESTATICOS).then(async (cache) => {
        const cacheada = await cache.match(req);
        if (cacheada) return cacheada;
        const resp = await fetch(req);
        if (resp.ok) cache.put(req, resp.clone());
        return resp;
      })
    );
    return;
  }

  // Todo lo demás (HTML, /media/, endpoints de dominio): siempre red.
});

self.addEventListener("push", (event) => {
  let datos = {};
  try {
    datos = event.data ? event.data.json() : {};
  } catch (e) {
    datos = {};
  }
  event.waitUntil(
    self.registration.showNotification(datos.title || "App Gimnasios", {
      body: datos.body || "",
      icon: datos.icon,
      data: { url: datos.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/";
  event.waitUntil(clients.openWindow(url));
});
